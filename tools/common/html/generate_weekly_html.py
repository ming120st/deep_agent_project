
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from core.state.search_repository import (
    search_repository,
)
from core.state.article_repository import (
    article_repository,
)
from agents.common.html_agent.content_writer import (
    ContentWriter,
)
from agents.common.html_agent.html_generator import (
    HTMLGenerator,
)
from tools.integrations.intranet_publish.store import MongoPublishStore
import os

KST = ZoneInfo("Asia/Seoul")


def create_post_key() -> str:
    """
    인트라넷 게시글 고유 ID 생성.

    형식: ai-weekly-YYYYMMDD-난수8자리

    생성된 post_key는 MongoDB에 저장되어
    이후 게시 요청에서도 동일하게 사용한다.
    """
    now = datetime.now(KST)

    return (
        f"ai-weekly-{now:%Y%m%d}-"
        f"{uuid4().hex[:8]}"
    )


@tool
async def generate_weekly_html(
    search_id: str,
    board: Literal["genai", "work"] = "genai",
    post_key: str | None = None,
) -> dict:
    """
    MongoDB 검색 결과를 기반으로
    인트라넷 게시용 콘텐츠와 HTML을 생성한다.

    1. 검색 결과 조회
    2. 게시글 고유 ID 생성
    3. LLM 콘텐츠 작성
    4. ArticleContent MongoDB 저장
    5. SKILL 기반 HTML 생성
    6. 게시용 레코드 구성
    7. boa_intranet_posts MongoDB 저장
    8. HTML 파일 저장
    9. 생성 결과 반환
    """

    search_id = search_id.strip()

    if not search_id:
        raise ValueError(
            "search_id는 필수입니다."
        )

    # 1. MongoDB 검색 데이터 조회
    record = await search_repository.get_search_record(
        search_id=search_id
    )

    if record is None:
        raise ValueError(
            f"검색 결과를 찾을 수 없습니다: {search_id}"
        )

    if record.status != "completed":
        raise ValueError(
            f"검색이 완료되지 않았습니다: {record.status}"
        )

    response = record.search_response

    if not response.results:
        raise ValueError(
            "검색 결과가 없어 콘텐츠를 생성할 수 없습니다."
        )

    # 2. 게시글 고유 ID 결정
    # 재시도 시 기존 post_key를 전달할 수 있다.
    post_key = (
        post_key.strip()
        if post_key is not None
        else create_post_key()
    )

    if (
        not post_key
        or len(post_key) > 80
        or not all(
            c.isascii() and (
                c.isalnum() or c == "-"
            )
            for c in post_key
        )
    ):
        raise ValueError(
            "post_key는 영문/숫자/하이픈만 "
            "사용하며 80자 이하여야 합니다."
        )

    # 3. LLM 콘텐츠 작성
    writer = ContentWriter()

    article = await writer.write(
        response=response
    )

    # 4. 게시용 콘텐츠 MongoDB 저장
    await article_repository.save_article(
        search_id=search_id,
        post_key=post_key,
        article=article,
    )

    # 5. SKILL 기반 HTML 생성
    generator = HTMLGenerator()

    html = await generator.generate(
        article=article
    )

    if not html.strip():
        raise ValueError(
            "HTML 생성 결과가 비어 있습니다."
        )

    # 6. 게시용 데이터 구성
    now = datetime.now(KST)

    job_id = f"intranet-{board}-{post_key}"

    publish_record = {
        "job_id": job_id,
        "thread_id": f"intranet-publish-{search_id}",
        "post_key": post_key,
        "board": board,
        "title": article.title,
        "body_html": html,
        "images": [],
        "recipients": [],
        "context": {
            "search_id": search_id,
        },
        "notify": {},
        "status": "generated",
        "attempt": 0,
        "reason": None,
        "result": None,
        "created_at": now,
        "updated_at": now,
    }

    # 7. MongoDB 게시 레코드 저장
    publish_store = MongoPublishStore()

    existing = await publish_store.get(
        job_id=job_id
    )

    if existing is not None:
        if existing.get("status") != "generated":
            raise ValueError(
                "이미 게시 처리가 시작된 "
                f"레코드입니다: {job_id}"
            )

        # 동일 job_id 재생성 시 최초 생성일 유지
        publish_record["created_at"] = (
            existing["created_at"]
        )

    await publish_store.save(
        publish_record
    )

    # 8. HTML 파일 저장
    root_dir = Path(
        os.environ["DEEP_AGENT_ROOT_DIR"]
    ).resolve()

    output_dir = root_dir / "output"

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    html_path = (
        output_dir
        / f"{search_id}_article.html"
    )

    html_path.write_text(
        html,
        encoding="utf-8",
    )

    # 9. 결과 반환
    return {
        "success": True,
        "search_id": search_id,
        "job_id": job_id,
        "post_key": post_key,
        "board": board,
        "title": article.title,
        "section_count": len(article.sections),
        "status": "generated",
    }