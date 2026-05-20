"""
src/core/blob_report_client.py

boa-reports 컨테이너 전용 Blob 클라이언트.
리포트 HTML 업로드/삭제/SAS URL 생성을 담당합니다.

환경변수:
    AZURE_STORAGE_CONNECTION_STRING  (rag_engine.py와 동일 변수 재사용)
    REPORTS_CONTAINER_NAME           (기본값: boa-reports)
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
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "boa-reports")

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


def upload_report(blob_path: str, content: bytes, content_type: str = "text/html; charset=utf-8") -> str:
    svc = _get_blob_service()
    blob_client = svc.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_path)
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_path


def delete_report(blob_path: str) -> None:
    svc = _get_blob_service()
    blob_client = svc.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_path)
    blob_client.delete_blob(delete_snapshots="include")


def get_report_sas_url(blob_path: str, expires_hours: int = 2) -> str:
    account_name = _get_account_name()
    account_key = _get_account_key()
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=REPORTS_CONTAINER_NAME,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expires_hours),
    )
    return (
        f"https://{account_name}.blob.core.windows.net/"
        f"{REPORTS_CONTAINER_NAME}/{blob_path}?{sas_token}"
    )
