"""Uploads post images to Azure Blob and mints short-lived read-only SAS URLs for the device_agent.

The connection string and SAS URLs never appear in logs or exception messages raised from here.
"""
from __future__ import annotations
from azure.storage.blob import BlobServiceClient

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Protocol


class BlobConfigError(RuntimeError):
    pass


class BlobUploadError(RuntimeError):
    pass


class ImageBlobStore(Protocol):
    async def upload(self, path: str, data: bytes, content_type: str) -> None: ...

    async def head(self, path: str, length: int = 16) -> "tuple[int, bytes] | None":
        """(size, first ``length`` bytes) of the blob, or None if it does not exist."""
        ...

    def read_url(self, path: str, ttl: timedelta) -> str: ...


class AzureImageBlobStore:
    def __init__(self, connection_string: str, container: str) -> None:
        if not connection_string:
            raise BlobConfigError("AZURE_BLOB_CONNECTION_STRING이 설정되지 않았습니다.")
        
        try:
            self._service = BlobServiceClient.from_connection_string(connection_string)
        except Exception as exc:  # the message could echo the string
            raise BlobConfigError(f"Blob 연결문자열을 해석할 수 없습니다 ({type(exc).__name__}).") from None
        credential = self._service.credential
        self._account_name = getattr(credential, "account_name", None)
        self._account_key = getattr(credential, "account_key", None)
        if not self._account_name or not self._account_key:
            raise BlobConfigError("SAS 발급에는 AccountKey가 포함된 연결문자열이 필요합니다.")
        self._container = container
        self._container_client = self._service.get_container_client(container)

    @property
    def host(self) -> str:
        """For the device_agent's INTRANET_BOARD_BLOB_HOSTS allowlist."""
        return self._service.primary_hostname

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._upload_sync, path, data, content_type)

    def _upload_sync(self, path: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        try:
            self._container_client.upload_blob(
                name=path,
                data=data,
                overwrite=True,  # retries and re-renders replace the same file
                content_settings=ContentSettings(content_type=content_type),
            )
        except Exception as exc:
            raise BlobUploadError(f"Blob 업로드 실패: {path} ({type(exc).__name__})") from None

    async def head(self, path: str, length: int = 16) -> tuple[int, bytes] | None:
        return await asyncio.to_thread(self._head_sync, path, length)

    def _head_sync(self, path: str, length: int) -> tuple[int, bytes] | None:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._container_client.get_blob_client(path)
        try:
            size = blob.get_blob_properties().size
            first = blob.download_blob(offset=0, length=min(length, size)).readall() if size else b""
        except ResourceNotFoundError:
            return None
        except Exception as exc:
            raise BlobUploadError(f"Blob 확인 실패: {path} ({type(exc).__name__})") from None
        return size, first

    def read_url(self, path: str, ttl: timedelta) -> str:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = datetime.now(timezone.utc)
        sas = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container,
            blob_name=path,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            start=now - timedelta(minutes=5),  # clock skew
            expiry=now + ttl,
            protocol="https",
        )
        return f"{self._container_client.get_blob_client(path).url}?{sas}"
