import os

from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from tools.common.notion.tool import (
    notion_append_markdown,
    notion_create_page,
    notion_get_page,
    notion_get_page_markdown,
    notion_query_database,
    notion_replace_page_content,
    notion_search,
)

NOTION_PAGE_ID = os.environ["NOTION_PAGE_ID"]
async def register_notion_agent() -> None:
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.notion.system",
        **{
            "NOTION_PAGE_ID": NOTION_PAGE_ID,
        },
    )

    register_agent(
        name="notion",
        description=(
            "Notion 페이지와 데이터베이스를 검색, 조회, 생성, "
            "수정하거나 내용을 기록해야 할 때 사용한다."
        ),
        system_prompt=system_prompt,
        tools=[
            notion_search,
            notion_get_page,
            notion_get_page_markdown,
            notion_query_database,
            notion_append_markdown,
            notion_create_page,
            notion_replace_page_content,
        ],
    )