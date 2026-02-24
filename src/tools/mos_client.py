import json
import hashlib
import requests
from datetime import datetime, timezone
from typing import List, Dict
from dotenv import load_dotenv
import os

class DeviceDayUsageClient:
    def __init__(
        self,
        base_url: str,
        channel_id: str,
        channel_secret: str,
        lang: str = "en",
        timeout: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.channel_id = channel_id
        self.channel_secret = channel_secret
        self.lang = lang
        self.timeout = timeout

    # -------------------------
    # 내부 유틸
    # -------------------------
    def _now_timestamp(self) -> str:
        return str(int(datetime.now(timezone.utc).timestamp()))

    def _make_payload_and_sign(self, body: dict) -> tuple[str, str]:
        # sort_keys=True: 키 순서를 고정하여 서명 일관성 보장
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        raw = body_str + self.channel_secret
        sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
        return body_str, sign

    def _headers(self, sign: str) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "x-channel-id": self.channel_id,
            "x-sign-method": "md5",
            "x-sign-value": sign,
            "x-sign-lang": self.lang,
        }

    # -------------------------
    # 날짜 관련
    # -------------------------
    @staticmethod
    def _month_range(start_yyyymmdd: str, end_yyyymmdd: str) -> List[str]:
        """조회에 필요한 YYYY-MM 목록"""
        start = datetime.strptime(start_yyyymmdd, "%Y%m%d")
        end = datetime.strptime(end_yyyymmdd, "%Y%m%d")

        months = set()
        cur = start.replace(day=1)

        while cur <= end:
            months.add(cur.strftime("%Y-%m"))
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)

        return sorted(months)

    @staticmethod
    def _filter_detail_by_range(
        detail: Dict[str, str],
        start_yyyymmdd: str,
        end_yyyymmdd: str,
    ) -> Dict[str, float]:
        result = {}
        for day, value in detail.items():
            if start_yyyymmdd <= day <= end_yyyymmdd:
                result[day] = float(value)
        return result

    # -------------------------
    # API 호출
    # -------------------------
    def _fetch_month_data(self, imei: str, month: str, goods_id: int = None) -> dict:
        url = f"{self.base_url}/coope/device/dayUsed"

        body = {
            "timestamp": self._now_timestamp(),
            "imei": imei,
            "month": month,
        }
        if goods_id is not None:
            body["goods_id"] = goods_id

        # 수정: 서명에 사용된 문자열을 그대로 전송해야 함
        body_str, sign = self._make_payload_and_sign(body)
        headers = self._headers(sign)

        resp = requests.post(
            url,
            headers=headers,
            data=body_str.encode("utf-8"),  # json=body 대신 data=body_str 사용
            timeout=self.timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        if data.get("tradeCode") != 1000:
            raise RuntimeError(f"API Error: {data}")

        return data["tradeData"]["list"]

    # -------------------------
    # 외부 공개 메서드
    # -------------------------
    def get_usage_by_date_range(
        self,
        imeis: List[str],
        start_yyyymmdd: str, #"yyyymmdd"
        end_yyyymmdd: str, #"yyyymmdd"
        goods_id: int = None,
    ) -> str:
        """
        반환 형태:
        (String)
        [일자별 데이터 사용량 조회 결과]
        
        기기번호(ICCID/IMEI): 866837042039311
          - 2025-11-30: 0.0 MB
          - 2025-12-01: 0.5 MB
        ...
        """

        months = self._month_range(start_yyyymmdd, end_yyyymmdd)
        result: Dict[str, Dict[str, float]] = {imei: {} for imei in imeis}

        for imei in imeis:
            for month in months:
                items = self._fetch_month_data(imei, month, goods_id=goods_id)

                for item in items:
                    detail = item.get("detail", {})
                    filtered = self._filter_detail_by_range(
                        detail, start_yyyymmdd, end_yyyymmdd
                    )
                    result[imei].update(filtered)

        # 결과 포맷팅
        lines = ["[일자별 데이터 사용량 조회 결과]"]
        for imei, daily_usage in result.items():
            lines.append(f"\n기기번호(ICCID/IMEI): {imei}")
            if not daily_usage:
                lines.append("  (조회된 데이터가 없습니다.)")
                continue

            sorted_dates = sorted(daily_usage.keys())
            for date_str in sorted_dates:
                # 20251130 -> 2025-11-30 변환
                formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                usage_mb = daily_usage[date_str]
                lines.append(f"  - {formatted_date}: {usage_mb} MB")

        return "\n".join(lines)

load_dotenv()
mos_client = DeviceDayUsageClient(
    base_url=os.getenv("MOS_BASE_URL"),
    channel_id=os.getenv("MOS_CHANNEL_ID"),
    channel_secret=os.getenv("MOS_CHANNEL_SECRET"),
)


# -----------------------
# 테스트 코드
# -----------------------

if __name__ == "__main__":
    # 간단 테스트
    client = DeviceDayUsageClient(
        base_url="https://mcrmgr.umacaroon.com/",
        channel_id="widemobilewifi",
        channel_secret="EAy716D6w2ZmjsqDgTBQL9yfFgDgzoLR",
    )

    # 문서에 있는 테스트용 IMEI (이건 무조건 되어야 함)
    imeis = ["866837042039311"]
    
    # 문서에 데이터가 있는 날짜 범위 (2024년 3월)
    start_date = "20251130"
    end_date = "20251205"

    print(f"Testing IMEI: {imeis} from {start_date} to {end_date}")
    try:
        usage_data = client.get_usage_by_date_range(imeis, start_date, end_date)
        print(json.dumps(usage_data, indent=2))
    except Exception as e:
        print(f"Error occurred: {e}")