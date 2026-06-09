import os
import json
import base64
import hashlib
import requests
import uuid
from datetime import datetime, UTC, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


# ====================================================
# 예외 클래스
# ====================================================

class CMIAuthError(Exception):
    """인증 관련 오류 (WSSE, accessToken)"""
    pass


class CMIApiError(Exception):
    """CMI API 응답 오류 (code != 0000000)"""
    def __init__(self, code: str, description: str):
        self.code = code
        self.description = description
        super().__init__(f"[{code}] {description}")


# ====================================================
# 메인 클라이언트
# ====================================================

class CMIClient:
    """
    China Mobile International Global Data SIM Platform API 클라이언트

    사용 예시:
        client = CMIClient()
        client.login(iccid="8985...", ver_code="123456")  # accessToken 발급
        quota = client.get_quota(iccid="8985...")
    """

    ENDPOINTS = {
        "get_token":       "/aep/APP_getAccessToken_SBO/v1",
        "get_bundle":      "/aep/app_getDataBundle_SBO/v1",
        "activate_bundle": "/aep/APP_activeDataBundle_SBO/v1",
        "get_location":    "/aep/APP_HIMSI_TERMSTATE_SBO/v1",
        "create_order":    "/aep/APP_createOrder_SBO/v1",
        "get_user_bundle": "/aep/APP_getSubedUserDataBundle_SBO/v1",
        "query_carrier":   "/aep/APP_queryCarrier_SBO/v1",
        "get_quota":       "/aep/APP_getSubscriberAllQuota_SBO/v1",
        "early_termination":     "/aep/SBO_package_end/v1",
        "query_sim_info":  "/aep/SBO_query_SIMInfo/v1",
        "query_trajectory":"/aep/SBO_query_usingTrajectories/v1",
        "unsubscribe":     "/aep/SBO_channel_unsubscribe/v1",
        "query_esim":      "/aep/SBO_queryEsimCardInfo/v1",
    }

    # accessToken 만료 여유 시간 (만료 30초 전에 갱신)
    TOKEN_REFRESH_MARGIN = timedelta(seconds=30)

    def __init__(self):
        self.base_url = os.getenv("CM_BASE_URL", "").rstrip("/")
        self.username = os.getenv("CM_USERNAME")  # AppKey
        self.password = os.getenv("CM_PASSWORD")  # AppSecret

        if not all([self.base_url, self.username, self.password]):
            missing = [k for k, v in {
                "CM_BASE_URL": self.base_url,
                "CM_USERNAME": self.username,
                "CM_PASSWORD": self.password,
            }.items() if not v]
            raise ValueError(f"환경변수 누락: {', '.join(missing)}")

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    # ====================================================
    # WSSE 헤더 생성
    # ====================================================

    def _build_wsse_headers(self, body_str: str) -> dict:
        """
        WSSE 인증 헤더 생성.
        PasswordDigest = Base64(SHA256(Nonce + Created + AppSecret))
        """
        nonce = uuid.uuid4().hex.upper()
        created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        raw = (nonce + created + self.password).encode("utf-8")
        digest = base64.b64encode(hashlib.sha256(raw).digest()).decode()

        return {
            "Content-Type": "application/json",
            "Content-Length": str(len(body_str.encode("utf-8"))),
            "Accept": "application/json",
            "Authorization": 'WSSE realm="SDP", profile="UsernameToken", type="Appkey"',
            "X-WSSE": (
                f'UsernameToken Username="{self.username}", '
                f'PasswordDigest="{digest}", '
                f'Nonce="{nonce}", '
                f'Created="{created}"'
            ),
        }

    # ====================================================
    # 공통 POST 요청
    # ====================================================

    def _post(self, endpoint_key: str, payload: dict) -> dict:
        """
        WSSE 헤더 포함 POST 요청 전송 후 응답 파싱.
        CMI 공통 에러 코드(code != 0000000) 시 CMIApiError raise.
        """
        url = self.base_url + self.ENDPOINTS[endpoint_key]
        body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = self._build_wsse_headers(body_str)

        try:
            resp = requests.post(url, data=body_str.encode("utf-8"), headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise CMIApiError("TIMEOUT", f"요청 타임아웃: {endpoint_key}")
        except requests.exceptions.HTTPError as e:
            raise CMIApiError(str(e.response.status_code), f"HTTP 오류: {e}")

        data = resp.json()
        code = data.get("code", "")

        if code not in ("0", "0000000"):
            description = data.get("description") or data.get("msg", "알 수 없는 오류")
            raise CMIApiError(code, description)

        return data

    # ====================================================
    # accessToken 관리
    # ====================================================

    def _is_token_valid(self) -> bool:
        """현재 accessToken이 유효한지 확인 (만료 30초 전부터 갱신)"""
        if not self._access_token or not self._token_expires_at:
            return False
        return datetime.now(UTC) < (self._token_expires_at - self.TOKEN_REFRESH_MARGIN)

    def _parse_expire_time(self, expire_str: str) -> datetime:
        """
        CMI expireTime 파싱 (YYYYMMDDHHMMSS, UTC0 기준)
        파싱 실패 시 현재로부터 9분 30초 후로 fallback.
        """
        try:
            return datetime.strptime(expire_str, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return datetime.now(UTC) + timedelta(minutes=9, seconds=30)

    def login(
        self,
        id_value: str,
        id_type: int = 106,
        ver_code: Optional[str] = None,
    ) -> str:
        """
        accessToken 발급 및 내부 캐싱.

        Args:
            id_value: ICCID / 전화번호 / 임시계정 / 채널 AppKey
            id_type:  102=ICCID, 104=전화번호, 105=임시계정, 106=채널(기본값)
            ver_code: 카드 인증 코드 (id_type=102일 때 필수)

        Returns:
            accessToken 문자열
        """
        payload: dict = {"id": id_value, "type": id_type}
        if ver_code:
            payload["verCode"] = ver_code

        data = self._post("get_token", payload)

        self._access_token = data["accessToken"]
        self._token_expires_at = self._parse_expire_time(data.get("expireTime", ""))
        return self._access_token

    def get_token(self) -> str:
        """
        유효한 accessToken 반환. 만료됐거나 없으면 CMIAuthError raise.
        login()을 먼저 호출해야 함.
        """
        if self._is_token_valid():
            return self._access_token
        raise CMIAuthError("accessToken이 없거나 만료됨. login()을 먼저 호출하세요.")

    # ====================================================
    # 3.2.2 패키지 정보 조회
    # ====================================================

    def get_data_bundle(
        self,
        data_bundle_id: Optional[str] = None,
        data_bundle_name: Optional[str] = None,
        group_id: Optional[str] = None,
        language: Optional[str] = None,
        country: Optional[str] = None,
        mcc: Optional[str] = None,
        status: Optional[int] = None,
        currency: Optional[list] = None,
        begin_index: int = 0,
        count: int = 50,
        cooperation_mode: Optional[str] = None,
        partners: Optional[str] = None,
    ) -> list:
        payload = {"accessToken": self.get_token(), "beginIndex": begin_index, "count": count}
        if data_bundle_id:    payload["dataBundleId"] = data_bundle_id
        if data_bundle_name:  payload["dataBundleName"] = data_bundle_name
        if group_id:          payload["Group_id"] = group_id
        if language:          payload["language"] = language
        if country:           payload["country"] = country
        if mcc:               payload["mcc"] = mcc
        if status is not None: payload["status"] = status
        if currency:          payload["currency"] = currency
        if cooperation_mode:  payload["cooperationMode"] = cooperation_mode
        if partners:          payload["Partners"] = partners

        return self._post("get_bundle", payload).get("dataBundles", [])

    # ====================================================
    # 3.2.3 패키지 활성화
    # ====================================================

    def activate_bundle(
        self,
        data_bundle_id: str,
        mcc: str,
        h_imsi: Optional[str] = None,
        msisdn: Optional[str] = None,
        iccid: Optional[str] = None,
    ) -> dict:
        """hImsi / msisdn / iccid 중 하나 필수"""
        if not any([h_imsi, msisdn, iccid]):
            raise ValueError("hImsi, msisdn, iccid 중 하나는 필수입니다.")

        payload = {
            "accessToken": self.get_token(),
            "dataBundleId": data_bundle_id,
            "mcc": mcc,
        }
        if h_imsi:  payload["hImsi"] = h_imsi
        if msisdn:  payload["msisdn"] = msisdn
        if iccid:   payload["iccid"] = iccid

        return self._post("activate_bundle", payload)

    # ====================================================
    # 3.2.4 H-IMSI 위치 및 상태 조회 (실시간)
    # ====================================================

    def get_himsi_location(
        self,
        imsi: Optional[str] = None,
        iccid: Optional[str] = None,
    ) -> dict:
        """imsi / iccid 중 하나 필수"""
        if not any([imsi, iccid]):
            raise ValueError("imsi, iccid 중 하나는 필수입니다.")

        payload = {"accessToken": self.get_token()}
        if imsi:  payload["imsi"] = imsi
        if iccid: payload["iccid"] = iccid

        return self._post("get_location", payload)

    # ====================================================
    # 3.2.5 주문 생성
    # ====================================================

    def create_order(
        self,
        third_order_id: str,
        include_card: int,
        is_refuel: str,
        quantity: int,
        data_bundle_id: Optional[str] = None,
        refueling_id: Optional[str] = None,
        card_form: Optional[str] = None,
        iccid: Optional[str] = None,
        address: Optional[dict] = None,
        send_lang: Optional[str] = None,
        set_active_time: Optional[str] = None,
        transaction_code: Optional[str] = None,
        currency: Optional[str] = None,  # "344"=HKD, "840"=USD, "156"=CNY
        price: Optional[str] = None,
        cmcc_number: Optional[str] = None,
        cmcc_number_province: Optional[str] = None,
    ) -> dict:
        payload = {
            "accessToken": self.get_token(),
            "thirdOrderId": third_order_id,
            "includeCard": include_card,
            "is_Refuel": is_refuel,
            "quantity": quantity,
        }
        if data_bundle_id:       payload["dataBundleId"] = data_bundle_id
        if refueling_id:         payload["refuelingId"] = refueling_id
        if card_form:            payload["cardForm"] = card_form
        if iccid:                payload["ICCID"] = iccid
        if address:              payload["address"] = address
        if send_lang:            payload["sendLang"] = send_lang
        if set_active_time:      payload["setActiveTime"] = set_active_time
        if transaction_code:     payload["transactionCode"] = transaction_code
        if cmcc_number:          payload["cmccNumber"] = cmcc_number
        if cmcc_number_province: payload["cmccNumberProvince"] = cmcc_number_province

        ext: dict = {}
        if currency: ext["currency"] = currency
        if price:    ext["price"] = price
        if ext:      payload["ext"] = ext

        return self._post("create_order", payload)

    # ====================================================
    # 3.2.6 사용자 구독 패키지 조회
    # ====================================================

    def get_subscribed_bundles(
        self,
        language: str,
        h_imsi: Optional[str] = None,
        iccid: Optional[str] = None,
        status: Optional[str] = None,
        mcc: Optional[str] = None,
        begin_index: int = 0,
        count: int = 50,
    ) -> list:
        """hImsi / iccid 중 하나 필수. language: 0=간체, 1=번체, 2=영어"""
        if not any([h_imsi, iccid]):
            raise ValueError("hImsi, iccid 중 하나는 필수입니다.")

        payload = {
            "accessToken": self.get_token(),
            "language": language,
            "beginIndex": begin_index,
            "count": count,
        }
        if h_imsi:          payload["hImsi"] = h_imsi
        if iccid:           payload["iccid"] = iccid
        if status:          payload["status"] = status
        if mcc:             payload["mcc"] = mcc

        return self._post("get_user_bundle", payload).get("userDataBundles", [])

    # ====================================================
    # 3.2.7 통신사 정보 조회
    # ====================================================

    def query_carrier(
        self,
        language: str,
        mcc: Optional[str] = None,
        continent: Optional[str] = None,
    ) -> list:
        """language: 0=간체, 1=번체, 2=영어"""
        payload = {"language": language}
        if mcc:       payload["mcc"] = mcc
        if continent: payload["continent"] = continent

        return self._post("query_carrier", payload).get("stateList", [])

    # ====================================================
    # 3.2.8 데이터 사용량 조회 (UPCC)
    # ====================================================

    def get_quota(
        self,
        himsi: Optional[str] = None,
        iccid: Optional[str] = None,
        begin_time: Optional[str] = None,  # YYYYMMDD
        end_time: Optional[str] = None,    # YYYYMMDD
        child_order_id: Optional[str] = None,
        third_order_id: Optional[str] = None,
    ) -> dict:
        """himsi / iccid 중 하나 필수"""
        if not any([himsi, iccid]):
            raise ValueError("himsi, iccid 중 하나는 필수입니다.")

        payload: dict = {}
        if himsi:          payload["himsi"] = himsi
        if iccid:          payload["iccid"] = iccid
        if begin_time:     payload["beginTime"] = begin_time
        if end_time:       payload["endTime"] = end_time
        if child_order_id: payload["childOrderId"] = child_order_id
        if third_order_id: payload["thirdOrderId"] = third_order_id

        data = self._post("get_quota", payload)
        return {
            "subscriberQuota": data.get("subscriberQuota"),
            "historyQuota": data.get("historyQuota", []),
        }

    # ====================================================
    # 3.2.9 패키지 조기 종료
    # ====================================================

    def early_termination(self, iccid_package_list: list[dict]) -> dict:
        """
        Args:
            iccid_package_list: [{"iccid": "...", "packageid": "..."}, ...]
                                 iccid / imsi 중 하나 + packageid 필수
        """
        payload = {
            "accessToken": self.get_token(),
            "iccidPackageList": iccid_package_list,
        }
        return self._post("early_termination", payload)

    # ====================================================
    # 3.2.10 SIM 카드 정보 조회
    # ====================================================

    def query_sim_info(self, sim_list: list[dict]) -> list:
        """
        Args:
            sim_list: [{"imsi": "..."}, {"iccid": "..."}]  imsi / iccid 중 하나 필수
        """
        return self._post("query_sim_info", sim_list).get("himsis", [])

    # ====================================================
    # 3.2.11 사용 궤적 조회
    # ====================================================

    def query_trajectory(
        self,
        package_id: str,
        language: str,
        imsi: Optional[str] = None,
        iccid: Optional[str] = None,
        order_id: Optional[str] = None,
        subscription_key: Optional[str] = None,
    ) -> list:
        """imsi / iccid 중 하나 필수"""
        if not any([imsi, iccid]):
            raise ValueError("imsi, iccid 중 하나는 필수입니다.")

        payload = {"packageID": package_id, "language": language}
        if imsi:             payload["imsi"] = imsi
        if iccid:            payload["iccid"] = iccid
        if order_id:         payload["orderID"] = order_id
        if subscription_key: payload["subscriptionKey"] = subscription_key

        return self._post("query_trajectory", payload).get("trajectoriesList", [])

    # ====================================================
    # 3.2.12 채널 구독 취소
    # ====================================================

    def unsubscribe(
        self,
        order_id: Optional[str] = None,
        third_order_id: Optional[str] = None,
    ) -> dict:
        """orderId / thirdOrderId 중 하나 필수. orderId 우선 적용."""
        if not any([order_id, third_order_id]):
            raise ValueError("orderId, thirdOrderId 중 하나는 필수입니다.")

        payload = {"accessToken": self.get_token()}
        if order_id:       payload["orderId"] = order_id
        if third_order_id: payload["thirdOrderId"] = third_order_id

        return self._post("unsubscribe", payload)

    # ====================================================
    # 3.2.13 eSIM 카드 정보 조회
    # ====================================================

    def query_esim_info(self, iccid: str) -> dict:
        payload = {
            "iccid": iccid,
            "accessToken": self.get_token(),
        }
        return self._post("query_esim", payload).get("cardInfo", {})


# ====================================================
# 실행 테스트
# ====================================================

if __name__ == "__main__":
    try:
        client = CMIClient()
        print("✅ CMIClient 초기화 성공")
        print(f"   base_url : {client.base_url}")
        print(f"   username : {client.username}")

        # --- accessToken 발급 테스트 (채널 타입, type=106) ---
        token = client.login(id_value=client.username, id_type=106)
        print(f"✅ accessToken 발급: {token}")

        # --- 데이터 사용량 조회 테스트 ---
        quota = client.get_quota(
            iccid="89852342022316305597",
            begin_time='20260102',
            end_time='20260228',
            )
        print("✅ 사용량 조회:", json.dumps(quota, ensure_ascii=False, indent=2))

    except ValueError as e:
        print("❌ 초기화 오류:", e)
    except CMIAuthError as e:
        print("❌ 인증 오류:", e)
    except CMIApiError as e:
        print(f"❌ API 오류 [{e.code}]:", e.description)
    except Exception as e:
        print("❌ 예상치 못한 오류:", e)

# python -m src.core.cm_client