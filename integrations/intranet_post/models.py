"""Post document schema (v1).

An agent produces a ``PostDocument`` (pure content). The renderer turns it into
intranet-safe HTML. Agents never write HTML.

Design rules
- ``extra="forbid"``: a typo in a field name fails loudly instead of being ignored.
- Every free-text field is cleaned (control chars removed) and length-limited.
- Every URL must be a plain http(s) link (see ``safety.safe_url``).
- Colours/looks are never part of the content: only semantic names (``tone``,
  ``header_tone``, ``theme``) that the theme resolves.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from .safety import safe_url
from .themes import THEMES

SCHEMA_VERSION = 1

MAX_BLOCKS = 200
MAX_ASSETS = 20
MAX_TABLE_ROWS = 100
MAX_TABLE_COLUMNS = 10

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")

KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
POST_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$"
PERIOD_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$"
FILENAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,80}\.(?:png|jpe?g|gif)$"
WIDTH_PATTERN = r"^\d{1,3}(?:%|px)$"


def _clean_text(value: str) -> str:
    """Multi-line text: drop control characters, normalise newlines, trim."""
    value = _CTRL.sub("", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise ValueError("text must not be empty")
    return value


def _clean_line(value: str) -> str:
    """Single-line text: also collapse all whitespace/newlines to single spaces."""
    value = _WS.sub(" ", _CTRL.sub("", value)).strip()
    if not value:
        raise ValueError("text must not be empty")
    return value


def _clean_cell(value: str) -> str:
    return _WS.sub(" ", _CTRL.sub("", value)).strip()


def _check_url(value: str) -> str:
    url = safe_url(value)
    if url is None:
        raise ValueError("url must be a plain http(s) link without credentials or unsafe characters")
    return url


Text = Annotated[str, Field(max_length=5000), AfterValidator(_clean_text)]
ShortText = Annotated[str, Field(max_length=600), AfterValidator(_clean_text)]
Line = Annotated[str, Field(max_length=200), AfterValidator(_clean_line)]
Tag = Annotated[str, Field(max_length=16), AfterValidator(_clean_line)]
CellText = Annotated[str, Field(max_length=300), AfterValidator(_clean_cell)]
Url = Annotated[str, AfterValidator(_check_url)]
Key = Annotated[str, Field(pattern=KEY_PATTERN)]
ToneName = Literal["info", "good", "warn", "danger", "neutral"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------- blocks
class Heading(_Strict):
    type: Literal["heading"]
    level: Literal[2, 3] = 2
    text: Line


class Paragraph(_Strict):
    """Plain text. Inline: ``**bold**`` and ``[label](https://url)``. Newlines become line breaks."""

    type: Literal["paragraph"]
    text: Text


class Bullets(_Strict):
    type: Literal["bullets"]
    items: Annotated[list[ShortText], Field(min_length=1, max_length=30)]
    ordered: bool = False


class Callout(_Strict):
    type: Literal["callout"]
    tone: ToneName = "info"
    title: Line | None = None
    text: Text


class ImageBlock(_Strict):
    """References an entry of ``PostDocument.assets`` by key."""

    type: Literal["image"]
    key: Key
    alt: Line | None = None
    caption: Line | None = None
    width: Annotated[int, Field(ge=80, le=1200)] | None = None  # display width in px; default = fit


class LinkBlock(_Strict):
    type: Literal["link"]
    text: Line
    url: Url


class NewsCard(_Strict):
    type: Literal["news_card"]
    title: Line
    url: Url
    source: Line
    published_date: str | None = None  # YYYY-MM-DD
    summary: Annotated[str, Field(max_length=1200), AfterValidator(_clean_text)]
    impact: ShortText | None = None  # "why it matters to us"
    tags: Annotated[list[Tag], Field(max_length=5)] = Field(default_factory=list)
    image_key: Key | None = None

    @field_validator("published_date")
    @classmethod
    def _valid_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            _dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("published_date must be a valid YYYY-MM-DD date") from exc
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("published_date must be YYYY-MM-DD")
        return value


class Cell(_Strict):
    """A table cell with options. Plain strings/numbers are also accepted as cells."""

    text: CellText
    tone: ToneName | None = None
    bold: bool = False
    url: Url | None = None


CellValue = Union[Cell, CellText, int, float]


class Column(_Strict):
    label: Line
    align: Literal["left", "center", "right"] = "left"
    width: Annotated[str, Field(pattern=WIDTH_PATTERN)] | None = None  # e.g. "40%" or "120px"


class TableStyle(_Strict):
    striped: bool = True
    header_tone: Literal["primary", "neutral"] = "primary"
    first_col_bold: bool = False
    compact: bool = False


class TableBlock(_Strict):
    type: Literal["table"]
    caption: Line | None = None
    columns: Annotated[list[Column], Field(min_length=1, max_length=MAX_TABLE_COLUMNS)]
    rows: Annotated[
        list[list[CellValue]],
        Field(
            min_length=1,
            max_length=MAX_TABLE_ROWS,
            description=(
                "Each cell is a string, a number, or a Cell object. Numbers are rendered with thousands "
                "separators (2026 -> 2,026): use a string for years, ids and phone numbers."
            ),
        ),
    ]
    style: TableStyle = Field(default_factory=TableStyle)
    footnote: Line | None = None

    @field_validator("rows", mode="before")
    @classmethod
    def _reject_bool_cells(cls, rows):
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, list):
                    for cell in row:
                        if isinstance(cell, bool):
                            raise ValueError("boolean cells are not supported; use text")
                        if isinstance(cell, float) and not math.isfinite(cell):
                            raise ValueError("non-finite numbers are not supported")
        return rows

    @model_validator(mode="after")
    def _rows_match_columns(self):
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"row {index} has {len(row)} cells but there are {width} columns")
        return self


