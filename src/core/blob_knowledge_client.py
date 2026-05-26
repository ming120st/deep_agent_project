"""
src/core/blob_knowledge_client.py

지식베이스 원본 파일(엑셀, Markdown, 텍스트 등)을 Blob Storage에 업로드하기 위한 클라이언트입니다.
기본 컨테이너: BOA_CONTAINER_NAME 환경변수(기본 wm-boa)
디렉터리: knowledge_sources/
"""

import os
from typing import Optional
import mimetypes

from azure.storage.blob import BlobServiceClient, ContentSettings

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


def upload_knowledge_source(filename: str, content: bytes) -> str:
    """
    업로드된 원본 파일을 knowledge_sources 가상 디렉터리 내부에 시간/파일명 형식으로 저장합니다.
    """
    clean_name = filename.lstrip('/')
    # 타임스탬프를 넣어 유니크하게 만들 수도 있지만, 일단 파일명 위주로. (필요 시 uuid/변경 가능)
    blob_path = f"knowledge_sources/{clean_name}"

    # mime type 추정
    content_type, _ = mimetypes.guess_type(filename)
    if not content_type:
        content_type = "application/octet-stream"

    svc = _get_blob_service()
    blob_client = svc.get_blob_client(container=BOA_CONTAINER_NAME, blob=blob_path)
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_path
