"""인메모리 저장소, Fake Blob, Fake 디바이스 클라이언트를 사용한 IntranetPublisher 단위 테스트 (네트워크 및 Mongo 미사용)."""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import integrations.intranet_publish as pkg
from integrations.intranet_publish import IntranetPublisher, PublishError, PublishSettings
from integrations.intranet_publish.blob import AzureImageBlobStore, BlobConfigError
from integrations.intranet_publish.service import describe
from integrations.intranet_publish.store import MemoryPublishStore
from integrations.intranet_publish.validation import extract_reason, make_job_id


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
NAMES = ["홍길동", "김철수"]


def run(coro):
    return asyncio.run(coro)


SAMPLE_BODY = (
    '<div style="font-family:sans-serif;font-size:14px">'
    '<div><img src="{{img:header.png}}" alt="header" style="max-width:100%"></div>'
    '<div>본문</div>'
    '<div><img src="{{img:issue1.png}}" alt="issue" style="max-width:100%"></div>'
    '<div><img src="{{img:chart-keywords.png}}" alt="chart" style="max-width:100%"></div>'
    "</div>"
)
SAMPLE_IMAGES = ["header.png", "issue1.png", "chart-keywords.png"]


def sample(board="genai", **changes):
    doc = {
        "post_key": "ai-weekly-2026-W42",
        "board": board,
        "title": "[AI 위클리] 10월 3주차 (샘플)",
        "body_html": SAMPLE_BODY,
        "images": list(SAMPLE_IMAGES),
    }
    doc.update(changes)
    return doc


def assets_for(doc):
    return {n: (JPG if n.endswith((".jpg", ".jpeg")) else PNG) for n in doc["images"]}


class FakeBlob:
    """files = Blob에 존재하는 파일; uploads = publisher가 직접 업로드한 파일(비어 있어야 함)."""

    def __init__(self):
        self.uploads = {}
        self.files = {}
        self.minted = 0

    async def upload(self, path, data, content_type):
        self.uploads[path] = (data, content_type)
        self.files[path] = data

    async def head(self, path, length=16):
        data = self.files.get(path)
        return None if data is None else (len(data), bytes(data[:length]) if isinstance(data, (bytes, bytearray)) else b"")

    def read_url(self, path, ttl):
        self.minted += 1
        return f"https://acct.blob.core.windows.net/wm-device-agent-ai/{path}?sig=SECRETSIG{self.minted}"


class FakeDevice:
    def __init__(self, errors=None):
        self.requests = []
        self.errors = list(errors or [])

    async def submit(self, request):
        self.requests.append(request.model_dump())
        if self.errors:
            raise self.errors.pop(0)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


async def no_sleep(_seconds):
    return None


def status_error(code):
    request = httpx.Request("POST", "https://device.example/device-job")
    return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))


@pytest.fixture()
def env():
    class Env:
        pass

    e = Env()
    e.store = MemoryPublishStore()
    e.blob = FakeBlob()
    e.device = FakeDevice()
    e.clock = Clock()
    e.notified = []

    async def notifier(record):
        e.notified.append(record)

    e.publisher = IntranetPublisher(
        store=e.store, blob=e.blob, device=e.device, settings=PublishSettings(), notifier=notifier,
        sleep=no_sleep, clock=e.clock,
    )
    return e


def publish(env, doc=None, **kw):
    doc = doc or sample()
    assets = kw.pop("assets", None)
    if assets is None:
        assets = assets_for(doc)
    for name, data in assets.items():  # 이미지 업로드 도구가 Blob에 저장했을 데이터 시뮬레이션
        env.blob.files[f"intranet-board/{doc['post_key']}/{name}"] = data
    kw.setdefault("thread_id", "user@widemobile.com")
    return run(env.publisher.publish_post(doc, **kw))


def callback(job_id, status="error", reason=None, result=None, error_message=None):
    res = dict(result or {})
    if reason:
        res.setdefault("success", status == "success")
        res["reason"] = reason
    return {"job_id": job_id, "status": status, "result": res, "error_message": error_message or (reason if status == "error" else None)}


