import os
from pathlib import Path

from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent
from tools.common.bigquery.bigquery import (
    list_tables,
    get_table_schema,
    run_sql_query,
)

BASE_DIR = Path(__file__).parent
SKILLS_DIR = BASE_DIR / "skills"

BQ_PROJECT_ID = os.environ["BQ_SALES_PROJECT_ID"]
BQ_DATASET_ID = os.environ["BQ_SALES_DATASET"]

SYSTEM_PROMPT = (
    BASE_DIR
    / "prompts"
    / "system.md"
).read_text(
    encoding="utf-8"
)

BIGQUERY_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    .replace(
        "{{ BQ_PROJECT_ID }}",
        BQ_PROJECT_ID,
    )
    .replace(
        "{{ BQ_DATASET_ID }}",
        BQ_DATASET_ID,
    )
)


bigquery_agent = {
    "name": "bigquery",
    "description": (
        "BigQuery 데이터 조회 및 분석이 필요할 때 사용한다."
    ),
    "system_prompt": BIGQUERY_SYSTEM_PROMPT,
    "model": LLM_LIGHT,
    "skills": [
        str(SKILLS_DIR),
    ],
    "tools": [
        list_tables,
        get_table_schema,
        run_sql_query,
    ],
}


def register_bigquery_agent() -> None:
    register_agent(
        name=bigquery_agent["name"],
        description=bigquery_agent["description"],
        system_prompt=bigquery_agent["system_prompt"],
        model=bigquery_agent["model"],
        tools=bigquery_agent["tools"],
        skills=bigquery_agent["skills"],
    )