import os

from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    register_agent,
)
from tools.common.bigquery.bigquery import (
    list_tables,
    get_table_schema,
    run_sql_query,
)
from tools.common.bigquery.skill import (
    get_bigquery_skills,
    get_bigquery_skill,
)
from core.tracing.logging_middleware import (
    ToolLoggingMiddleware,
)


BQ_PROJECT_ID = os.environ[
    "BQ_SALES_PROJECT_ID"
]

BQ_DATASET_ID = os.environ[
    "BQ_SALES_DATASET"
]


async def register_bigquery_agent() -> None:
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.bigquery.system",
        BQ_PROJECT_ID=BQ_PROJECT_ID,
        BQ_DATASET_ID=BQ_DATASET_ID,
    )

    register_agent(
        name="bigquery",
        description=(
            "BigQuery 전용 Agent. "
            "BigQuery 테이블, 데이터셋, SQL, 사내 DW 데이터를 "
            "조회하거나 분석할 때 사용한다. "
            "Meta Ads 캠페인, 광고세트, 광고, 소재/크리에이티브 "
            "분석 요청에는 사용하지 않는다."
        ),
        system_prompt=system_prompt,
        tools=[
            # 등록된 BigQuery Skill 목록 조회
            get_bigquery_skills,

            # 특정 BigQuery Skill 본문 조회
            get_bigquery_skill,

            # BigQuery Tool
            list_tables,
            get_table_schema,
            run_sql_query,
        ],
        middleware=[
            ToolLoggingMiddleware(),
        ],
    )