from core.llm.model_factory import LLM_LIGHT
import os
from registry.agent_registry import register_agent

from tools.common.bigquery.bigquery import (
    list_tables,
    get_table_schema,
    run_sql_query,
)
BQ_PROJECT_ID = os.environ["BQ_SALES_PROJECT_ID"]
BQ_DATASET_ID = os.environ["BQ_SALES_DATASET"]

BIGQUERY_SYSTEM_PROMPT = f"""
너는 BigQuery 데이터 조회 및 분석 전용 에이전트다.

기본 BigQuery 위치: 
- Project ID: `{BQ_PROJECT_ID}` 
- Dataset ID: `{BQ_DATASET_ID}` 
- Dataset 전체 경로: `{BQ_PROJECT_ID}.{BQ_DATASET_ID}`

규칙:
1. 위임받은 요청에서 분석 대상 데이터셋 또는 테이블 범위를 확인한다.
2. 대상 테이블이 명확하지 않으면 list_tables로 사용 가능한 테이블을 확인한다.
3. 사용할 테이블을 결정한 뒤 반드시 get_table_schema로 실제 컬럼 구조를 확인한다.
4. 테이블명과 컬럼명을 추측하지 않는다.
5. 확인한 스키마를 바탕으로 run_sql_query를 사용해 조회 전용 SQL을 실행한다.
6. 필요한 경우 여러 번 조회해 분석하되, 조회 결과와 핵심 인사이트를 명확히 정리해 반환한다.
"""


bigquery_agent = {
    "name": "bigquery",
    "description": (
        "BigQuery 데이터 조회 및 분석이 필요할 때 사용한다."
    ),
    "system_prompt": BIGQUERY_SYSTEM_PROMPT,
    "model": LLM_LIGHT,
    "tools": [
        list_tables,
        get_table_schema,
        run_sql_query,
    ],
}


register_agent(
    name=bigquery_agent["name"],
    description=bigquery_agent["description"],
    system_prompt=bigquery_agent["system_prompt"],
    model=bigquery_agent["model"],
    tools=bigquery_agent["tools"],
)