from __future__ import annotations

import os
from pathlib import Path

from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent

from tools.common.notion.tool import (
    notion_append_markdown,
    notion_create_page,
    notion_get_block_children,
    notion_get_page,
    notion_get_page_markdown,
    notion_query_database,
    notion_replace_page_content,
    notion_search,
)


SYSTEM_PROMPT = (
    Path(__file__).parent
    / "prompts"
    / "system.md"
).read_text(
    encoding="utf-8"
)

SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{{ NOTION_PAGE_ID }}",
    os.environ["NOTION_PAGE_ID"],
)


def register_notion_agent() -> None:
    register_agent(
        name="notion",
        description=(
            "Notion 페이지와 데이터베이스를 검색, 조회, 생성, "
            "수정하거나 내용을 기록해야 할 때 사용한다."
        ),
        system_prompt=SYSTEM_PROMPT,
        model=LLM_LIGHT,
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