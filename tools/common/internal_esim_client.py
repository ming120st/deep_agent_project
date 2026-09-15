import base64
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import os
from dotenv import load_dotenv

load_dotenv()  # .env 파일 로드

class esimApiClient:
    def __init__(self):
        # AES256 암호화를 위한 키와 IV 설정 (C#에서 추출한 고정된 키와 IV 값)
        self.key = bytes([
            220, 166, 28, 230, 161, 196, 166, 41, 
            77, 136, 152, 135, 51, 66, 143, 93, 
            155, 136, 118, 221, 221, 208, 84, 80, 
            21, 228, 39, 178, 153, 87, 50, 85
        ])
        
        self.iv = bytes([
            77, 136, 152, 135, 51, 66, 143, 93, 
            78, 68, 60, 93, 199, 125, 188, 9
        ])
        
        # api 인증 정보 설정
        self.api_key = os.getenv("ESIM_API_KEY")
        self.client_id = os.getenv("ESIM_CLIENT_ID")
        self.route_id = os.getenv("ESIM_ROUTE_ID")
        
        #base64(btoa) 변환
    def btoa(self, data):
        encoded_bytes = base64.b64encode(data.encode("utf-8"))
        return encoded_bytes.decode("utf-8")
        
        # eSIM 데이터 암호화 함수. 마지막 base64 변환까지 해서 반환하기 때문에 바로 api_url에 사용하면 된다.
    def encrypt(self, data):
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv))
        encryptor = cipher.encryptor()
        # 데이터에 UTF-16 인코딩 및 PKCS7 패딩 추가
        data_bytes = data.encode("utf-16")[2:]  # UTF-16 BOM 제거
        padding_len = 16 - (len(data_bytes) % 16)
        padded_data = data_bytes + bytes([padding_len] * padding_len)
        # 암호화 수행
        pre_encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        # 암호화된 데이터를 Base64 인코딩하여 변수에 담고
        encrypted_data = base64.b64encode(pre_encrypted_data).decode()
        # 한번 더 base64(btoa)까지 거쳐야 API요청 보낼 수 있는 텍스트로 변환됨
        return self.btoa(encrypted_data)
    
        # API 호출 함수. 응답값 return하기 때문에 가공은 function에서 해야한다.
    async def call_esim_api_get(self, api_url):
        headers = {
            "ApiKey": self.api_key,
            "ClientID": self.client_id,
            "Content-Type": "application/json",
            "x-routeId": self.route_id
        }
        timeout = httpx.Timeout(20.0, connect=5.0, read=10.0, write=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()  # HTTP 오류 발생 시 예외 처리
            return response.json()  # JSON 형식의 응답 반환


    async def call_esim_api_post(self, api_url, json=None):
        headers = {
            "ApiKey": self.api_key,
            "ClientID": self.client_id,
            "Content-Type": "application/json",
            "x-routeId": self.route_id
        }
        timeout = httpx.Timeout(20.0, connect=5.0, read=10.0, write=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(api_url, json=json, headers=headers)  # POST 요청
            response.raise_for_status()  # HTTP 오류 발생 시 예외를 호출자에게 전달
            return response.json()  # JSON 형식의 응답 반환
        

# 앱 시작 시 클라이언트 하나 생성해놓고 쓴다.
esim_client = esimApiClient()