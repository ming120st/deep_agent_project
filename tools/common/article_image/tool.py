"""기사 대표 이미지를 기존 인트라넷 Blob 저장소에 업로드하는 LangChain Tool."""
from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

import httpx
from langchain_core.tools import tool
from pydantic import ValidationError

from tools.common.article_image.extractor import (
    ArticleImageExtractionError,
    extract_article_og_image,
)
from tools.integrations.intranet_publish import PublishError, get_publisher
from tools.integrations.intranet_publish.fetch import fetch_image
from tools.integrations.intranet_publish.post import IntranetPost
from tools.integrations.intranet_publish.validation import check_image

logger = logging.getLogger(__name__)


def _image_extension(data: bytes) -> str:
    """파일명과 Content-Type을 실제 바이너리 포맷과 일치시키기 위한 판별."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    raise ValueError("지원하지 않는 이미지 포맷입니다 (jpg/png/gif만 허용).")


@tool
async def upload_article_image(
    article_url: Annotated[str, "대표 이미지를 추출할 뉴스 기사 HTTPS URL"],
    post_key: Annotated[str, "인트라넷 글의 post_key (최종 게시 post_key와 동일해야 함)"],
) -> dict[str, Any]:
    """기사 og:image를 가져와 intranet-board/{post_key}/{filename} Blob에 저장한다.

    성공 결과의 filename을 게시글 images 배열과 {{img:filename}} 참조에 사용한다.
    """
    try:
        image_url = await extract_article_og_image(article_url)
        if image_url is None:
            return {
                "success": False,
                "error": {"code": "IMAGE_NOT_FOUND", "message": "기사에 대표 이미지 메타태그가 없습니다."},
            }

        # 기존 fetch_image가 외부 이미지 URL 안전성/크기/리다이렉트를 검증하는지 확인 필요.
        data = await fetch_image(image_url)
        extension = _image_extension(data)
        digest = hashlib.sha256(article_url.encode("utf-8")).hexdigest()[:20]
        filename = f"news-{digest}.{extension}"

        post = IntranetPost(
            post_key=post_key,
            board="genai",
            title="article-image",
            body_html="article-image",
            images=[filename],
        )
        content_type = check_image(filename, data)
        blob_path = post.blob_path(filename)
        await get_publisher().blob.upload(blob_path, data, content_type)

        return {
            "success": True,
            "article_url": article_url,
            "original_image_url": image_url,
            "filename": filename,
            "blob_path": blob_path,
            "content_type": content_type,
            "size": len(data),
        }
    except ValidationError:
        return {"success": False, "error": {"code": "INVALID_DOCUMENT", "message": "post_key 또는 이미지 파일명이 올바르지 않습니다."}}
    except PublishError as exc:
        return {"success": False, "error": exc.to_dict()}
    except ArticleImageExtractionError as exc:
        return {"success": False, "error": {"code": "INVALID_ARTICLE", "message": str(exc)}}
    except httpx.HTTPStatusError as exc:
        return {"success": False, "error": {"code": "HTTP_ERROR", "message": f"원격 서버 HTTP {exc.response.status_code}"}}
    except (httpx.HTTPError, ValueError) as exc:
        return {"success": False, "error": {"code": "IMAGE_FETCH_FAILED", "message": type(exc).__name__}}
    except Exception as exc:
        logger.exception("[ArticleImage] image upload failed")
        return {"success": False, "error": {"code": "UNEXPECTED", "message": f"이미지 업로드 실패 ({type(exc).__name__})"}}