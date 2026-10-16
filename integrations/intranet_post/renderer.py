"""PostDocument -> intranet-safe HTML.

Rules baked in here (found by testing the real intranet view page):
- Everything is an inline ``style`` (a ``<style>`` block would restyle the whole page).
- No ``<th>`` (page CSS overrides it): headers are ``<td>`` with explicit colours.
- Every ``<td>`` carries its own font/size/colour/background/border/padding
  (``td`` does not inherit from the wrapper because the page styles ``td`` itself).
- Lists get explicit padding, headings get explicit size/margin (Bootstrap 3 defaults differ).
- Only tags from ``ALLOWED_TAGS``; links are http(s) only; all text is HTML-escaped.
- Images are ``{{img:key}}`` placeholders; the device_agent swaps in the uploaded URL.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Mapping

from .models import (
    Bullets,
    Callout,
    Cell,
    Divider,
    Heading,
    ImageBlock,
    LinkBlock,
    NewsCard,
    Paragraph,
    PostDocument,
    TableBlock,
)
from .safety import is_safe_html, safe_url
from .themes import Theme, get_theme

MAX_BODY_CHARS = 300_000
BLOB_PREFIX = "intranet-board"
LABEL_IMPACT = "우리에게 의미"

# Used by tests to guarantee the renderer never emits anything outside this set.
ALLOWED_TAGS = frozenset({"div", "span", "h2", "h3", "ul", "ol", "li", "table", "tr", "td", "a", "img", "b", "br"})
ALLOWED_ATTRS = frozenset({"style", "href", "target", "rel", "src", "alt"})


class RenderError(ValueError):
    """The document could not be rendered safely."""


@dataclass(frozen=True)
class ImageRef:
    key: str
    filename: str
    alt: str = ""


@dataclass
class RenderedPost:
    post_key: str
    series: str
    period: str
    board: str
    title: str
    body_html: str
    images: list[ImageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def blob_path(self, filename: str) -> str:
        """Blob folder convention: intranet-board/{series}/{period}/{filename}."""
        return f"{BLOB_PREFIX}/{self.series}/{self.period}/{filename}"

    def to_dict(self) -> dict:
        return {
            "post_key": self.post_key,
            "series": self.series,
            "period": self.period,
            "board": self.board,
            "title": self.title,
            "body_html": self.body_html,
            "images": [{"key": i.key, "filename": i.filename, "alt": i.alt} for i in self.images],
            "warnings": list(self.warnings),
        }


# ----------------------------------------------------------------- inline text
_MD_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^\s()]+)\)")
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_SLOT_RE = re.compile("(\\d+)")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _bold(escaped: str) -> str:
    return _MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)


def _anchor(url: str, label_html: str, th: Theme, *, color: str | None = None, underline: bool = True) -> str:
    deco = "underline" if underline else "none"
    return (
        f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
        f'style="color:{color or th.primary};text-decoration:{deco}">{label_html}</a>'
    )


def _inline(text: str, th: Theme) -> str:
    """Escape, then apply the tiny inline syntax: **bold**, [label](https://url), newlines."""
    escaped = _esc(text.replace("", "").replace("", ""))
    anchors: list[str] = []

    def take_link(match: re.Match[str]) -> str:
        url = safe_url(html.unescape(match.group(2)))
        if url is None:
            return match.group(0)  # not a safe link: leave it as plain text
        anchors.append(_anchor(url, _bold(match.group(1)), th))
        return f"{len(anchors) - 1}"

    out = _MD_LINK.sub(take_link, escaped)
    out = _bold(out).replace("\n", "<br>")
    return _SLOT_RE.sub(lambda m: anchors[int(m.group(1))], out)


# --------------------------------------------------------------------- context
class _Ctx:
    def __init__(self, doc: PostDocument, th: Theme) -> None:
        self.doc = doc
        self.th = th
        self.assets = {a.key: a for a in doc.assets}
        self.used: dict[str, ImageRef] = {}

    def img(self, key: str, alt: str | None = None, width: int | None = None) -> str:
        asset = self.assets[key]
        text = alt or asset.alt or ""
        self.used.setdefault(key, ImageRef(key=key, filename=asset.filename, alt=text))
        # "=" is escaped in alt text so it can never look like an attribute assignment to the safety regex
        safe_alt = _esc(text).replace("=", "&#61;")
        size = f"width:{width}px;max-width:100%;" if width else "max-width:100%;"
        return (
            f'<img src="{{{{img:{key}}}}}" alt="{safe_alt}" '
            f'style="{size}height:auto;border:0;vertical-align:middle">'
        )


# ---------------------------------------------------------------------- blocks
def _heading(b: Heading, c: _Ctx) -> str:
    th = c.th
    text = _esc(b.text)
    if b.level == 2:
        return (
            f'<h2 style="margin:30px 0 14px 0;padding:0 0 8px 0;font-family:{th.font_family};font-size:20px;'
            f'line-height:1.35;font-weight:700;color:{th.primary_dark};border-bottom:2px solid {th.primary}">{text}</h2>'
        )
    return (
        f'<h3 style="margin:22px 0 10px 0;padding:2px 0 2px 10px;font-family:{th.font_family};font-size:16px;'
        f'line-height:1.4;font-weight:700;color:{th.text};border-left:4px solid {th.primary}">{text}</h3>'
    )


def _paragraph(b: Paragraph, c: _Ctx) -> str:
    th = c.th
    return (
        f'<div style="margin:0 0 12px 0;font-size:{th.base_size}px;line-height:1.7;color:{th.text}">'
        f"{_inline(b.text, th)}</div>"
    )


def _bullets(b: Bullets, c: _Ctx) -> str:
    th = c.th
    tag = "ol" if b.ordered else "ul"
    kind = "decimal" if b.ordered else "disc"
    items = "".join(
        f'<li style="margin:0 0 6px 0;padding:0;font-size:{th.base_size}px;line-height:1.6;color:{th.text}">'
        f"{_inline(item, th)}</li>"
        for item in b.items
    )
    return f'<{tag} style="margin:0 0 12px 0;padding:0 0 0 22px;list-style-type:{kind}">{items}</{tag}>'


def _callout(b: Callout, c: _Ctx) -> str:
    th = c.th
    tone = th.tones[b.tone]
    title = (
        f'<div style="margin:0 0 4px 0;font-weight:700;font-size:{th.base_size}px">{_esc(b.title)}</div>'
        if b.title
        else ""
    )
    return (
        f'<div style="margin:0 0 14px 0;padding:12px 16px;background:{tone.bg};border:1px solid {tone.border};'
        f"border-left:5px solid {tone.accent};border-radius:{th.radius}px;color:{tone.text};"
        f'font-size:{th.base_size}px;line-height:1.65">{title}<div style="margin:0">{_inline(b.text, th)}</div></div>'
    )


def _image(b: ImageBlock, c: _Ctx) -> str:
    th = c.th
    caption = (
        f'<div style="margin:6px 0 0 0;font-size:12px;line-height:1.5;color:{th.muted};text-align:center">'
        f"{_esc(b.caption)}</div>"
        if b.caption
        else ""
    )
    return f'<div style="margin:0 0 14px 0;text-align:center">{c.img(b.key, b.alt, b.width)}{caption}</div>'


def _link(b: LinkBlock, c: _Ctx) -> str:
    th = c.th
    return (
        f'<div style="margin:0 0 12px 0;font-size:{th.base_size}px;line-height:1.6">'
        f"{_anchor(b.url, _esc(b.text), th)}</div>"
    )


def _divider(_: Divider, c: _Ctx) -> str:
    return f'<div style="margin:22px 0;height:0;line-height:0;font-size:0;border-top:1px solid {c.th.line}">&nbsp;</div>'


def _news_card(b: NewsCard, c: _Ctx) -> str:
    th = c.th
    tags = ""
    if b.tags:
        chips = "".join(
            f'<span style="display:inline-block;margin:0 6px 4px 0;padding:2px 8px;background:{th.primary_soft};'
            f"color:{th.primary_dark};border:1px solid {th.primary_border};border-radius:10px;font-size:11px;"
            f'line-height:1.5;font-weight:700">{_esc(tag)}</span>'
            for tag in b.tags
        )
        tags = f'<div style="margin:0 0 6px 0">{chips}</div>'
    title = (
        f'<div style="margin:0 0 4px 0;font-size:17px;line-height:1.4;font-weight:700">'
        f"{_anchor(b.url, _esc(b.title), th, color=th.primary_dark, underline=False)}</div>"
    )
    meta_text = _esc(b.source) + (f" · {_esc(b.published_date)}" if b.published_date else "")
    meta = f'<div style="margin:0 0 10px 0;font-size:12px;line-height:1.5;color:{th.muted}">{meta_text}</div>'
    image = f'<div style="margin:0 0 10px 0">{c.img(b.image_key)}</div>' if b.image_key else ""
    summary = (
        f'<div style="margin:0 0 10px 0;font-size:{th.base_size}px;line-height:1.7;color:{th.text}">'
        f"{_inline(b.summary, th)}</div>"
    )
    impact = ""
    if b.impact:
        impact = (
            f'<div style="margin:0;padding:10px 12px;background:{th.primary_soft};border-radius:4px;'
            f'font-size:{th.base_size - 1}px;line-height:1.65;color:{th.text}">'
            f'<b style="color:{th.primary_dark}">{LABEL_IMPACT}</b><br>{_inline(b.impact, th)}</div>'
        )
    return (
        f'<div style="margin:0 0 14px 0;padding:14px 16px;background:{th.surface};border:1px solid {th.line};'
        f"border-left:5px solid {th.primary};border-radius:{th.radius}px;box-shadow:0 1px 3px rgba(0,0,0,0.06)\">"
        f"{tags}{title}{meta}{image}{summary}{impact}</div>"
    )


def _fmt_number(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _table(b: TableBlock, c: _Ctx) -> str:
    th = c.th
    style = b.style
    size = th.base_size - (2 if style.compact else 1)
    pad = "5px 8px" if style.compact else "8px 12px"

    def td(inner: str, *, bg: str, color: str, align: str, bold: bool, border: str, extra: str = "") -> str:
        return (
            f'<td style="font-family:{th.font_family};font-size:{size}px;line-height:1.5;color:{color};'
            f"background:{bg};border:1px solid {border};padding:{pad};text-align:{align};vertical-align:top;"
            f'font-weight:{700 if bold else 400}{extra}">{inner}</td>'
        )

    if style.header_tone == "primary":
        head_bg, head_color, head_border = th.primary, th.on_primary, th.primary_dark
    else:
        head_bg, head_color, head_border = th.neutral_head_bg, th.text, th.line

    head_cells = "".join(
        td(
            _esc(col.label),
            bg=head_bg,
            color=head_color,
            align=col.align,
            bold=True,
            border=head_border,
            extra=f";width:{col.width}" if col.width else "",
        )
        for col in b.columns
    )
    rows = [f"<tr>{head_cells}</tr>"]

    for r_index, row in enumerate(b.rows):
        bg_row = th.zebra if (style.striped and r_index % 2 == 1) else th.surface
        cells = []
        for c_index, value in enumerate(row):
            col = b.columns[c_index]
            tone = None
            bold = style.first_col_bold and c_index == 0
            url = None
            if isinstance(value, Cell):
                text, tone, url = value.text, value.tone, value.url
                bold = bold or value.bold
            else:
                text = _fmt_number(value) if isinstance(value, (int, float)) else str(value)
            inner = _esc(text) if text else "&nbsp;"
            bg, color = bg_row, th.text
            if tone:
                bg, color = th.tones[tone].bg, th.tones[tone].text
            if url:
                inner = _anchor(url, inner, th, color=color if tone else None)
            cells.append(td(inner, bg=bg, color=color, align=col.align, bold=bold, border=th.line))
        rows.append(f"<tr>{''.join(cells)}</tr>")

    caption = (
        f'<div style="margin:0 0 6px 0;font-size:{th.base_size}px;font-weight:700;color:{th.text}">{_esc(b.caption)}</div>'
        if b.caption
        else ""
    )
    footnote = (
        f'<div style="margin:4px 0 14px 0;font-size:12px;line-height:1.5;color:{th.muted}">{_esc(b.footnote)}</div>'
        if b.footnote
        else ""
    )
    bottom = "0" if b.footnote else "14px"
    table = (
        f'<table style="border-collapse:collapse;border-spacing:0;width:100%;margin:0 0 {bottom} 0;'
        f'background:{th.surface}">{"".join(rows)}</table>'
    )
    return f"{caption}{table}{footnote}"


_RENDERERS = {
    Heading: _heading,
    Paragraph: _paragraph,
    Bullets: _bullets,
    Callout: _callout,
    ImageBlock: _image,
    LinkBlock: _link,
    NewsCard: _news_card,
    TableBlock: _table,
    Divider: _divider,
}


# ------------------------------------------------------------------------ main
def render_post(document: PostDocument | Mapping) -> RenderedPost:
    """Validate (if a dict) and render. Raises ``pydantic.ValidationError`` or ``RenderError``."""
    doc = document if isinstance(document, PostDocument) else PostDocument.model_validate(document)
    th = get_theme(doc.theme)
    ctx = _Ctx(doc, th)

    parts = []
    for block in doc.blocks:
        try:
            renderer = _RENDERERS[type(block)]
        except KeyError as exc:  # pragma: no cover - the schema makes this unreachable
            raise RenderError(f"no renderer for block type {type(block).__name__}") from exc
        parts.append(renderer(block, ctx))

    body = (
        f'<div style="font-family:{th.font_family};font-size:{th.base_size}px;line-height:1.6;'
        f'color:{th.text};max-width:{th.content_width}px">\n' + "\n".join(parts) + "\n</div>"
    )

    if len(body) > MAX_BODY_CHARS:
        raise RenderError(f"rendered body is too large ({len(body)} > {MAX_BODY_CHARS} chars)")
    if not is_safe_html(body):  # defence in depth; should be impossible
        raise RenderError("rendered body failed the safety check")
    placeholders = set(re.findall(r"\{\{img:([a-z0-9-]+)\}\}", body))
    if placeholders != set(ctx.used):  # pragma: no cover - internal consistency
        raise RenderError("image placeholders do not match the collected image list")

    warnings = [
        f"asset {asset.key!r} is declared but not used by any block"
        for asset in doc.assets
        if asset.key not in ctx.used
    ]
    return RenderedPost(
        post_key=doc.post_key,
        series=doc.series,
        period=doc.period,
        board=doc.board,
        title=doc.title,
        body_html=body,
        images=list(ctx.used.values()),
        warnings=warnings,
    )
