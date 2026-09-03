from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent
from tools.common.bigquery.bigquery import (
    get_table_schema,
    run_sql_query,
)


BIGQUERY_SYSTEM_PROMPT = """
너는 BigQuery 데이터 조회 및 분석 전용 에이전트다.

필요하면 get_table_schema로 실제 컬럼 구조를 확인하고,
run_sql_query로 데이터를 조회한다.

테이블명과 컬럼명을 추측하지 않는다.
"""


register_agent(
    name="bigquery",
    description="BigQuery 데이터 조회 및 분석이 필요할 때 사용한다.",
    system_prompt=BIGQUERY_SYSTEM_PROMPT,
    model=LLM_LIGHT,
    tools=[
        get_table_schema,
        run_sql_query,
    ],
)