def _callback_and_drain(env, cb):
    """재시도는 백그라운드 태스크이므로 콜백 처리와 대기가 동일한 이벤트 루프를 공유해야 합니다."""

    async def go():
        response = await env.publisher.handle_callback(cb)
        await env.publisher.drain()
        return response

    return run(go())


# ------------------------------------------------------------------ 요청 형태 검증
def test_publish_checks_images_in_blob_and_sends_the_agreed_request(env):
    result = publish(env)
    doc = sample()
    assert result.status == "pending"
    assert result.job_id == "intranet-genai-ai-weekly-2026-W42"
    assert env.blob.uploads == {}  # publisher는 직접 업로드하지 않음 (이미지 업로드 도구가 수행)

    (req,) = env.device.requests
    assert req["job_id"] == req["parameters"]["job_id"] == result.job_id
    assert req["task"] == "post_intranet_board"
    assert req["thread_id"] == "user@widemobile.com"
    params = req["parameters"]
    assert params["board"] == "genai" and params["mode"] == "create"
    assert "board_url" not in params and "recipients" not in params
    assert "{{img:header.png}}" in params["body_html"]
    assert all(i["blob_url"].startswith("https://acct.blob.core.windows.net/wm-device-agent-ai/intranet-board/ai-weekly-2026-W42/") for i in params["images"])
    assert {i["filename"] for i in params["images"]} == set(doc["images"])

    record = run(env.store.get(result.job_id))
    assert record["status"] == "pending" and record["attempt"] == 1
    assert "SECRETSIG" not in json.dumps(record, default=str)  # SAS 토큰은 절대 저장하지 않음
    assert all("data" not in i for i in record["images"])  # 원본 bytes 데이터는 저장하지 않음


def test_work_board_sends_normalised_recipients(env):
    result = publish(env, sample("work"), recipients=["  홍길동 ", "김 철수", "홍길동", "김  철수"])
    params = env.device.requests[0]["parameters"]
    assert params["board"] == "work"
    assert params["recipients"] == ["홍길동", "김 철수"]
    assert result.recipient_count == 2


def test_post_without_images(env):
    doc = sample("work")
    doc["images"] = []
    doc["body_html"] = "<div>이미지 없는 글</div>"
    publish(env, doc, assets={})
    assert env.device.requests[0]["parameters"]["images"] == []
    assert env.blob.uploads == {}


def test_job_id_fits_the_device_agent_rule():
    job_id = make_job_id("work", "a" * 80)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id)


# ------------------------------------------------------------------ 입력 유효성 검사 (업로드 및 전송 없음)
def _rejected(env, code, *args, **kw):
    with pytest.raises(PublishError) as info:
        publish(env, *args, **kw)
    assert info.value.code == code
    assert env.blob.uploads == {} and env.device.requests == []
    return info.value


def test_genai_refuses_recipients(env):
    _rejected(env, "INVALID_RECIPIENTS", sample("genai"), recipients=["홍길동"])


def test_unknown_board(env):
    _rejected(env, "INVALID_BOARD", sample("ai-weekly"))


@pytest.mark.parametrize(
    "recipients",
    [
        "홍길동",
        [f"사람{i}" for i in range(21)],
        ["가" * 31],
        ["   "],
        ["홍길동;"],
        ["<b>"],
        ['a"b'],
        ["a'b"],
        ["a\x00b"],
        [123],
    ],
)
def test_recipient_rules(env, recipients):
    error = _rejected(env, "INVALID_RECIPIENTS", sample("work"), recipients=recipients)
    for name in recipients if isinstance(recipients, list) else []:
        if isinstance(name, str) and name.strip():
            assert name not in error.message


def test_missing_asset(env):
    doc = sample()
    assets = assets_for(doc)
    assets.pop("header.png")
    error = _rejected(env, "MISSING_ASSET", doc, assets=assets)
    assert error.details == {"filenames": ["header.png"]}


