"""
RSP Business Integration API Client — Async Singleton
Spec: RSP_业务集成接口规范 v1.3.3

인증 방식: MD5(AppId + TransId + Timestamp + AppSecret)
요청 헤더: AppId / TransId / Timestamp / Ciphertext
"""

import hashlib
import logging
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 예외 정의
# ---------------------------------------------------------------------------

class RSPError(Exception):
    """RSP API 공통 예외"""
    def __init__(self, code: str, mesg: str, raw: dict | None = None):
        super().__init__(f"[{code}] {mesg}")
        self.code = code
        self.mesg = mesg
        self.raw = raw or {}


class RSPBusinessError(RSPError):
    """비즈니스 처리 실패 (code != '000')"""


# ---------------------------------------------------------------------------
# 에러 코드 참고
# ---------------------------------------------------------------------------
# 000  성공
# 300  처리 중
# 401  AppId 무효
# 403  Ciphertext 검증 실패
# 406  API 권한 없음
# 407  IP 화이트리스트 미등록
# 500  파라미터 오류
# 501  TransId 중복
# 502  Timestamp 유효 범위 초과 (±10분)
# 503  거래 없음
# 600  비즈니스 처리 실패
# 601  카드 없음          602  쿠폰 없음
# 603  재고 없음          604  주문 없음
# 605  주문 미활성화      606  쿠폰 사용됨
# 607  카드 권한 없음     608  상품 없음
# 609  상품 권한 없음     610  채널 없음
# 611  쿠폰 무효          612  미지원 기능
# 999  시스템 오류


# ---------------------------------------------------------------------------
# Async Singleton Client
# ---------------------------------------------------------------------------

