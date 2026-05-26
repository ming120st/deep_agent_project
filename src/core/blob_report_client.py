"""
src/core/blob_report_client.py

boa-reports 컨테이너 전용 Blob 클라이언트.
리포트 HTML 업로드/삭제/SAS URL 생성을 담당합니다.

환경변수:
    AZURE_STORAGE_CONNECTION_STRING  (rag_engine.py와 동일 변수 재사용)
    BOA_CONTAINER_NAME               (기본값: wm-boa)
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from azure.storage.blob import (
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
    BlobSasPermissions,
)

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
BOA_CONTAINER_NAME = os.getenv("BOA_CONTAINER_NAME", "wm-boa")

_blob_service: Optional[BlobServiceClient] = None


def _get_blob_service() -> BlobServiceClient:
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        )
    return _blob_service


def _get_account_name() -> str:
    return _get_blob_service().account_name


def _get_account_key() -> str:
    for part in AZURE_STORAGE_CONNECTION_STRING.split(";"):
        if part.startswith("AccountKey="):
            return part[len("AccountKey="):]
    raise ValueError("AccountKey not found in AZURE_STORAGE_CONNECTION_STRING")


def _format_blob_path(blob_path: str) -> str:
    """blob_path가 'reports/'로 시작하지 않으면 자동으로 접두사를 붙여 가상 디렉터리를 형성합니다."""
    clean_path = blob_path.lstrip('/')
    if not clean_path.startswith("reports/"):
        return f"reports/{clean_path}"
    return clean_path


def upload_report(blob_path: str, content: bytes, content_type: str = "text/html; charset=utf-8") -> str:
    blob_path = _format_blob_path(blob_path)
    svc = _get_blob_service()
    blob_client = svc.get_blob_client(container=BOA_CONTAINER_NAME, blob=blob_path)
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_path


def delete_report(blob_path: str) -> None:
    blob_path = _format_blob_path(blob_path)
    svc = _get_blob_service()
    blob_client = svc.get_blob_client(container=BOA_CONTAINER_NAME, blob=blob_path)
    blob_client.delete_blob(delete_snapshots="include")


def get_report_sas_url(blob_path: str, expires_hours: int = 2) -> str:
    blob_path = _format_blob_path(blob_path)
    account_name = _get_account_name()
    account_key = _get_account_key()
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=BOA_CONTAINER_NAME,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expires_hours),
    )
    return (
        f"https://{account_name}.blob.core.windows.net/"
        f"{BOA_CONTAINER_NAME}/{blob_path}?{sas_token}"
    )
