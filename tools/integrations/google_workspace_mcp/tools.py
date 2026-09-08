import re
from typing import Any, Dict, List

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from tools.integrations.google_workspace_mcp.runtime import (
    google_workspace_runtime,
)


@tool
async def drive_search(
    query: str,
    runtime: ToolRuntime,
) -> str:
    """현재 사용자의 Google Drive에서 파일을 검색한다."""

    user_email = runtime.state.get(
        "user_email"
    )

    if not user_email:
        return "사용자 이메일을 확인할 수 없습니다."

    search_tool = google_workspace_runtime.get_tool(
        "search_drive_files"
    )

    result = await search_tool.ainvoke(
        {
            "query": query,
            "user_google_email": user_email,
        }
    )

    return str(result)


@tool
async def drive_create_file(
    file_name: str,
    content: str,
    runtime: ToolRuntime,
) -> str:
    """현재 사용자의 Google Drive에 파일을 생성한다."""

    user_email = runtime.state.get(
        "user_email"
    )

    if not user_email:
        return "사용자 이메일을 확인할 수 없습니다."

    create_tool = google_workspace_runtime.get_tool(
        "create_drive_file"
    )

    result = await create_tool.ainvoke(
        {
            "user_google_email": user_email,
            "file_name": file_name,
            "content": content,
            "folder_id": "root",
            "mime_type": "text/plain",
        }
    )

    return str(result)


@tool
async def gmail_search(
    query: str,
    runtime: ToolRuntime,
) -> str:
    """현재 사용자의 Gmail에서 메일을 검색한다."""

    user_email = runtime.state.get(
        "user_email"
    )

    if not user_email:
        return "사용자 이메일을 확인할 수 없습니다."

    search_tool = (
        google_workspace_runtime.get_tool(
            "search_gmail_messages"
        )
    )

    result = await search_tool.ainvoke(
        {
            "user_google_email": user_email,
            "query": query,
        }
    )

    return str(result)


@tool
async def gmail_recent_messages(
    runtime: ToolRuntime,
    limit: int = 5,
) -> str:
    """
    현재 사용자의 최근 Gmail 메일을 조회한다.
    최근 메일의 제목, 발신자 등의 확인에 사용한다.
    """

    user_email = runtime.state.get(
        "user_email"
    )

    if not user_email:
        return "사용자 이메일을 확인할 수 없습니다."

    search_tool = (
        google_workspace_runtime.get_tool(
            "search_gmail_messages"
        )
    )

    search_result = await search_tool.ainvoke(
        {
            "user_google_email": user_email,
            "query": "newer_than:30d",
        }
    )

    print(
        "SEARCH RESULT TYPE:",
        type(search_result),
    )
    print(
        "SEARCH RESULT:",
        repr(search_result),
    )

    search_text = "\n".join(
        item.get("text", "")
        for item in search_result
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
        )
    )

    message_ids = re.findall(
        r"Message ID:\s*([a-zA-Z0-9]+)",
        search_text,
    )[:limit]

    if not message_ids:
        return "최근 메일을 찾을 수 없습니다."

    batch_tool = (
        google_workspace_runtime.get_tool(
            "get_gmail_messages_content_batch"
        )
    )

    result = await batch_tool.ainvoke(
        {
            "user_google_email": user_email,
            "message_ids": message_ids,
        }
    )

    return str(result)


@tool
async def gmail_send(
    to: str,
    subject: str,
    body: str,
    runtime: ToolRuntime,
) -> str:
    """현재 사용자의 Gmail 계정으로 메일을 발송한다."""

    user_email = runtime.state.get(
        "user_email"
    )

    if not user_email:
        return "사용자 이메일을 확인할 수 없습니다."

    send_tool = (
        google_workspace_runtime.get_tool(
            "send_gmail_message"
        )
    )

    result = await send_tool.ainvoke(
        {
            "user_google_email": user_email,
            "to": to,
            "subject": subject,
            "body": body,
            "body_format": "plain",
        }
    )

    return str(result)