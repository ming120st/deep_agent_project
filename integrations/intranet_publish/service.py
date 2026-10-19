"""AI가 작성한 HTML -> 유효성 검증 -> Blob 이미지 -> Device Job -> 콜백 -> 결과 저장.

인트라넷에 게시하는 모든 BOA 기능은 ``IntranetPublisher.publish_post``를 거칩니다.
HTML 작성 에이전트는 ``agents/common/intranet_html_agent/skills/intranet-html/SKILL.md``를 따르며,
본 모듈은 작성된 내용만 검증하고(``post.py`` 참고) 내용을 직접 다시 작성하지는 않습니다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

import httpx
from pydantic import ValidationError

from integrations.device_job.schemas import DeviceJobRequest

from .blob import BlobConfigError, BlobUploadError, ImageBlobStore
from .post import IntranetPost, ValidationError, parse_post
from .safety import is_safe_html
from .config import FINAL_REASONS, JOB_ID_PREFIX, MANUAL_CHECK_REASONS, RETRYABLE_REASONS, PublishSettings
from .store import PublishStore
from .validation import PublishError, check_board, check_image_meta, extract_reason, make_job_id, normalize_recipients

logger = logging.getLogger(__name__)

PENDING, RETRY_WAIT, SUCCEEDED, FAILED, NEEDS_CHECK = "pending", "retry_wait", "succeeded", "failed", "needs_check"
TERMINAL = (SUCCEEDED, FAILED, NEEDS_CHECK)
CALLBACK_TIMEOUT = "CALLBACK_TIMEOUT"
RETRY_INTERRUPTED = "RETRY_INTERRUPTED"
DEVICE_REQUEST_REJECTED = "DEVICE_REQUEST_REJECTED"

# device_agent 콜백에서 유지할 결과 키 목록 (그 외는 제거됨)
_RESULT_KEYS = ("post_id", "post_url", "image_urls", "recipient_count", "recipient_errors", "deduplicated", "warnings", "reason")


class DeviceSubmitter(Protocol):
    async def submit(self, request: DeviceJobRequest) -> None: ...


Notifier = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class PublishResult:
    job_id: str
    status: str  # pending | succeeded (이전에 이미 게시됨)
    board: str
    post_key: str
    title: str
    image_count: int
    recipient_count: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # MongoDB는 타임존 정보 없는 naive UTC를 반환함


class IntranetPublisher:
    def __init__(
        self,
        *,
        store: PublishStore,
        blob: ImageBlobStore | Callable[[], ImageBlobStore],
        device: DeviceSubmitter,
        settings: PublishSettings | None = None,
        notifier: Notifier | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.store = store
        self._blob = blob
        self.device = device
        self.settings = settings or PublishSettings()
        self.notifier = notifier
        self._sleep = sleep
        self._clock = clock
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 헬퍼
    @property
    def blob(self) -> ImageBlobStore:
        if callable(self._blob) and not hasattr(self._blob, "upload"):
            self._blob = self._blob()  # 지연 로딩: 재시도가 필요 없는 콜백 처리는 Blob 설정이 필요하지 않음
        return self._blob  # type: ignore[return-value]

    @staticmethod
    def owns_job_id(job_id: str) -> bool:
        return isinstance(job_id, str) and job_id.startswith(JOB_ID_PREFIX)

    async def owns_job(self, job_id: str) -> bool:
        return self.owns_job_id(job_id) and await self.store.get(job_id) is not None

    async def get_status(self, job_id: str) -> dict[str, Any] | None:
        await self.expire_stale()
        record = await self.store.get(job_id)
        if record is None:
            return None
        return {k: record.get(k) for k in ("job_id", "status", "reason", "attempt", "board", "post_key", "title", "result")}

    async def drain(self) -> None:
        """예약된 재시도 작업들이 완료될 때까지 대기합니다(테스트 및 스크립트용)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ 게시
    async def publish_post(
        self,
        post: IntranetPost | dict[str, Any],
        *,
        recipients: list[str] | None = None,
        thread_id: str,
        context: dict[str, Any] | None = None,
        notify: dict[str, str] | None = None,
    ) -> PublishResult:
        """HTML 게시글을 검증하고, Blob의 이미지를 확인한 뒤 device job을 전송합니다.
        작업이 접수되면 즉시 반환되며, 최종 결과는 디바이스 콜백(``handle_callback`` 참고)을 통해 수신됩니다.

        ``notify`` = {"space_id": ..., "thread_name": ...}는 처리 결과를 Google Chat으로 전송합니다.
        입력값이 올바르지 않은 경우 전송하기 전에 ``PublishError``를 발생시킵니다.
        """
        rendered = self._parse(post)
        board = check_board(rendered.board, self.settings.boards)
        names = normalize_recipients(recipients, board)
        if not is_safe_html(rendered.body_html):
            raise PublishError("UNSAFE_BODY", "본문에 허용되지 않는 HTML이 있습니다.")

        job_id = make_job_id(board.name, rendered.post_key)
        await self.expire_stale()
        previous = await self.store.get(job_id)
        if previous is not None:
            if previous["status"] == SUCCEEDED:
                logger.info("[IntranetPublish] %s already posted; not sending again", job_id)
                return self._result(previous, rendered, SUCCEEDED)
            if previous["status"] in (PENDING, RETRY_WAIT):
                raise PublishError("IN_PROGRESS", "같은 글(post_key)을 이미 게시 중입니다.", {"job_id": job_id})
            if previous["status"] == NEEDS_CHECK:
                raise PublishError(
                    "NEEDS_MANUAL_CHECK",
                    "이전 시도가 실제로 게시됐을 수 있습니다. 게시판을 확인한 뒤 새 post_key로 다시 요청하세요.",
                    {"job_id": job_id, "reason": previous.get("reason")},
                )
            # FAILED: 게시판에 실제로 게시되지 않았으므로 동일한 job_id를 다시 사용할 수 있습니다.

        uploads = await self._check_images(rendered)

        now = self._clock()
        record = {
            "job_id": job_id,
            "thread_id": thread_id,
            "post_key": rendered.post_key,
            "board": board.name,
            "title": rendered.title,
            "body_html": rendered.body_html,
            "images": uploads,
            "recipients": names,  # 재시도 시 재전송에 필요하며 로그에는 기록되지 않음
            "context": context or {},
            "notify": notify or {},
            "warnings": list(rendered.warnings),
            "status": PENDING,
            "attempt": 1,
            "reason": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
        }
        await self.store.save(record)
        logger.info(
            "[IntranetPublish] send %s board=%s images=%d recipients=%d body_chars=%d",
            job_id, board.name, len(uploads), len(names), len(rendered.body_html),
        )
        await self._submit(record)
        return self._result(record, rendered, PENDING)

    @staticmethod
    def _parse(post: IntranetPost | dict[str, Any]) -> IntranetPost:
        try:
            return parse_post(post)
        except ValidationError as exc:
            errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()[:20]]
            raise PublishError("INVALID_DOCUMENT", "게시 입력이 규격에 맞지 않습니다.", errors) from None

    async def _check_images(self, post: IntranetPost) -> list[dict[str, Any]]:
        """선언된 모든 이미지는 반드시 Blob의 intranet-board/{post_key}/{filename} 경로에 이미 존재해야 합니다
        (이미지 업로드 도구를 통해 업로드됨). 해당 폴더 외부의 이미지는 참조할 수 없습니다."""
        found: list[dict[str, Any]] = []
        missing: list[str] = []
        for name in post.images:
            path = post.blob_path(name)
            try:
                info = await self.blob.head(path)
            except (BlobUploadError, BlobConfigError) as exc:
                raise PublishError("BLOB_FAILED", str(exc)) from None
            if info is None:
                missing.append(name)
                continue
            size, head = info
            content_type = check_image_meta(name, size, head)
            found.append({"filename": name, "blob_path": path, "content_type": content_type})
        if missing:
            raise PublishError(
                "MISSING_ASSET",
                f"Blob에 이미지가 없습니다: {', '.join(missing)}. 먼저 이미지 업로드 Tool로 올리세요.",
                {"filenames": missing},
            )
        return found

    @staticmethod
    def _result(record: dict[str, Any], rendered: IntranetPost, status: str) -> PublishResult:
        return PublishResult(
            job_id=record["job_id"],
            status=status,
            board=record["board"],
            post_key=record["post_key"],
            title=record["title"],
            image_count=len(record.get("images") or []),
            recipient_count=len(record.get("recipients") or []),
            warnings=list(rendered.warnings),
        )

    # ------------------------------------------------------------------ 전송
    def _build_request(self, record: dict[str, Any]) -> DeviceJobRequest:
        parameters: dict[str, Any] = {
            "job_id": record["job_id"],  # 디스패처가 최상위 job_id를 핸들러에 직접 전달하지 않음
            "board": record["board"],
            "title": record["title"],
            "body_html": record["body_html"],
            "images": [
                {"key": i["filename"], "filename": i["filename"], "blob_url": self.blob.read_url(i["blob_path"], self.settings.sas_ttl)}
                for i in record.get("images") or []
            ],
            "mode": "create",
        }
        if record.get("recipients"):
            parameters["recipients"] = list(record["recipients"])
        return DeviceJobRequest(
            job_id=record["job_id"],
            thread_id=record["thread_id"],
            task=self.settings.task_name,
            parameters=parameters,
            context=dict(record.get("context") or {}),
        )

    async def _submit(self, record: dict[str, Any]) -> None:
        """POST /device-job을 호출합니다. 요청이 확실히 전달되지 않은 경우에만 재시도합니다.

        - 연결 실패 / 503: 재시도 후 FAILED 처리 (실행된 작업 없음)
        - 그 외 4xx: FAILED 처리 (요청 거절됨, 실행된 작업 없음)
        - 연결 후 타임아웃, 그 외 5xx: PENDING 상태 유지. device_agent가 이미 작업을 진행 중일 수 있으므로
          콜백 수신 또는 만료 타임아웃으로 최종 결정합니다.
        """
        job_id = record["job_id"]
        try:
            request = self._build_request(record)
        except Exception as exc:
            await self._finish(job_id, [PENDING], FAILED, "BLOB_FAILED", None)
            raise PublishError("BLOB_FAILED", f"이미지 읽기 URL 발급 실패 ({type(exc).__name__})") from None

        attempts = max(1, self.settings.submit_attempts)
        for attempt in range(1, attempts + 1):
            try:
                await self.device.submit(request)
                return
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                problem = type(exc).__name__
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 503:
                    problem = "HTTP 503"
                elif 400 <= code < 500:
                    await self._finish(job_id, [PENDING], FAILED, DEVICE_REQUEST_REJECTED, None)
                    raise PublishError(DEVICE_REQUEST_REJECTED, f"device_agent가 요청을 거부했습니다 (HTTP {code}).") from None
                else:
                    logger.warning("[IntranetPublish] %s submit got HTTP %s; waiting for callback", job_id, code)
                    return
            except httpx.HTTPError as exc:
                logger.warning("[IntranetPublish] %s submit outcome unknown (%s); waiting for callback", job_id, type(exc).__name__)
                return
            except RuntimeError as exc:  # DEVICE_JOB_URL 누락 또는 클라이언트 미초기화
                await self._finish(job_id, [PENDING], FAILED, "DEVICE_REQUEST_FAILED", None)
                raise PublishError("DEVICE_REQUEST_FAILED", str(exc)) from None
            logger.warning("[IntranetPublish] %s submit attempt %d/%d failed (%s)", job_id, attempt, attempts, problem)
            if attempt < attempts:
                await self._sleep(self.settings.submit_backoff_seconds * (2 ** (attempt - 1)))
        await self._finish(job_id, [PENDING], FAILED, "DEVICE_REQUEST_FAILED", None)
        raise PublishError("DEVICE_REQUEST_FAILED", "device_agent에 연결하지 못했습니다.")

    # ------------------------------------------------------------------ 콜백
    async def handle_callback(self, callback: dict[str, Any]) -> dict[str, Any]:
        """``owns_job``에 해당하는 작업에 대해 /device-callback에서 호출됩니다. 멱등성을 보장합니다."""
        await self.expire_stale()
        job_id = callback.get("job_id", "")
        status = callback.get("status")
        result = callback.get("result") or {}
        kept = {k: result[k] for k in _RESULT_KEYS if k in result}

        if status == "success":
            # 타임아웃 이후에 뒤늦게 도착한 성공 결과도 기록함
            record = await self._finish(job_id, [PENDING], SUCCEEDED, None, kept) or await self._finish(
                job_id, [NEEDS_CHECK], SUCCEEDED, None, kept, only_reason=CALLBACK_TIMEOUT
            )
            if record is None:
                return {"status": "duplicate", "job_id": job_id}
            logger.info("[IntranetPublish] %s posted: post_id=%s", job_id, kept.get("post_id"))
            return {"status": SUCCEEDED, "job_id": job_id}

        reason = extract_reason(callback)
        current = await self.store.get(job_id)
        if current is None:
            return {"status": "duplicate", "job_id": job_id}
        if current.get("status") == NEEDS_CHECK and current.get("reason") == CALLBACK_TIMEOUT:
            # 대기를 포기한 뒤 뒤늦게 도착한 실패: 기록만 하고 자동 재시도는 하지 않음
            final = FAILED if reason in FINAL_REASONS or reason in RETRYABLE_REASONS else NEEDS_CHECK
            record = await self._finish(job_id, [NEEDS_CHECK], final, reason, kept, only_reason=CALLBACK_TIMEOUT)
            return {"status": final if record else "duplicate", "job_id": job_id, "reason": reason}
        if current.get("status") != PENDING:
            return {"status": "duplicate", "job_id": job_id}

        retries_done = current.get("attempt", 1) - 1
        if reason in RETRYABLE_REASONS and retries_done < len(self.settings.retry_delays_seconds):
            record = await self.store.transition(
                job_id, [PENDING], {"status": RETRY_WAIT, "reason": reason, "result": kept, "updated_at": self._clock()}
            )
            if record is None:
                return {"status": "duplicate", "job_id": job_id}
            delay = self.settings.retry_delays_seconds[retries_done]
            logger.warning("[IntranetPublish] %s failed with %s; retry in %ss", job_id, reason, delay)
            task = asyncio.create_task(self._retry_later(job_id, delay))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return {"status": RETRY_WAIT, "job_id": job_id, "reason": reason}

        if reason in FINAL_REASONS or reason in RETRYABLE_REASONS:
            final = FAILED  # 입력 문제이거나 재시도 횟수를 모두 소진함: 게시판에 실제로 게시되지 않음
        else:
            final = NEEDS_CHECK  # 수동 확인 사유(MANUAL_CHECK_REASONS) 및 알 수 없는 사유: 담당자 확인 필요
        record = await self._finish(job_id, [PENDING], final, reason, kept)
        if record is None:
            return {"status": "duplicate", "job_id": job_id}
        logger.warning("[IntranetPublish] %s %s: %s", job_id, final, reason)
        return {"status": final, "job_id": job_id, "reason": reason}

    async def _retry_later(self, job_id: str, delay: float) -> None:
        try:
            await self._sleep(delay)
            current = await self.store.get(job_id)
            if current is None:
                return
            record = await self.store.transition(
                job_id,
                [RETRY_WAIT],
                {"status": PENDING, "attempt": current.get("attempt", 1) + 1, "updated_at": self._clock()},
            )
            if record is not None:
                await self._submit(record)  # 여기서 새로 갱신된 SAS URL이 생성됨
        except PublishError as exc:
            logger.error("[IntranetPublish] %s retry failed: %s", job_id, exc.code)
        except Exception as exc:
            logger.error("[IntranetPublish] %s retry crashed (%s)", job_id, type(exc).__name__)
            await self._finish(job_id, [PENDING, RETRY_WAIT], FAILED, RETRY_INTERRUPTED, None)

    async def _finish(
        self,
        job_id: str,
        from_statuses: list[str],
        status: str,
        reason: str | None,
        result: dict[str, Any] | None,
        *,
        only_reason: str | None = None,
    ) -> dict[str, Any] | None:
        update: dict[str, Any] = {"status": status, "reason": reason, "updated_at": self._clock()}
        if result is not None:
            update["result"] = result
        record = await self.store.transition(job_id, from_statuses, update, reason=only_reason)
        if record is not None and status in TERMINAL:
            await self._notify(record)
        return record

    async def _notify(self, record: dict[str, Any]) -> None:
        if self.notifier is None:
            return
        try:
            await self.notifier(record)
        except Exception as exc:
            logger.warning("[IntranetPublish] notify failed for %s (%s)", record.get("job_id"), type(exc).__name__)

    # ------------------------------------------------------------------ 타임아웃
    async def expire_stale(self) -> int:
        """콜백 대기 시간을 초과한 PENDING -> NEEDS_CHECK (이미 게시되었을 가능성 있음).
        재개되지 못한 RETRY_WAIT (프로세스 재시작 등) -> FAILED (게시된 내용 없음)."""
        before = self._clock() - self.settings.callback_timeout
        changed = 0
        for record in await self.store.stale([PENDING, RETRY_WAIT], before):
            if _as_utc(record["updated_at"]) >= before:
                continue
            if record["status"] == PENDING:
                done = await self._finish(record["job_id"], [PENDING], NEEDS_CHECK, CALLBACK_TIMEOUT, None)
            else:
                done = await self._finish(record["job_id"], [RETRY_WAIT], FAILED, RETRY_INTERRUPTED, None)
            changed += done is not None
        return changed


def describe(record: dict[str, Any]) -> str:
    """채팅 알림용 짧은 한국어 요약문 생성 (수신자 이름 및 보안 URL 미포함)."""
    title = record.get("title", "")
    status = record.get("status")
    result = record.get("result") or {}
    if status == SUCCEEDED:
        line = f"인트라넷 게시 완료: {title}"
        if result.get("post_url"):
            line += f"\n{result['post_url']}"
        if result.get("deduplicated"):
            line += "\n(이미 게시된 글이라 다시 올리지 않았습니다)"
        return line
    reason = record.get("reason") or "UNKNOWN"
    if status == NEEDS_CHECK:
        return f"인트라넷 게시 확인 필요({reason}): {title}\n게시판에 글이 올라갔는지 직접 확인해 주세요. 자동으로 다시 올리지 않습니다."
    line = f"인트라넷 게시 실패({reason}): {title}"
    errors = result.get("recipient_errors") or []
    if errors:
        line += "\n수신자 확인 필요: " + ", ".join(f"{e.get('index')}번({e.get('reason')})" for e in errors)
    return line
