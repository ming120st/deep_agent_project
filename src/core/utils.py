import base64
import os
import json
import pytz
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# AES-256 비밀키 (32 bytes). 환경변수 SESSION_LOG_SECRET_KEY가 없으면 기본키 사용.
_SECRET_KEY_STR = os.getenv("SESSION_LOG_SECRET_KEY")
_KEY = _SECRET_KEY_STR.encode("utf-8").ljust(32, b'\0')[:32]
_aesgcm = AESGCM(_KEY)

def encrypt_text(text) -> str:
    """문자열을 AES-256-GCM으로 암호화하여 base64 문자열로 반환."""
    if not text:
        return text
    try:
        # text가 str이 아닌 list나 dict 형식이 들어올 경우를 대비해 JSON 직렬화
        if not isinstance(text, str):
            text_str = json.dumps(text, ensure_ascii=False)
        else:
            text_str = text
            
        nonce = os.urandom(12)
        ciphertext = _aesgcm.encrypt(nonce, text_str.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("utf-8")
    except Exception as e:
        print(f"[Crypto] encrypt error: {e}")
        return text


def decrypt_text(encrypted_text: str) -> str:
    """base64 암호화 문자열을 AES-256-GCM으로 복호화. 실패 시(기존 평문 데이터 등) 원본 반환."""
    if not encrypted_text:
        return encrypted_text
    try:
        data = base64.b64decode(encrypted_text.encode("utf-8"))
        if len(data) < 28:  # 12-byte nonce + 16-byte tag minimum
            return encrypted_text
        nonce = data[:12]
        ciphertext = data[12:]
        decrypted_bytes = _aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted_bytes.decode("utf-8")
    except Exception:
        # 복호화 실패 시 기존 저장된 평문이거나 암호화되지 않은 텍스트로 판단하여 원본 반환
        return encrypted_text


def today():
    korea = pytz.timezone('Asia/Seoul') 
    now = datetime.now(korea)
    return now.strftime("%Y%m%d")

def yesterday():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    one_day = timedelta(days=1)
    yesterday = now - one_day
    return yesterday.strftime('%Y%m%d')

def currTime():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    formatted_now = now.strftime("%Y.%m.%d %H:%M:%S")
    return formatted_now