@pytest.mark.parametrize("data", [JPG, b"", b"GIF89a....", "not bytes", PNG[:4]])
def test_image_signature_must_match_extension(env, data):
    doc = sample()
    assets = assets_for(doc)
    assets["header.png"] = data
    _rejected(env, "INVALID_IMAGE", doc, assets=assets)


def test_image_too_large(env):
    doc = sample()
    assets = assets_for(doc)
    assets["header.png"] = PNG + b"\x00" * (10 * 1024 * 1024)
    _rejected(env, "INVALID_IMAGE", doc, assets=assets)


def test_invalid_document(env):
    _rejected(env, "INVALID_DOCUMENT", {"title": "x"}, assets={})


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "<style>td{color:red}</style>",
        '<div onclick="x()">a</div>',
        '<a href="javascript:alert(1)">a</a>',
        "<object data=x></object>",
        "<form></form>",
    ],
)
def test_unsafe_body_is_never_sent(env, bad):
    doc = sample()
    doc["body_html"] += bad
    _rejected(env, "UNSAFE_BODY", doc)


@pytest.mark.parametrize(
    "body",
    [
        '<img src="https://evil.example/x.png">',
        '<img src="data:image/png;base64,AAAA">',
        '<img alt="x">',
        '<div>{{img:unknown}}</div>',
        "   ",
    ],
)
def test_body_image_rules(env, body):
    doc = sample()
    doc["body_html"] = body
    doc["images"] = []
    _rejected(env, "INVALID_DOCUMENT", doc, assets={})


def test_undeclared_placeholder_is_rejected(env):
    doc = sample()
    doc["body_html"] += '<img src="{{img:other.png}}">'
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_body_size_limit(env):
    doc = sample()
    doc["body_html"] = "<div>" + "가" * 300_000 + "</div>"
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_unused_image_is_uploaded_with_a_warning(env):
    doc = sample()
    doc["body_html"] = '<div><img src="{{img:header.png}}" alt="h"></div>'
    result = publish(env, doc)
    assert any("issue1.png" in w for w in result.warnings)
    assert len(env.device.requests[0]["parameters"]["images"]) == 3


def test_duplicate_image_files_are_rejected(env):
    doc = sample()
    doc["images"].append("header.png")
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_unknown_fields_are_rejected(env):
    doc = sample()
    doc["theme"] = "default"
    _rejected(env, "INVALID_DOCUMENT", doc)


# ------------------------------------------------------------------ 전송 오류 처리
def test_connection_errors_are_retried_then_fail(env):
    env.device.errors = [httpx.ConnectError("down")] * 3
    with pytest.raises(PublishError):
        publish(env)
    assert len(env.device.requests) == 3
    record = run(env.store.get("intranet-genai-ai-weekly-2026-W42"))
    assert record["status"] == "failed" and record["reason"] == "DEVICE_REQUEST_FAILED"
    assert len(env.notified) == 1


def test_connection_error_then_success(env):
    env.device.errors = [httpx.ConnectTimeout("slow")]
    assert publish(env).status == "pending"
    assert len(env.device.requests) == 2


def test_rejected_request_fails_without_retry(env):
    env.device.errors = [status_error(401)]
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "DEVICE_REQUEST_REJECTED"
    assert len(env.device.requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), status_error(500)])
def test_ambiguous_submit_waits_for_the_callback(env, error):
    env.device.errors = [error]
    assert publish(env).status == "pending"
    assert len(env.device.requests) == 1  # 재전송하지 않음: device_agent가 이미 작업을 실행 중일 수 있음


# ------------------------------------------------------------------ 콜백 처리
def test_success_callback_is_recorded_once(env):
    job_id = publish(env).job_id
    cb = callback(job_id, "success", result={"success": True, "post_id": "526400", "post_url": "https://intra/x?Num=526400", "secret": "drop"})
    assert run(env.publisher.handle_callback(cb))["status"] == "succeeded"
    assert run(env.publisher.handle_callback(cb))["status"] == "duplicate"
    record = run(env.store.get(job_id))
    assert record["status"] == "succeeded" and record["result"]["post_id"] == "526400"
    assert "secret" not in record["result"]
    assert len(env.notified) == 1
    assert "526400" in describe(record)


