from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from google.cloud import bigquery
from google.oauth2 import service_account

_PROJECT_ID = os.getenv("BQ_BOA_PROJECT_ID", "")
_DATASET    = os.getenv("BQ_BOA_DATASET", "boa_logs")


def _get_client() -> bigquery.Client:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", "")
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ 환경변수 없음")
    creds = service_account.Credentials.from_service_account_info(json.loads(raw))
    return bigquery.Client(credentials=creds, project=_PROJECT_ID)


def _insert_sync(table_id: str, row: dict) -> None:
    client = _get_client()
    table_ref = f"{_PROJECT_ID}.{_DATASET}.{table_id}"
    job_config = bigquery.LoadJobConfig(
        schema=client.get_table(table_ref).schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    import io
    data = io.BytesIO(json.dumps(row, ensure_ascii=False).encode("utf-8"))
    job = client.load_table_from_file(data, table_ref, job_config=job_config)
    job.result()  # 완료 대기


async def insert_row(table_id: str, row: dict) -> None:
    """BQ 테이블에 비동기 단건 insert. 실패해도 업무 중단 없음."""
    if not _PROJECT_ID:
        return
    try:
        await asyncio.to_thread(_insert_sync, table_id, row)
    except Exception as e:
        print(f"[bq_logger] insert 실패 (무시): {e}")