class Divider(_Strict):
    type: Literal["divider"]


Block = Annotated[
    Union[Heading, Paragraph, Bullets, Callout, ImageBlock, LinkBlock, NewsCard, TableBlock, Divider],
    Field(discriminator="type"),
]


# ------------------------------------------------------------------- document
class Asset(_Strict):
    """An image the post uses. The bytes are produced/uploaded by another module."""

    key: Key
    filename: Annotated[str, Field(pattern=FILENAME_PATTERN)]  # png/jpg/jpeg/gif only (svg is not supported)
    alt: Line | None = None


class PostDocument(_Strict):
    schema_version: Literal[1] = 1
    post_key: Annotated[str, Field(pattern=POST_KEY_PATTERN)]   # stable id of this post, e.g. ai-weekly-2026-W42
    series: Key                                                  # e.g. ai-weekly  (blob folder level 1)
    period: Annotated[str, Field(pattern=PERIOD_PATTERN)]        # e.g. 2026-W42   (blob folder level 2)
    board: Key                                                   # board name from config, never a URL
    title: Annotated[str, Field(max_length=200), AfterValidator(_clean_line)]
    theme: str = "default"
    assets: Annotated[list[Asset], Field(max_length=MAX_ASSETS)] = Field(default_factory=list)
    blocks: Annotated[list[Block], Field(min_length=1, max_length=MAX_BLOCKS)]

    @field_validator("theme")
    @classmethod
    def _known_theme(cls, value: str) -> str:
        if value not in THEMES:
            raise ValueError(f"unknown theme {value!r}; available: {', '.join(THEMES)}")
        return value

    @model_validator(mode="after")
    def _check_assets(self):
        keys = [asset.key for asset in self.assets]
        if len(set(keys)) != len(keys):
            raise ValueError("asset keys must be unique")
        names = [asset.filename for asset in self.assets]
        if len(set(names)) != len(names):
            raise ValueError("asset filenames must be unique")
        known = set(keys)
        for key in self.referenced_image_keys():
            if key not in known:
                raise ValueError(f"image key {key!r} is used in blocks but not declared in assets")
        return self

    def referenced_image_keys(self) -> list[str]:
        """Image keys in order of first use."""
        seen: dict[str, None] = {}
        for block in self.blocks:
            if isinstance(block, ImageBlock):
                seen.setdefault(block.key)
            elif isinstance(block, NewsCard) and block.image_key:
                seen.setdefault(block.image_key)
        return list(seen)
