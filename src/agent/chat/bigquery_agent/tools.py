"""
src/agent/chat/bigquery_agent/tools.py

BigQuery 조회 도구 모음.
기존 src/core/bigquery_client.py의 bq_client를 재사용합니다.
"""
import os
import json
import logging
from langchain.tools import tool

from src.core.bigquery_client import bq_client

logger = logging.getLogger(__name__)

BQ_PROJECT_ID = os.getenv("BQ_PROJECT_ID", "")


@tool
def list_datasets() -> str:
    """
    BigQuery 프로젝트 내 모든 데이터셋 목록을 반환합니다.
    분석 시작 전 어떤 데이터셋이 있는지 파악할 때 사용하세요.
    """
    try:
        datasets = [ds.dataset_id for ds in bq_client.list_datasets()]
        if not datasets:
            return "데이터셋이 없습니다."
        return json.dumps(datasets, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[list_datasets] {e}")
        return f"오류: {e}"


@tool
def list_tables(dataset_id: str) -> str:
    """
    특정 데이터셋 내 테이블 목록을 반환합니다.

    Args:
        dataset_id: 조회할 데이터셋 ID (예: "esim_dataset")
    """
    try:
        tables = [t.table_id for t in bq_client.list_tables(dataset_id)]
        if not tables:
            return f"'{dataset_id}'에 테이블이 없습니다."
        return json.dumps(tables, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[list_tables] {e}")
        return f"오류: {e}"


@tool
def get_table_schema(dataset_id: str, table_id: str) -> str:
    """
    테이블의 스키마(컬럼명, 타입, 설명)와 행 수를 반환합니다.
    쿼리 작성 전 반드시 스키마를 확인하세요.

    Args:
        dataset_id: 데이터셋 ID
        table_id: 테이블 ID
    """
    try:
        table_ref = f"{BQ_PROJECT_ID}.{dataset_id}.{table_id}"
        table = bq_client.get_table(table_ref)
        result = {
            "table": table_ref,
            "num_rows": table.num_rows,
            "schema": [
                {
                    "name": f.name,
                    "type": f.field_type,
                    "mode": f.mode,
                    "description": f.description or "",
                }
                for f in table.schema
            ],
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[get_table_schema] {e}")
        return f"오류: {e}"


@tool
def get_sample_rows(dataset_id: str, table_id: str, limit: int = 5) -> str:
    """
    테이블의 샘플 데이터를 조회합니다.
    실제 데이터 형태와 값의 패턴을 파악할 때 사용하세요.

    Args:
        dataset_id: 데이터셋 ID
        table_id: 테이블 ID
        limit: 샘플 행 수 (기본 5)
    """
    try:
        sql = f"SELECT * FROM `{BQ_PROJECT_ID}.{dataset_id}.{table_id}` LIMIT {limit}"
        rows = [dict(row) for row in bq_client.query(sql).result()]
        if not rows:
            return "데이터가 없습니다."
        return json.dumps(rows, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"[get_sample_rows] {e}")
        return f"오류: {e}"


@tool
def run_query(sql: str, max_rows: int = 500) -> str:
    """
    BigQuery SQL 쿼리를 실행하고 결과를 반환합니다.
    - SELECT 쿼리만 허용됩니다.
    - 날짜 필터를 반드시 포함하여 범위를 제한하세요.
    - 테이블 참조는 백틱으로 감싸세요: `project.dataset.table`

    Args:
        sql: 실행할 SELECT SQL 문
        max_rows: 최대 반환 행 수 (기본 500)
    """
    try:
        if not sql.strip().upper().startswith("SELECT"):
            return "오류: SELECT 쿼리만 허용됩니다."
        rows = [dict(r) for r in bq_client.query(sql).result(max_results=max_rows)]
        if not rows:
            return "쿼리 결과가 없습니다."
        return json.dumps(rows, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"[run_query] {e}")
        return f"오류: {e}"


BIGQUERY_TOOLS = [
    list_datasets,
    list_tables,
    get_table_schema,
    get_sample_rows,
    run_query,
]
