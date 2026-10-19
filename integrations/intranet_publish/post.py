"""에이전트가 게시자(publisher)에게 전달하는 게시글 데이터: 메타데이터 + 작성된 HTML.

에이전트는 ``agents/common/intranet_html_agent/skills/intranet-html/SKILL.md`` 규칙에 따라 ``body_html``을 작성합니다.
본 모듈은 검증만 수행하며, HTML 내용을 직접 수정하지 않습니다.

- ``<img src>``에는 ``{{img:파일명}}`` 형태만 허용되며, 모든 파일명은 ``images``에 선언되어 있어야 합니다
  (이미지는 Blob의 ``intranet-board/{post_key}/`` 경로에 미리 업로드되어 있어야 함).
- 선언되었으나 본문에서 사용되지 않은 이미지는 허용되며 경고(warnings)로 보고됩니다(업로드는 정상 진행됨).
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

BLOB_PREFIX = "intranet-board"
MAX_BODY_CHARS = 300_000
MAX_IMAGES = 20

KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
POST_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$"
FILENAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,80}\.(?:png|jpe?g|gif)$"

_PLACEHOLDER = re.compile(r"\{\{img:([^{}]*)\}\}")
_IMG_TAG = re.compile(r"<\s*img\b[^>]*>", re.IGNORECASE)
_IMG_SRC_OK = re.compile(r"""\ssrc\s*=\s*(["'])\{\{img:[a-z0-9][a-z0-9._-]{0,80}\.(?:png|jpe?g|gif)\}\}\1""", re.IGNORECASE)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")


def _clean_title(value: str) -> str:
    value = _WS.sub(" ", _CTRL.sub("", value)).strip()
    if not value:
        raise ValueError("title must not be empty")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntranetPost(_Strict):
    post_key: Annotated[str, Field(pattern=POST_KEY_PATTERN)]  # 고유 ID (예: ai-weekly-2026-W42)
    board: Annotated[str, Field(pattern=KEY_PATTERN)]          # 게시판 이름 (genai | work), URL 형태 불가
    title: Annotated[str, Field(max_length=200), AfterValidator(_clean_title)]
    body_html: Annotated[str, Field(min_length=1, max_length=MAX_BODY_CHARS)]
    images: Annotated[list[Annotated[str, Field(pattern=FILENAME_PATTERN)]], Field(max_length=MAX_IMAGES)] = Field(default_factory=list)  # 이미지 파일명 목록

    @model_validator(mode="after")
    def _check_images(self):
        if len(set(self.images)) != len(self.images):
            raise ValueError("image filenames must be unique")
        if not self.body_html.strip():
            raise ValueError("body_html must not be empty")
        for tag in _IMG_TAG.findall(self.body_html):
            if not _IMG_SRC_OK.search(tag):
                raise ValueError('every <img> must use src="{{img:filename}}" (no external or inline images)')
        unknown = sorted(set(_PLACEHOLDER.findall(self.body_html)) - set(self.images))
        if unknown:
            raise ValueError(f"image placeholder(s) not declared in images: {', '.join(unknown)}")
        return self

    @property
    def used_images(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body_html))

    @property
    def warnings(self) -> list[str]:
        return [f"image {name!r} is listed but not used in the body" for name in self.images if name not in self.used_images]

    def blob_path(self, filename: str) -> str:
        """Blob 저장 경로 규칙: intranet-board/{post_key}/{filename}"""
        return f"{BLOB_PREFIX}/{self.post_key}/{filename}"


def parse_post(post: "IntranetPost | dict[str, Any]") -> IntranetPost:
    """dict 입력을 검증합니다(IntranetPost 인스턴스는 그대로 반환). 실패 시 ``pydantic.ValidationError``를 발생시킵니다."""
    return post if isinstance(post, IntranetPost) else IntranetPost.model_validate(post)


__all__ = ["IntranetPost", "ValidationError", "parse_post", "MAX_BODY_CHARS", "BLOB_PREFIX"]
