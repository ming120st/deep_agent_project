import os
from pathlib import Path
from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from tools.common.bigquery.bigquery import (
    list_tables,
    get_table_schema,
    run_sql_query,
)


BASE_DIR = Path(__file__).parent
SKILLS_DIR = BASE_DIR / "skills"

BQ_PROJECT_ID = os.environ[
    "BQ_SALES_PROJECT_ID"
]

BQ_DATASET_ID = os.environ[
    "BQ_SALES_DATASET"
]


async def register_bigquery_agent() -> None:
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.bigquery.system",
        **{
            "BQ_PROJECT_ID": BQ_PROJECT_ID,
            "BQ_DATASET_ID": BQ_DATASET_ID,
        },
    )

    register_agent(
        name="bigquery",
        description=(
            "BigQuery 데이터 조회 및 분석이 필요할 때 사용한다."
        ),
        system_prompt=system_prompt,
        tools=[
            list_tables,
            get_table_schema,
            run_sql_query,
        ],
        skills=[
            str(SKILLS_DIR),
        ],
    )