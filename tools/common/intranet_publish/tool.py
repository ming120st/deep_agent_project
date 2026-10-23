"""인트라넷 게시 Tool (publish_post를 에이전트가 호출할 수 있게 감싼 것).

- 게시 입력이 규격에 맞지 않으면(금지 태그, 선언 안 된 이미지 등) 예외가 아니라
  {"success": False, "error": {"code", "message", "details"}} 를 도구 결과로 돌려준다.
  에이전트는 이를 읽고 HTML을 고쳐 다시 호출한다.
- 결과는 항상 JSON 문자열이 아니라 dict (LangChain이 ToolMessage로 변환).
- 구글챗 알림 대상(space_id, google_thread_name)은 RunnableConfig에서 자동으로 가져온다.
- 이미지는 먼저 upload_intranet_image로 Blob에 올리고(경로 규칙: intranet-board/{post_key}/{filename}),
  publish_intranet_post의 images에는 파일명만 적는다. 게시 때 Blob에 있는지 다시 검사한다.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from tools.integrations.intranet_publish import (
    PublishError,
    get_publisher,
)
from tools.integrations.intranet_publish.fetch import (
    fetch_image,
)
from tools.integrations.intranet_publish.post import (
    IntranetPost,
)
from tools.integrations.intranet_publish.validation import (
    check_image,
)

logger = logging.getLogger(__name__)


def _cfg(config: RunnableConfig | None, key: str, default: str = "") -> str:
    return ((config or {}).get("configurable") or {}).get(key, default)


@tool
async def upload_intranet_image(
    post_key: Annotated[str, "이 이미지를 쓸 글의 post_key (publish_intranet_post에 넘길 값과 같아야 한다)"],
    filename: Annotated[str, "저장할 파일명. 소문자 영문/숫자/./_/- 만, 확장자 png/jpg/jpeg/gif. 예: header.png"],
    image_url: Annotated[str, "이미지를 내려받을 공개 http(s) 주소"],
) -> dict[str, Any]:
    """이미지 주소에서 이미지를 내려받아 Blob에 올린다 (게시 전에 호출).

    성공하면 {"success": True, "filename": ...}를 돌려준다. 이어서 publish_intranet_post의
    images에 이 파일명을 적고, 본문에는 <img src="{{img:파일명}}">으로 쓴다.
    같은 post_key와 filename으로 다시 올리면 덮어쓴다. 실패하면 error의 code와 message를 읽고 주소나 파일명을 바꿔 다시 호출한다.
    """
    try:
        probe = IntranetPost(
            post_key=post_key, board="genai", title="x", body_html="x", images=[filename]
        )
    except Exception:
        return {"success": False, "error": {"code": "INVALID_DOCUMENT", "message": "post_key 또는 filename 형식이 올바르지 않습니다."}}
    try:
        data = await fetch_image(image_url)
        content_type = check_image(filename, data)
        await get_publisher().blob.upload(probe.blob_path(filename), data, content_type)
    except PublishError as exc:
        return {"success": False, "error": exc.to_dict()}
    except Exception as exc:
        logger.exception("[IntranetPublishTool] image upload failed")
        return {"success": False, "error": {"code": "UNEXPECTED", "message": f"이미지 업로드 중 오류가 발생했습니다 ({type(exc).__name__})."}}
    return {"success": True, "filename": filename, "size": len(data)}

@tool
async def publish_saved_intranet_post(
    job_id: Annotated[
        str,
        "generate_weekly_html이 반환한 job_id"
    ],
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    generate_weekly_html에서 MongoDB에 저장한
    HTML을 조회하여 인트라넷에 게시한다.
    """

    publisher = get_publisher()

    # MongoDB에서 저장된 게시글 조회
    saved_post = await publisher.store.get(job_id)

    if saved_post is None:
        return {
            "success": False,
            "error": {
                "code": "NOT_FOUND",
                "message": "저장된 게시글을 찾을 수 없습니다.",
            },
        }

    if saved_post.get("status") != "generated":
        return {
            "success": False,
            "error": {
                "code": "INVALID_STATUS",
                "message": (
                    "게시 가능한 generated 상태가 아닙니다. "
                    f"현재 상태: {saved_post.get('status')}"
                ),
            },
        }

    body_html = saved_post.get("body_html")

    if not isinstance(body_html, str) or not body_html.strip():
        return {
            "success": False,
            "error": {
                "code": "EMPTY_HTML",
                "message": "저장된 HTML이 비어 있습니다.",
            },
        }

    thread_id = (
        _cfg(config, "thread_id")
        or _cfg(config, "session_id")
    )

    if not thread_id:
        return {
            "success": False,
            "error": {
                "code": "NO_THREAD",
                "message": "thread_id를 찾을 수 없습니다.",
            },
        }

    space_id = _cfg(config, "space_id")

    notify = None

    if space_id:
        notify = {
            "space_id": space_id,
            "thread_name": _cfg(
                config,
                "google_thread_name",
            ),
        }
    try:
        result = await publisher.publish_saved_post(
            job_id=job_id,
            thread_id=thread_id,
            notify=notify,
        )

    except PublishError as exc:
        logger.warning(
            "[IntranetPublishTool] saved publish rejected: %s",
            exc.code,
        )
        return {
            "success": False,
            "error": exc.to_dict(),
        }

    except Exception as exc:
        logger.exception(
            "[IntranetPublishTool] saved publish failed"
        )
        return {
            "success": False,
            "error": {
                "code": "UNEXPECTED",
                "message": (
                    "저장된 게시글 게시 중 오류가 발생했습니다 "
                    f"({type(exc).__name__})."
                ),
            },
        }

    return {
        "success": True,
        **result.to_dict(),
    }
@tool
async def check_intranet_post_status(
    job_id: Annotated[str, "publish_intranet_post가 돌려준 job_id"],
) -> dict[str, Any]:
    """게시 요청의 현재 상태를 조회한다.

    status: pending(진행 중), retry_wait(재시도 대기), succeeded(게시됨, result에 post_url),
    failed(게시 안 됨, reason 확인), needs_check(올라갔을 수 있으니 사람이 게시판 확인).
    """
    try:
        status = await get_publisher().get_status(job_id)
    except Exception as exc:
        logger.exception("[IntranetPublishTool] status failed")
        return {"success": False, "error": {"code": "UNEXPECTED", "message": f"상태 조회 중 오류가 발생했습니다 ({type(exc).__name__})."}}
    if status is None:
        return {"success": False, "error": {"code": "NOT_FOUND", "message": "해당 job_id의 게시 요청이 없습니다."}}
    return {"success": True, **status}


INTRANET_PUBLISH_TOOLS = [upload_intranet_image, publish_saved_intranet_post, check_intranet_post_status]
