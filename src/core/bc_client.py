import os
import json
import hashlib
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class bcApiClient:
    def __init__(self, key_type: str):
        self.api_url = "https://apiint-flow.billionconnect.com/Flow/saler/2.0/invoke"

        key_type = key_type.upper()

        if key_type == "CMI":
            self.app_key = os.getenv("BC_CMI_KEY")
            self.app_secret = os.getenv("BC_CMI_SECRET")
        elif key_type == "IRC":
            self.app_key = os.getenv("BC_IRC_KEY")
            self.app_secret = os.getenv("BC_IRC_SECRET")
        else:
            raise ValueError("지원하지 않는 key_type")

        if not self.app_key or not self.app_secret:
            raise ValueError("KEY/SECRET 없음")

    # ======================
    # 공통 처리
    # ======================
    def _serialize(self, body):
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    def _sign(self, body_str):
        return hashlib.md5((self.app_secret + body_str).encode("utf-8")).hexdigest()

    def _headers(self, sign):
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "x-channel-id": self.app_key,
            "x-sign-method": "md5",
            "x-sign-value": sign
        }

    def _post(self, body):
        body_str = self._serialize(body)
        sign = self._sign(body_str)
        headers = self._headers(sign)

        res = requests.post(
            self.api_url,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=10
        )

        if res.status_code != 200:
            raise Exception(f"HTTP 오류: {res.status_code} / {res.text}")

        data = res.json()

        if data.get("tradeCode") != "1000":
            raise Exception(f"API 오류: {data.get('tradeMsg')}")

        return data

    # ======================
    # F012 (orderId 조회)
    # ======================
    def get_order(self, iccid):
        body = {
            "tradeType": "F012",
            "tradeTime": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "tradeData": {
                "iccid": iccid,
                "language": "2"
            }
        }

        return self._post(body)

    # ======================
    # F046 (사용량 조회)
    # ======================
    def get_usage(self, iccid, order_id):
        body = {
            "tradeType": "F046",
            "tradeTime": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "tradeData": {
                "iccid": iccid,
                "orderId": order_id,
                "language": "2"
            }
        }

        return self._post(body)

    # ======================
    # 결과 포맷
    # ======================
    def format_usage(self, data: dict, start_date: str, end_date: str) -> str:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        usage_list = []
        total_mb = 0

        sub_orders = data.get("tradeData", {}).get("subOrderList", [])

        if not sub_orders:
            return "사용량 데이터 없음"

        for sub in sub_orders:
            usage_info = sub.get("usageInfoList")

            # usageInfoList 없으면 skip
            if not usage_info:
                continue

            for u in usage_info:
                use_date = u.get("usedDate")
                usage_amt = u.get("usageAmt")

                # 키 없으면 skip
                if not use_date or not usage_amt:
                    continue

                try:
                    d = datetime.strptime(use_date, "%Y%m%d")
                except:
                    continue

                if start <= d <= end:
                    mb = int(usage_amt) / 1024
                    total_mb += mb
                    usage_list.append((d.strftime("%Y-%m-%d"), int(mb)))

        # 데이터 없을 때
        if not usage_list:
            return "조회 기간 내 사용량 없음"

        usage_list.sort()

        result = []
        for idx, (date, mb) in enumerate(usage_list, 1):
            result.append(f"{idx}. {date}: {mb} MB")

        result.append(f"\n- 합계: {round(total_mb / 1024, 2)} GB")

        return "\n".join(result)

    # ======================
    # 통합 함수 (핵심)
    # ======================
    def get_usage_by_iccid(self, iccid, start_date, end_date):

        # 1. F012 → orderId
        order_data = self.get_order(iccid)

        trade_data = order_data.get("tradeData", [])

        if not trade_data:
            raise Exception("주문 정보 없음")

        order_id = trade_data[0]["orderId"]

        # 2. F046 → usage
        usage_data = self.get_usage(iccid, order_id)

        # 3. 포맷
        return self.format_usage(usage_data, start_date, end_date)


# ======================
# 실행 테스트
# ======================
if __name__ == "__main__":
    try:
        client = bcApiClient(key_type="irc")

        iccid = "22107796343"
        start_date = "2026-04-04"
        end_date = "2026-04-06"

        result = client.get_usage_by_iccid(iccid, start_date, end_date)

        print(result)

    except Exception as e:
        print("에러 발생:", str(e))