"""
ESIM (eSIM) 내부 API 싱글톤 클라이언트
- 토큰 자동 발급 및 만료 전 갱신
- 환경변수 ESIM_API_KEY / ESIM_CLIENT_ID 자동 로드
- 유심 예약조회 지원
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Response 모델
# ──────────────────────────────────────────────

class UsimDetail(BaseModel):
    productCode: str
    productName: str
    price: int
    useDay: int
    iccid: str


class UsimReservationResult(BaseModel):
    webOrdID: str
    joinRoute: str
    omarketWebOrdID: str
    paymentPrice: int
    usePoint: int
    reciveDate: str
    picupLocation: str
    payType: str
    carrier: str
    details: list[UsimDetail]


class UsimReservationResponse(BaseModel):
    traceId: str
    timestamp: str
    resultCode: bool
    resultMessage: Optional[str]
    responseData: Optional[dict]

    def get_result_value(self) -> Optional[UsimReservationResult]:
        """responseData.resultValue 파싱"""
        try:
            rv = self.responseData["resultValue"]
            return UsimReservationResult(**rv)
        except Exception:
            return None


# ──────────────────────────────────────────────
# 싱글톤 클라이언트
# ──────────────────────────────────────────────

class WapiClient:
    """
    WideAPI 싱글톤 클라이언트.
    """

    _instance: Optional["WapiClient"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    # 토큰 만료 여유 시간 (초) — 만료 60초 전에 갱신
    TOKEN_REFRESH_BUFFER_SECONDS = 60

    def __init__(
        self,
        api_key: str,
        client_id: str,
        base_url: str = "https://wapi.widemobile.com",
        token_url: str = "https://wapi.wifidosirak.com/joinRoute/auth/token",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._client_id = client_id
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._timeout = timeout

        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._token_lock = asyncio.Lock()

        self._http = httpx.AsyncClient(timeout=self._timeout)

    # ── 싱글톤 팩토리 ──────────────────────────────

    @classmethod
    async def get_instance(
        cls,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        **kwargs,
    ) -> "WapiClient":
        """
        싱글톤 인스턴스 반환.

        api_key / client_id 를 직접 넘기지 않으면
        환경변수 ESIM_API_KEY / ESIM_CLIENT_ID 에서 자동으로 읽어옴.
        """
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    resolved_api_key = os.environ.get("ESIM_API_KEY", "")
                    resolved_client_id = os.environ.get("ESIM_CLIENT_ID", "")

                    if not resolved_api_key or not resolved_client_id:
                        raise ValueError(
                            "ESIM_API_KEY / ESIM_CLIENT_ID 환경변수가 설정되지 않았습니다."
                        )

                    cls._instance = cls(
                        api_key=resolved_api_key,
                        client_id=resolved_client_id,
                        **kwargs,
                    )
                    logger.info("WapiClient 싱글톤 초기화 완료")
        return cls._instance

    # ── 토큰 관리 ──────────────────────────────────

    def _is_token_valid(self) -> bool:
        """토큰이 유효한지 확인 (만료 버퍼 포함)"""
        if not self._token or not self._token_expires_at:
            return False
        now = datetime.now(tz=timezone.utc)
        remaining = (self._token_expires_at - now).total_seconds()
        return remaining > self.TOKEN_REFRESH_BUFFER_SECONDS

    async def _refresh_token(self) -> None:
        """토큰 발급 / 갱신"""
        async with self._token_lock:
            # double-check: 다른 코루틴이 이미 갱신했을 수 있음
            if self._is_token_valid():
                return

            logger.info("WapiClient: 토큰 발급 요청")
            resp = await self._http.post(
                self._token_url,
                headers={
                    "ApiKey": self._api_key,
                    "ClientID": self._client_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("resultCode"):
                raise RuntimeError(f"토큰 발급 실패: {data}")

            rd = data["responseData"]
            self._token = rd["token"]
            # ISO 8601 → datetime (UTC)
            self._token_expires_at = datetime.fromisoformat(
                rd["expiredTime"].replace("Z", "+00:00")
            )
            logger.info(
                "WapiClient: 토큰 갱신 완료 (만료: %s)", self._token_expires_at
            )

    async def _get_token(self) -> str:
        """유효 토큰 반환 (필요 시 자동 갱신)"""
        if not self._is_token_valid():
            await self._refresh_token()
        return self._token  # type: ignore[return-value]

    # ── 공통 요청 헬퍼 ─────────────────────────────

    async def _get(self, path: str, **kwargs) -> dict:
        token = await self._get_token()
        url = f"{self._base_url}{path}"
        
        # 기본 헤더에 ApiKey, ClientID 추가 (API 규격)
        headers = kwargs.pop("headers", {})
        headers.update({
            "Authorization": f"Bearer {token}",
            "ApiKey": self._api_key,
            "ClientID": self._client_id,
        })
        
        resp = await self._http.get(
            url,
            headers=headers,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    # ── API 메서드 ─────────────────────────────────

    async def get_usim_reservation(
        self,
        webord_id: str,
        mobile_last4: str,
    ) -> UsimReservationResponse:
        """
        유심 예약조회

        Args:
            webord_id:    주문번호 (예: "S26052000000")
            mobile_last4: 휴대폰 번호 뒤 4자리 (예: "1111")

        Returns:
            UsimReservationResponse
        """
        path = f"/api/v1/usim/reservation/inquiry/{webord_id}"
        data = await self._get(path, params={"mobileLast": mobile_last4})
        return UsimReservationResponse(**data)

    # ── 종료 ───────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()
        logger.info("WapiClient: httpx 세션 종료")

    @classmethod
    async def destroy_instance(cls) -> None:
        """싱글톤 해제 (테스트·재초기화용)"""
        async with cls._lock:
            if cls._instance:
                await cls._instance.close()
                cls._instance = None


import asyncio

async def main():
    client = await WapiClient.get_instance()

    resp = await client.get_usim_reservation(
        webord_id="S26060800122",
        mobile_last4="8504",
    )

    print("resultCode:", resp.resultCode)
    print("resultMessage:", resp.resultMessage)

    order = resp.get_result_value()
    if order:
        print(order)
        #print("webOrdID:", order.webOrdID)
        #print("carrier:", order.carrier)
        #print("paymentPrice:", order.paymentPrice)
        #print("details:")
        for d in order.details:
            print(f"  - {d.productName} / {d.useDay}일 / {d.price}원")
    else:
        print("responseData:", resp.responseData)


if __name__ == "__main__":
    asyncio.run(main())


# python -m src.core.internal_esim_client_v2