@pytest.mark.parametrize("reason", ["LOGIN_REQUIRED", "IMAGE_UPLOAD_FAILED", "SUBMIT_FAILED"])
def test_retryable_failures_are_resent_with_fresh_sas_then_give_up(env, reason):
    job_id = publish(env).job_id
    first_urls = [i["blob_url"] for i in env.device.requests[0]["parameters"]["images"]]

    assert _callback_and_drain(env, callback(job_id, reason=reason))["status"] == "retry_wait"
    assert len(env.device.requests) == 2
    second = env.device.requests[1]
    assert second["job_id"] == job_id  # 동일한 job_id 유지: 원장의 일관성 보장
    assert [i["blob_url"] for i in second["parameters"]["images"]] != first_urls
    assert run(env.store.get(job_id))["attempt"] == 2

    _callback_and_drain(env, callback(job_id, reason=reason))
    assert len(env.device.requests) == 3
    result = run(env.publisher.handle_callback(callback(job_id, reason=reason)))
    assert result["status"] == "failed"
    assert len(env.device.requests) == 3
    assert len(env.notified) == 1


@pytest.mark.parametrize(
    "reason",
    ["BODY_REJECTED", "PLACEHOLDER_UNRESOLVED", "EDITOR_NOT_FOUND", "INVALID_PARAMS", "RECIPIENT_NOT_FOUND", "RECIPIENT_AMBIGUOUS", "RECIPIENT_FAILED"],
)
def test_final_failures_are_not_retried(env, reason):
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    result = _callback_and_drain(env, callback(job_id, reason=reason))
    assert result["status"] == "failed"
    assert len(env.device.requests) == 1


def test_recipient_errors_are_passed_by_index_only(env, caplog):
    caplog.set_level(logging.DEBUG)
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    errors = [{"index": 1, "reason": "AMBIGUOUS"}]
    run(env.publisher.handle_callback(callback(job_id, reason="RECIPIENT_AMBIGUOUS", result={"recipient_errors": errors})))
    record = run(env.store.get(job_id))
    assert record["result"]["recipient_errors"] == errors
    text = describe(record)
    assert "1번(AMBIGUOUS)" in text
    for name in NAMES:
        assert name not in text and name not in caplog.text


@pytest.mark.parametrize(
    "cb",
    [
        lambda j: callback(j, reason="SUBMIT_UNCERTAIN"),
        lambda j: callback(j, reason="VERIFY_FAILED"),
        lambda j: callback(j, reason="SOMETHING_NEW"),
        lambda j: {"job_id": j, "status": "error", "result": {}, "error_message": "Traceback: boom"},
    ],
)
def test_uncertain_or_unknown_needs_a_person(env, cb):
    job_id = publish(env).job_id
    result = _callback_and_drain(env, cb(job_id))
    assert result["status"] == "needs_check"
    assert len(env.device.requests) == 1
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "NEEDS_MANUAL_CHECK"


def test_callback_timeout_then_late_success(env):
    job_id = publish(env).job_id
    env.clock.now += timedelta(minutes=11)
    assert run(env.publisher.expire_stale()) == 1
    record = run(env.store.get(job_id))
    assert (record["status"], record["reason"]) == ("needs_check", "CALLBACK_TIMEOUT")
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True, "post_id": "1"})))
    assert run(env.store.get(job_id))["status"] == "succeeded"


def test_callback_timeout_then_late_pre_click_failure(env):
    job_id = publish(env).job_id
    env.clock.now += timedelta(minutes=11)
    run(env.publisher.handle_callback(callback(job_id, reason="LOGIN_REQUIRED")))
    record = run(env.store.get(job_id))
    assert (record["status"], record["reason"]) == ("failed", "LOGIN_REQUIRED")
    assert len(env.device.requests) == 1  # 타임아웃 이후에는 자동 재시도하지 않음


