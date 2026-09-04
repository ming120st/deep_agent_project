"""BigQuery 조회 전용 LangChain Tool 모음 (프로덕션 재사용 모듈).

이 파일은 특정 프로젝트/도메인에 대한 어떤 가정도 담고 있지 않다. 어떤 테이블을
조회할지는 Tool 호출 시 인자로 전달되며, 그 판단(어떤 테이블을 볼지, 무엇을
분석할지)은 이 파일을 사용하는 에이전트의 시스템 프롬프트 쪽 책임이다.

다른 프로젝트에 이 파일 하나만 복사해서 넣어도 그대로 동작하도록,
이 프로젝트의 다른 모듈(src.*)을 import하지 않는다.

필요한 환경변수:
    GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ : BigQuery 접근용 서비스 계정 키.
        (이름과 달리 파일 경로가 아니라, 서비스 계정 JSON 전체를 문자열로 담는다.
         예: '{"type": "service_account", "project_id": "...", ...}')

선택 환경변수:
    BQ_MAX_BYTES_BILLED    : 쿼리 1회당 과금 허용 최대 바이트. 기본 5GB.
    BQ_QUERY_TIMEOUT_SECONDS : 쿼리 1회 최대 대기 시간(초). 기본 60초.

환경변수가 없거나 형식이 잘못되면, 이 모듈을 import하는 시점에 즉시
명확한 한글 메시지와 함께 실패한다 (런타임 중간에 죽는 것을 방지).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from google.cloud import bigquery
from google.oauth2 import service_account
from langchain.tools import tool

# --------------------------------------------------------------------------
# 환경변수 / 클라이언트 초기화 (fail-fast: import 시점에 바로 검증)
# --------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"환경변수 '{name}'가 설정되어 있지 않습니다. "
            "BigQuery 도구(bigquery_tool.py)를 사용하려면 이 값이 반드시 필요합니다."
        )
    return value


def _build_client() -> bigquery.Client:
    raw_key = _require_env("GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ")
    try:
        credentials_info = json.loads(raw_key)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "환경변수 'GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ'의 값이 올바른 JSON이 아닙니다. "
            "서비스 계정 키 JSON 전체(파일 경로가 아니라 내용 자체)를 문자열로 넣어야 합니다."
        ) from exc

    try:
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
    except (ValueError, KeyError) as exc:
        raise RuntimeError(
            "환경변수 'GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ'의 JSON이 유효한 서비스 계정 키 형식이 아닙니다. "
            f"원인: {exc}"
        ) from exc

    return bigquery.Client(project=credentials.project_id, credentials=credentials)


_MAX_BYTES_BILLED = int(os.getenv("BQ_MAX_BYTES_BILLED", str(5 * 1024**3)))  # 기본 5GB
_QUERY_TIMEOUT_SECONDS = int(os.getenv("BQ_QUERY_TIMEOUT_SECONDS", "60"))
_DEFAULT_ROW_LIMIT = 200

_client = _build_client()

# --------------------------------------------------------------------------
# SQL 안전장치: SELECT/WITH 조회 쿼리만 허용 (쓰기 방지의 최후 방어선).
#
# 주의: 이건 코드 레벨의 방어선일 뿐, 진짜 보안 경계는 이 Tool이 사용하는
# 서비스 계정의 BigQuery IAM 권한을 읽기 전용(Data Viewer/Job User)으로
# 제한하는 것이다. 이 검증은 그 인프라 조치를 대체하지 않는다.
# --------------------------------------------------------------------------

_ALLOWED_START = re.compile(r"^\s*(--[^\n]*\n|\s)*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_KEYWORD = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|CALL|EXPORT)\b",
    re.IGNORECASE,
)


def _validate_select_only(sql: str) -> str | None:
    """조회 전용 쿼리인지 검증한다. 문제가 있으면 한글 에러 메시지를, 문제없으면 None을 반환."""
    if not sql or not sql.strip():
        return "sql이 비어 있습니다."
    if not _ALLOWED_START.match(sql):
        return "SELECT 또는 WITH로 시작하는 조회 쿼리만 허용됩니다."
    if _FORBIDDEN_KEYWORD.search(sql):
        return "데이터/스키마를 변경하는 SQL 구문(INSERT/UPDATE/DELETE/DROP 등)은 허용되지 않습니다."
    # 세미콜론으로 구분된 다중 문장(뒤에 또 다른 명령이 붙는 SQL 인젝션 패턴)을 차단한다.
    body = sql.rstrip().rstrip(";")
    if ";" in body:
        return "세미콜론으로 구분된 여러 SQL 문장은 허용되지 않습니다. 쿼리 하나만 전달하세요."
    return None


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@tool
def get_table_schema(
    table: Annotated[
        str,
        "스키마를 조회할 BigQuery 테이블의 전체 경로. "
        "'project_id.dataset.table' 형식이 가장 안전하며, "
        "생략된 project_id는 이 도구가 사용하는 서비스 계정의 기본 프로젝트로 간주된다. "
        "예: 'my-gcp-project.my_dataset.my_table' 또는 'my_dataset.my_table'",
    ],
) -> str:
    """지정한 BigQuery 테이블의 컬럼명/타입/설명을 조회한다.

    SQL을 작성하기 전에 반드시 이 도구를 먼저 호출해서 실제 컬럼 구조를 확인해야 한다.
    컬럼명을 추측해서 바로 run_sql_query를 호출하면 존재하지 않는 컬럼 오류가 날 수 있다.

    반환값 예시:
        {"table": "proj.ds.tbl", "columns": [{"name": "Order_ID", "type": "STRING", "description": "..."}]}
    실패 시:
        {"error": "사람이 읽을 수 있는 한글 오류 메시지"}
    """
    try:
        bq_table = _client.get_table(table)
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 에이전트에 알려줘야 재시도/보고가 가능하다
        return json.dumps(
            {"error": f"테이블 '{table}'의 스키마를 조회하는 중 오류가 발생했습니다: {exc}"},
            ensure_ascii=False,
        )

    columns = [
        {"name": field.name, "type": field.field_type, "description": field.description or ""}
        for field in bq_table.schema
    ]
    return json.dumps({"table": table, "columns": columns}, ensure_ascii=False)


@tool
def run_sql_query(
    sql: Annotated[
        str,
        "실행할 BigQuery Standard SQL. 반드시 SELECT(또는 WITH ... SELECT)로 시작하는 "
        "'조회 전용' 쿼리 하나여야 한다 (세미콜론으로 여러 문장을 이어붙이면 거부된다). "
        "테이블은 get_table_schema로 확인한 전체 경로(project.dataset.table)를 backtick으로 감싸서 사용한다. "
        "예: \"SELECT col_a, COUNT(*) FROM `proj.ds.tbl` WHERE col_b = 'x' GROUP BY col_a\"",
    ],
    row_limit: Annotated[
        int,
        "결과로 받을 최대 행 수. 쿼리에 이미 LIMIT이 있으면 이 값은 무시된다. 기본 200.",
    ] = _DEFAULT_ROW_LIMIT,
) -> str:
    """BigQuery에서 조회 전용 SQL을 실행하고 결과를 반환한다.
    
    실제 데이터 조회, 집계, 통계 계산이 필요할 때 사용한다.
    """
    validation_error = _validate_select_only(sql)
    if validation_error:
        return json.dumps({"error": validation_error}, ensure_ascii=False)

    query = sql if re.search(r"\bLIMIT\b", sql, re.IGNORECASE) else f"{sql.rstrip().rstrip(';')}\nLIMIT {row_limit}"
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=_MAX_BYTES_BILLED)

    try:
        query_job = _client.query(query, job_config=job_config)
        rows = [dict(row.items()) for row in query_job.result(timeout=_QUERY_TIMEOUT_SECONDS)]
    except Exception as exc: 
        return json.dumps({"error": f"쿼리 실행 중 오류가 발생했습니다: {exc}"}, ensure_ascii=False)

    return json.dumps({"row_count": len(rows), "rows": rows}, ensure_ascii=False, default=_json_default)