class RSPClient:
    """
    RSP API Async 싱글톤 클라이언트.

    ■ FastAPI lifespan에서 초기화 (권장)
    ─────────────────────────────────────
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        RSPClient.get_instance(
            base_url=settings.RSP_BASE_URL,
            app_id=settings.RSP_APP_ID,
            app_secret=settings.RSP_APP_SECRET,
            notify_base_url=settings.RSP_NOTIFY_BASE_URL,  # 선택
        )
        yield
        await RSPClient.get_instance().close()

    app = FastAPI(lifespan=lifespan)

    ■ LangGraph node에서 사용
    ─────────────────────────────────────
    async def some_node(state):
        client = RSPClient.get_instance()
        data = await client.query_esim_status(cid="89851100000000000001")
        ...
    """

    _instance: "RSPClient | None" = None

    def __init__(
        self,
        base_url: str,
        app_id: str,
        app_secret: str,
        notify_base_url: str = "",
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._notify_base_url = notify_base_url.rstrip("/")
        self._app_id = app_id
        self._app_secret = app_secret
        # AsyncClient는 이벤트 루프 바인딩 — app startup 시점에 생성해야 함
        self._http = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # 싱글톤 진입점
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(
        cls,
        base_url: str = "",
        app_id: str = "",
        app_secret: str = "",
        notify_base_url: str = "",
        timeout: float = 30.0,
    ) -> "RSPClient":
        """
        싱글톤 인스턴스 반환.
        최초 호출 시 파라미터 or 환경변수 필수.

        환경변수:
            RSP_BASE_URL          RSP 서버 기본 URL
            RSP_APP_ID            RSP가 발급한 AppId
            RSP_APP_SECRET        RSP가 발급한 AppSecret
            RSP_NOTIFY_BASE_URL   콜백 수신 서버 URL (선택)
        """
        if cls._instance is None:
            if not all([base_url, app_id, app_secret]):
                import os
                base_url        = base_url        or os.environ["RSP_BASE_URL"]
                app_id          = app_id          or os.environ["RSP_APP_ID"]
                app_secret      = app_secret      or os.environ["RSP_APP_SECRET"]
                notify_base_url = notify_base_url or os.environ.get("RSP_NOTIFY_BASE_URL", "")
            cls._instance = cls(base_url, app_id, app_secret, notify_base_url, timeout)
            logger.info("RSPClient initialized (base_url=%s)", base_url)
        return cls._instance

    @classmethod
    async def reset_instance(cls) -> None:
        """테스트 전용 — 싱글톤 초기화"""
        if cls._instance:
            await cls._instance._http.aclose()
        cls._instance = None

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _make_trans_id(self) -> str:
        return uuid.uuid4().hex  # 32자, 매 요청 유니크 보장

    def _make_headers(self, trans_id: str) -> dict[str, str]:
        timestamp  = str(int(time.time() * 1000))
        raw        = f"{self._app_id}{trans_id}{timestamp}{self._app_secret}"
        ciphertext = hashlib.md5(raw.encode()).hexdigest()
        return {
            "AppId":        self._app_id,
            "TransId":      trans_id,
            "Timestamp":    timestamp,
            "Ciphertext":   ciphertext,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict) -> dict:
        trans_id = self._make_trans_id()
        url      = f"{self._base_url}{path}"
        headers  = self._make_headers(trans_id)

        logger.debug("RSP POST %s | trans_id=%s | body=%s", path, trans_id, body)
        resp = await self._http.post(url, json=body, headers=headers)
        resp.raise_for_status()
        result: dict = resp.json()

        code = result.get("code", "")
        mesg = result.get("mesg", "")
        if code != "000":
            logger.warning("RSP error %s — %s | path=%s", code, mesg, path)
            raise RSPBusinessError(code, mesg, raw=result)

        return result.get("data") or {}

    # ------------------------------------------------------------------
    # 2.1 공통 인터페이스
    # ------------------------------------------------------------------

    async def get_transaction_status(self, q_trans_id: str) -> dict:
        """2.1.1 거래 상태 조회 (최근 3개월 이내)"""
        return await self._post("/openapi/getTransactionStatus", {"qTransId": q_trans_id})

    async def get_plan_list(
        self,
        plan_type: str | None = None,   # "1"=eSIM, "2"=OTA
        page_no: int = 1,
        page_size: int = 100,
    ) -> dict:
        """2.1.2 상품 목록 조회"""
        body: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if plan_type:
            body["planType"] = plan_type
        return await self._post("/openapi/sim/plan/query", body)

    async def get_subscription_list(
        self,
        cid: str,
        coupon: str | None = None,
        at_code: str | None = None,
    ) -> dict:
        """2.1.3 카드 서비스 주문 목록 조회"""
        body: dict[str, Any] = {"cid": cid}
        if coupon:
            body["coupon"] = coupon
        if at_code:
            body["atCode"] = at_code
        return await self._post("/openapi/sim/subscription/query", body)

    async def get_subscription_detail(
        self,
        cid: str,
        order_id: str,
        coupon: str | None = None,
        at_code: str | None = None,
    ) -> dict:
        """2.1.4 카드 서비스 주문 상세 조회"""
        body: dict[str, Any] = {"cid": cid, "orderId": order_id}
        if coupon:
            body["coupon"] = coupon
        if at_code:
            body["atCode"] = at_code
        return await self._post("/openapi/sim/subscription/profile/query", body)

    # ------------------------------------------------------------------
    # 2.2 eSIM 인터페이스
    # ------------------------------------------------------------------

    async def query_coupon(self, coupons: list[str]) -> list[dict]:
        """2.2.1 쿠폰 정보 조회 (최대 20개)"""
        if len(coupons) > 20:
            raise ValueError("쿠폰은 한 번에 최대 20개까지 조회 가능합니다.")
        data = await self._post("/openapi/coupon/query", {"coupons": ",".join(coupons)})
        return data if isinstance(data, list) else [data]

    async def change_coupon(self, coupons: list[str], action: int) -> None:
        """2.2.2 쿠폰 무효화/복원 (action: 1=무효화, 2=복원) — 결과는 콜백(2.2.3)"""
        await self._post("/openapi/coupon/change", {
            "coupons": ",".join(coupons),
            "action":  action,
        })

    async def redeem_coupon(
        self,
        coupon: str,
        qrcode_type: int = 0,           # 0=이미지URL, 1=텍스트, 2=둘다
        channel_id: str | None = None,
    ) -> None:
        """2.2.4 쿠폰 교환 — 결과는 콜백(2.2.5)"""
        body: dict[str, Any] = {"coupon": coupon, "qrcodeType": qrcode_type}
        if channel_id:
            body["channelId"] = channel_id
        await self._post("/openapi/coupon/redeem", body)

    async def generate_esim(
        self,
        iccid: str,
        imsi: str,
        plan_code: str,
        *,
        msisdn: str | None = None,
        pin1: str | None = None,
        pin2: str | None = None,
        puk1: str | None = None,
        puk2: str | None = None,
        ki: str | None = None,
        opc: str | None = None,
    ) -> None:
        """2.2.6 eSIM 생성 — 결과는 콜백(2.2.7)"""
        body: dict[str, Any] = {"iccid": iccid, "imsi": imsi, "planCode": plan_code}
        for k, v in [("msisdn", msisdn), ("pin1", pin1), ("pin2", pin2),
                     ("puk1", puk1), ("puk2", puk2), ("ki", ki), ("opc", opc)]:
            if v is not None:
                body[k] = v
        await self._post("/openapi/esim/generate", body)

    async def subscribe_esim_plan(
        self,
        plan_code: str,
        cid: str | None = None,
        qrcode_type: int = 0,
        email: str | None = None,
    ) -> None:
        """2.2.8 eSIM 상품 주문 — 결과는 콜백(2.2.9)"""
        body: dict[str, Any] = {"planCode": plan_code, "qrcodeType": qrcode_type}
        if cid:
            body["cid"] = cid
        if email:
            body["email"] = email
        await self._post("/openapi/esim/plan/subscribe", body)

    async def query_esim_profile(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
    ) -> dict:
        """2.2.11 eSIM Profile 정보 조회"""
        return await self._post("/openapi/esim/profile/query", _esim_id_body(coupon, cid, at_code))

    async def cancel_esim(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
    ) -> None:
        """2.2.12 eSIM 취소 (RELEASED 상태만) — 결과는 콜백(2.2.13)"""
        await self._post("/openapi/esim/cancel", _esim_id_body(coupon, cid, at_code))

    async def query_esim_usage(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
        order_id: str | None = None,
    ) -> dict:
        """2.2.14 eSIM 사용량 조회"""
        body = _esim_id_body(coupon, cid, at_code)
        if order_id:
            body["orderId"] = order_id
        return await self._post("/openapi/esim/usage/query", body)

    async def query_esim_status(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
    ) -> dict:
        """2.2.15 eSIM 상태 조회"""
        return await self._post("/openapi/esim/status/query", _esim_id_body(coupon, cid, at_code))

    async def generate_coupon(
        self,
        template_id: str,
        quantity: int,
        email: str,
        type_: str = "0",               # "0"=자사만, "1"=하위조직 포함
    ) -> dict:
        """2.2.16 쿠폰 생성 (1~2000개) — 결과는 이메일. 반환: {couponOrderId}"""
        if not (1 <= quantity <= 2000):
            raise ValueError("quantity는 1~2000 범위여야 합니다.")
        return await self._post("/openapi/coupon/generate", {
            "templateId": template_id,
            "quantity":   quantity,
            "type":       type_,
            "email":      email,
        })

    async def query_esim_realtime_usage(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
    ) -> dict:
        """2.2.17 eSIM 실시간 트래픽 조회"""
        return await self._post("/openapi/esim/usage/realtime", _esim_id_body(coupon, cid, at_code))

    async def terminate_esim(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
    ) -> None:
        """2.2.19 eSIM 강제 종료 (비-RELEASED 상태만) — 결과는 콜백(2.2.20)"""
        await self._post("/openapi/esim/terminate", _esim_id_body(coupon, cid, at_code))

    async def reset_esim_subscription(
        self,
        coupon: str | None = None,
        cid: str | None = None,
        at_code: str | None = None,
        order_id: str | None = None,
    ) -> None:
        """2.2.21 eSIM 주문 트래픽 초기화 — 결과는 콜백(2.2.22)"""
        body = _esim_id_body(coupon, cid, at_code)
        if order_id:
            body["orderId"] = order_id
        await self._post("/openapi/esim/subscription/reset", body)

    async def query_coupon_generate(self, coupon_order_id: str) -> dict:
        """2.2.25 쿠폰 생성 주문 조회"""
        return await self._post("/openapi/coupon/generate/query", {"couponOrderId": coupon_order_id})

    # ------------------------------------------------------------------
    # 2.3 OTA 인터페이스
    # ------------------------------------------------------------------

    async def subscribe_ota_plan(self, cid: str, plan_code: str) -> dict:
        """2.3.1 OTA 상품 주문 — 결과는 콜백(2.3.2). 반환: {orderId}"""
        return await self._post("/openapi/sim/plan/subscribe", {"cid": cid, "planCode": plan_code})

    async def subscribe_ota_plan_with_channel(
        self,
        cid: str,
        plan_code: str,
        channel_id: str | None = None,
    ) -> dict:
        """2.3.3 OTA 상품 주문(채널 지정) — 반환: {orderId}"""
        body: dict[str, Any] = {"cid": cid, "planCode": plan_code}
        if channel_id:
            body["channelId"] = channel_id
        return await self._post("/openapi/sim/plan/subscribe1", body)

    async def cancel_ota_subscription(self, cid: str, order_id: str) -> None:
        """2.3.4 OTA 주문 취소 (미활성화만) — 결과는 콜백(2.3.5)"""
        await self._post("/openapi/sim/subscription/cancel", {"cid": cid, "orderId": order_id})

    async def activate_ota_subscription(self, cid: str, order_id: str, mcc: str) -> None:
        """2.3.6 OTA 주문 원격 활성화 — 결과는 콜백(2.3.7)"""
        await self._post("/openapi/sim/subscription/activate", {
            "cid": cid, "orderId": order_id, "mcc": mcc,
        })

    async def terminate_ota_subscription(self, cid: str, order_id: str) -> None:
        """2.3.8 OTA 주문 강제 종료 — 결과는 콜백(2.3.9)"""
        await self._post("/openapi/sim/subscription/terminate", {"cid": cid, "orderId": order_id})

    async def query_ota_usage(
        self,
        cid: str,
        order_id: str,
        imsi: str | None = None,
    ) -> dict:
        """2.3.10 OTA 사용량 조회"""
        body: dict[str, Any] = {"cid": cid, "orderId": order_id}
        if imsi:
            body["imsi"] = imsi
        return await self._post("/openapi/sim/usage/query", body)

    async def query_sim_status(self, cid: str) -> dict:
        """2.3.13 카드 상태 조회"""
        return await self._post("/openapi/sim/status/query", {"cid": cid})

    async def reset_ota_subscription(self, cid: str, order_id: str) -> None:
        """2.3.14 OTA 주문 트래픽 초기화 — 결과는 콜백(2.3.15)"""
        await self._post("/openapi/sim/subscription/reset", {"cid": cid, "orderId": order_id})

    async def query_ota_realtime_usage(
        self,
        cid: str,
        order_id: str,
        imsi: str | None = None,
    ) -> dict:
        """2.3.17 OTA 실시간 트래픽 조회"""
        body: dict[str, Any] = {"cid": cid, "orderId": order_id}
        if imsi:
            body["imsi"] = imsi
        return await self._post("/openapi/sim/usage/realtime", body)

    async def recover_sim(self, cid: str) -> None:
        """2.3.18 카드 만료 복구 — 결과는 콜백(2.3.19)"""
        await self._post("/openapi/sim/recover", {"cid": cid})

    async def change_service_order(self, cid: str, order_id: str, plan_code: str) -> None:
        """2.3.20 서비스 주문 변경 — 결과는 콜백(2.3.21)"""
        await self._post("/openapi/sim/order/service/change", {
            "cid": cid, "orderId": order_id, "planCode": plan_code,
        })

    # ------------------------------------------------------------------
    # 생명주기
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _esim_id_body(
    coupon: str | None,
    cid: str | None,
    at_code: str | None,
) -> dict[str, Any]:
    """eSIM 식별자 3종 중 최소 하나 필수"""
    if not any([coupon, cid, at_code]):
        raise ValueError("coupon / cid / at_code 중 하나 이상 필요합니다.")
    body: dict[str, Any] = {}
    if coupon:
        body["coupon"] = coupon
    if cid:
        body["cid"] = cid
    if at_code:
        body["atCode"] = at_code
    return body
