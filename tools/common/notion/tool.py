# tools/common/notion/tools.py

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal

from langchain.tools import tool

from tools.common.notion.client import (
    describe_error,
    error_json,
    get_notion_client,
)


_MARKDOWN_CHUNK_SIZE = int(
    os.getenv(
        "NOTION_MARKDOWN_CHUNK_SIZE",
        "80000",
    )
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _extract_title(
    obj: dict[str, Any],
) -> str:

    if obj.get("object") == "database":
        return "".join(
            item.get("plain_text", "")
            for item in obj.get("title", [])
        )

    if obj.get("object") == "page":
        for prop in obj.get(
            "properties",
            {},
        ).values():

            if prop.get("type") == "title":
                return "".join(
                    item.get("plain_text", "")
                    for item in prop.get(
                        "title",
                        [],
                    )
                )

    return ""


def _summarize_object(
    obj: dict[str, Any],
) -> dict[str, Any]:

    return {
        "id": obj.get("id"),
        "object": obj.get("object"),
        "title": _extract_title(obj),
        "url": obj.get("url"),
        "last_edited_time": obj.get(
            "last_edited_time"
        ),
    }


def _summarize_properties(
    properties: dict[str, Any],
) -> dict[str, Any]:

    summary: dict[str, Any] = {}

    for name, prop in properties.items():
        prop_type = prop.get("type")

        if prop_type == "title":
            summary[name] = "".join(
                item.get("plain_text", "")
                for item in prop.get(
                    "title",
                    [],
                )
            )

        elif prop_type == "rich_text":
            summary[name] = "".join(
                item.get("plain_text", "")
                for item in prop.get(
                    "rich_text",
                    [],
                )
            )

        elif prop_type in (
            "number",
            "checkbox",
            "url",
            "email",
            "phone_number",
            "date",
        ):
            summary[name] = prop.get(
                prop_type
            )

        elif prop_type == "select":
            summary[name] = (
                prop.get("select")
                or {}
            ).get("name")

        elif prop_type == "status":
            summary[name] = (
                prop.get("status")
                or {}
            ).get("name")

        elif prop_type == "multi_select":
            summary[name] = [
                item.get("name")
                for item in prop.get(
                    "multi_select",
                    [],
                )
            ]

        elif prop_type == "people":
            summary[name] = [
                person.get("name")
                for person in prop.get(
                    "people",
                    [],
                )
            ]

        else:
            summary[name] = prop.get(
                prop_type
            )

    return summary


def _split_markdown(
    markdown: str,
    chunk_size: int,
) -> list[str]:

    if len(markdown) <= chunk_size:
        return [markdown]

    paragraphs = markdown.split("\n\n")

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = (
            f"{current}\n\n{paragraph}"
            if current
            else paragraph
        )

        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(paragraph) > chunk_size:
            for index in range(
                0,
                len(paragraph),
                chunk_size,
            ):
                chunks.append(
                    paragraph[
                        index:index + chunk_size
                    ]
                )

            current = ""

        else:
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def _resolve_data_source_id(
    database_id: str,
) -> str:

    client = get_notion_client()

    database = client.databases.retrieve(
        database_id=database_id,
    )

    data_sources = (
        database.get("data_sources")
        or []
    )

    if not data_sources:
        raise ValueError(
            f"데이터베이스 '{database_id}'에서 "
            "data source를 찾을 수 없습니다."
        )

    return data_sources[0]["id"]


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


@tool
def notion_search(
    query: Annotated[
        str,
        (
            "검색할 텍스트. 페이지/데이터베이스의 "
            "제목을 기준으로 검색된다."
        ),
    ],
    object_type: Annotated[
        Literal["page", "database"] | None,
        (
            "결과를 페이지 또는 데이터베이스로 "
            "제한할 때 지정한다."
        ),
    ] = None,
    page_size: Annotated[
        int,
        "반환받을 최대 결과 수 (1~100)",
    ] = 20,
) -> str:
    """
    Connect된 Notion 페이지/데이터베이스를
    제목으로 검색한다.
    """

    client = get_notion_client()

    try:
        kwargs: dict[str, Any] = {
            "query": query,
            "page_size": min(
                max(page_size, 1),
                100,
            ),
        }

        if object_type:
            kwargs["filter"] = {
                "property": "object",
                "value": object_type,
            }

        response = client.search(
            **kwargs
        )

    except Exception as exc:
        return error_json(exc)

    results = [
        _summarize_object(item)
        for item in response.get(
            "results",
            [],
        )
    ]

    return json.dumps(
        {
            "result_count": len(results),
            "results": results,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@tool
def notion_get_page(
    page_id: Annotated[
        str,
        "조회할 Notion 페이지 ID",
    ],
) -> str:
    """
    Notion 페이지의 제목, URL, 속성,
    마지막 수정 시각 등을 조회한다.
    """

    client = get_notion_client()

    try:
        page = client.pages.retrieve(
            page_id=page_id,
        )

    except Exception as exc:
        return error_json(exc)

    result = _summarize_object(page)

    result["properties"] = (
        _summarize_properties(
            page.get(
                "properties",
                {},
            )
        )
    )

    return json.dumps(
        result,
        ensure_ascii=False,
    )


@tool
def notion_get_page_markdown(
    page_id: Annotated[
        str,
        "본문을 조회할 Notion 페이지 ID",
    ],
) -> str:
    """
    Notion 페이지의 본문 내용을
    Markdown 형태로 가져온다.
    """

    client = get_notion_client()

    try:
        response = (
            client.pages.retrieve_markdown(
                page_id=page_id,
            )
        )

    except Exception as exc:
        return error_json(exc)

    return json.dumps(
        {
            "page_id": page_id,
            "markdown": response.get(
                "markdown",
                "",
            ),
            "truncated": bool(
                response.get(
                    "truncated",
                    False,
                )
            ),
        },
        ensure_ascii=False,
    )


@tool
def notion_append_markdown(
    page_id: Annotated[
        str,
        "내용을 추가할 대상 Notion 페이지 ID",
    ],
    markdown: Annotated[
        str,
        "페이지 끝에 추가할 Markdown 텍스트",
    ],
) -> str:
    """
    기존 페이지 내용을 유지하면서
    Markdown 내용을 페이지 끝에 추가한다.
    """

    client = get_notion_client()

    chunks = _split_markdown(
        markdown,
        _MARKDOWN_CHUNK_SIZE,
    )

    appended_chunks = 0

    try:
        for chunk in chunks:
            client.pages.update_markdown(
                page_id=page_id,
                type="insert_content",
                insert_content={
                    "content": chunk,
                },
            )

            appended_chunks += 1

    except Exception as exc:
        info = describe_error(exc)

        info[
            "chunks_appended_before_failure"
        ] = appended_chunks

        info["total_chunks"] = len(
            chunks
        )

        return json.dumps(
            info,
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "success": True,
            "page_id": page_id,
            "chunks_appended": (
                appended_chunks
            ),
            "characters_written": (
                len(markdown)
            ),
        },
        ensure_ascii=False,
    )


@tool
def notion_replace_page_content(
    page_id: Annotated[
        str,
        "내용을 완전히 교체할 대상 Notion 페이지 ID",
    ],
    markdown: Annotated[
        str,
        "새 본문이 될 Markdown 텍스트",
    ],
) -> str:
    """
    페이지의 기존 본문 전체를
    새로운 Markdown 내용으로 교체한다.
    """

    client = get_notion_client()

    try:
        client.pages.update_markdown(
            page_id=page_id,
            type="replace_content",
            replace_content={
                "new_str": markdown,
            },
        )

    except Exception as exc:
        return error_json(exc)

    return json.dumps(
        {
            "success": True,
            "page_id": page_id,
            "characters_written": (
                len(markdown)
            ),
        },
        ensure_ascii=False,
    )


@tool
def notion_create_page(
    parent_page_id: Annotated[
        str,
        "새 페이지를 만들 부모 Notion 페이지 ID",
    ],
    title: Annotated[
        str,
        "새 페이지의 제목",
    ],
    markdown: Annotated[
        str,
        "새 페이지의 초기 Markdown 본문",
    ] = "",
) -> str:
    """
    지정한 부모 페이지 하위에
    새로운 Notion 페이지를 만든다.
    """

    client = get_notion_client()

    try:
        page: dict[str, Any] = {
            "parent": {
                "type": "page_id",
                "page_id": parent_page_id,
            },
            "properties": {
                "title": {
                    "title": [
                        {
                            "type": "text",
                            "text": {
                                "content": title,
                            },
                        }
                    ]
                }
            },
        }

        if markdown:
            page["markdown"] = markdown

        created = client.pages.create(
            **page
        )

    except Exception as exc:
        return error_json(exc)

    return json.dumps(
        _summarize_object(created),
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# Databases
# --------------------------------------------------------------------------


@tool
def notion_query_database(
    database_id: Annotated[
        str,
        "조회할 Notion 데이터베이스 ID",
    ],
    filter: Annotated[
        dict[str, Any] | None,
        "Notion API filter 객체",
    ] = None,
    sorts: Annotated[
        list[dict[str, Any]] | None,
        "Notion API sorts 배열",
    ] = None,
    page_size: Annotated[
        int,
        "반환받을 최대 행 수 (1~100)",
    ] = 50,
) -> str:
    """
    Notion 데이터베이스 항목을
    조건에 맞게 조회한다.
    """

    client = get_notion_client()

    try:
        data_source_id = (
            _resolve_data_source_id(
                database_id
            )
        )

        kwargs: dict[str, Any] = {
            "data_source_id": (
                data_source_id
            ),
            "page_size": min(
                max(page_size, 1),
                100,
            ),
        }

        if filter:
            kwargs["filter"] = filter

        if sorts:
            kwargs["sorts"] = sorts

        response = (
            client.data_sources.query(
                **kwargs
            )
        )

    except Exception as exc:
        return error_json(exc)

    rows = []

    for row in response.get(
        "results",
        [],
    ):
        item = _summarize_object(row)

        item["properties"] = (
            _summarize_properties(
                row.get(
                    "properties",
                    {},
                )
            )
        )

        rows.append(item)

    return json.dumps(
        {
            "row_count": len(rows),
            "has_more": bool(
                response.get(
                    "has_more",
                    False,
                )
            ),
            "rows": rows,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


@tool
def notion_get_block_children(
    block_id: Annotated[
        str,
        "자식 블록을 조회할 페이지 또는 블록 ID",
    ],
    page_size: Annotated[
        int,
        "가져올 최대 블록 수 (1~100)",
    ] = 100,
) -> str:
    """
    지정한 페이지 또는 블록의
    하위 블록 목록을 조회한다.
    """

    client = get_notion_client()

    try:
        response = (
            client.blocks.children.list(
                block_id=block_id,
                page_size=min(
                    max(page_size, 1),
                    100,
                ),
            )
        )

    except Exception as exc:
        return error_json(exc)

    blocks = []

    for block in response.get(
        "results",
        [],
    ):
        block_type = block.get("type")

        payload = (
            block.get(
                block_type,
                {},
            )
            if block_type
            else {}
        )

        text = (
            "".join(
                item.get(
                    "plain_text",
                    "",
                )
                for item in payload.get(
                    "rich_text",
                    [],
                )
            )
            if isinstance(
                payload,
                dict,
            )
            
            else ""
        )

        blocks.append(
            {
                "id": block.get("id"),
                "type": block_type,
                "text": text,
                "has_children": bool(
                    block.get(
                        "has_children",
                        False,
                    )
                ),
            }
        )

    return json.dumps(
        {
            "block_count": len(blocks),
            "has_more": bool(
                response.get(
                    "has_more",
                    False,
                )
            ),
            "blocks": blocks,
        },
        ensure_ascii=False,
    )