def test_not_timed_out_yet(env):
    publish(env)
    env.clock.now += timedelta(minutes=9)
    assert run(env.publisher.expire_stale()) == 0


# ------------------------------------------------------------------ 동일 게시글 재게시 처리
def test_republish_after_success_does_not_send(env):
    job_id = publish(env).job_id
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True})))
    assert publish(env).status == "succeeded"
    assert len(env.device.requests) == 1


def test_republish_while_in_progress_is_refused(env):
    publish(env)
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "IN_PROGRESS"


def test_republish_after_input_failure_is_allowed(env):
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    run(env.publisher.handle_callback(callback(job_id, reason="RECIPIENT_NOT_FOUND")))
    publish(env, sample("work"), recipients=["홍길동"])
    assert len(env.device.requests) == 2
    assert run(env.store.get(job_id))["attempt"] == 1


# ------------------------------------------------------------------ 로그 내 민감 정보 누출 검증
def test_no_secrets_or_names_in_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    _callback_and_drain(env, callback(job_id, reason="LOGIN_REQUIRED"))
    assert len(env.device.requests) == 2
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True, "post_id": "9"})))
    assert "SECRETSIG" not in caplog.text
    for name in NAMES:
        assert name not in caplog.text
    assert job_id in caplog.text


# ------------------------------------------------------------------ 기타 기능 검증
def test_extract_reason():
    assert extract_reason({"result": {"reason": "LOGIN_REQUIRED"}}) == "LOGIN_REQUIRED"
    assert extract_reason({"result": {}, "error_message": "VERIFY_FAILED"}) == "VERIFY_FAILED"
    assert extract_reason({"result": {}, "error_message": "알 수 없는 job_type: x"}) == "UNKNOWN"
    assert extract_reason({}) == "UNKNOWN"


def test_try_handle_callback_ignores_other_jobs(monkeypatch):
    def boom():
        raise AssertionError("must not build the publisher for other jobs")

    monkeypatch.setattr(pkg, "get_publisher", boom)
    assert run(pkg.try_handle_callback({"job_id": "3f0c-uuid", "status": "success"})) is None


def test_try_handle_callback_routes_owned_jobs(env, monkeypatch):
    job_id = publish(env).job_id
    monkeypatch.setattr(pkg, "get_publisher", lambda: env.publisher)
    assert run(pkg.try_handle_callback({"job_id": "intranet-genai-unknown", "status": "success"})) is None
    response = run(pkg.try_handle_callback({"job_id": job_id, "status": "success", "result": {"success": True}}))
    assert response["status"] == "succeeded"


# ------------------------------------------------------------------ Azure SAS (오프라인: SAS는 로컬에서 연산됨)
KEY = base64.b64encode(b"k" * 64).decode()
CONN = f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={KEY};EndpointSuffix=core.windows.net"


def test_sas_is_read_only_https_and_short_lived():
    store = AzureImageBlobStore(CONN, "wm-device-agent-ai")
    assert store.host == "acct.blob.core.windows.net"
    url = store.read_url("intranet-board/ai-weekly-2026-W42/header.png", timedelta(hours=1))
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.hostname == "acct.blob.core.windows.net"
    assert parts.path == "/wm-device-agent-ai/intranet-board/ai-weekly-2026-W42/header.png"
    query = parse_qs(parts.query)
    assert query["sp"] == ["r"] and query["spr"] == ["https"]
    expiry = datetime.strptime(query["se"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(minutes=55) < expiry - datetime.now(timezone.utc) <= timedelta(hours=1)


def test_blob_config_errors_do_not_echo_the_connection_string():
    with pytest.raises(BlobConfigError):
        AzureImageBlobStore("", "c")
    sas_only = "BlobEndpoint=https://acct.blob.core.windows.net/;SharedAccessSignature=sv=2024&sig=TOPSECRET"
    with pytest.raises(BlobConfigError) as info:
        AzureImageBlobStore(sas_only, "c")
    assert "TOPSECRET" not in str(info.value)
