"""The post an agent hands to the publisher: metadata + the HTML it wrote.

Agents write ``body_html`` following ``agents/common/intranet_html_agent/skills/intranet-html/SKILL.md``. This module only
checks it; it never rewrites the HTML.

- ``{{img:key}}`` is the only allowed ``<img src>``; every key must be declared in ``images``.
- Declared but unused images are allowed and reported as warnings (they are still uploaded).
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
_IMG_SRC_OK = re.compile(r"""\ssrc\s*=\s*(["'])\{\{img:[a-z0-9][a-z0-9-]{0,63}\}\}\1""", re.IGNORECASE)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")


def _clean_title(value: str) -> str:
    value = _WS.sub(" ", _CTRL.sub("", value)).strip()
    if not value:
        raise ValueError("title must not be empty")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageSpec(_Strict):
    key: Annotated[str, Field(pattern=KEY_PATTERN)]
    filename: Annotated[str, Field(pattern=FILENAME_PATTERN)]  # png/jpg/jpeg/gif only
    alt: str | None = None


class IntranetPost(_Strict):
    post_key: Annotated[str, Field(pattern=POST_KEY_PATTERN)]  # stable id, e.g. ai-weekly-2026-W42
    board: Annotated[str, Field(pattern=KEY_PATTERN)]          # board name (genai | work), never a URL
    title: Annotated[str, Field(max_length=200), AfterValidator(_clean_title)]
    body_html: Annotated[str, Field(min_length=1, max_length=MAX_BODY_CHARS)]
    images: Annotated[list[ImageSpec], Field(max_length=MAX_IMAGES)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_images(self):
        keys = [i.key for i in self.images]
        if len(set(keys)) != len(keys):
            raise ValueError("image keys must be unique")
        names = [i.filename for i in self.images]
        if len(set(names)) != len(names):
            raise ValueError("image filenames must be unique")
        if not self.body_html.strip():
            raise ValueError("body_html must not be empty")
        for tag in _IMG_TAG.findall(self.body_html):
            if not _IMG_SRC_OK.search(tag):
                raise ValueError('every <img> must use src="{{img:key}}" (no external or inline images)')
        unknown = sorted({k for k in _PLACEHOLDER.findall(self.body_html) if k not in set(keys)})
        if unknown:
            raise ValueError(f"image placeholder(s) not declared in images: {', '.join(unknown)}")
        return self

    @property
    def used_keys(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body_html))

    @property
    def warnings(self) -> list[str]:
        return [f"image {i.key!r} is declared but not used in the body" for i in self.images if i.key not in self.used_keys]

    def blob_path(self, filename: str) -> str:
        """Blob folder convention: intranet-board/{post_key}/{filename}."""
        return f"{BLOB_PREFIX}/{self.post_key}/{filename}"


def parse_post(post: "IntranetPost | dict[str, Any]") -> IntranetPost:
    """Validate a dict (or pass an IntranetPost through). Raises ``pydantic.ValidationError``."""
    return post if isinstance(post, IntranetPost) else IntranetPost.model_validate(post)


__all__ = ["IntranetPost", "ImageSpec", "ValidationError", "parse_post", "MAX_BODY_CHARS", "BLOB_PREFIX"]
