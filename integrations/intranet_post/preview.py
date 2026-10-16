"""Local preview that imitates the intranet *view* page.

The real view page injects the post body into ``<span id="IntraBoardView1_Content">`` and
applies its global CSS (CommonCss.css + Bootstrap 3 + page styles) to it. This module
reproduces the parts of that CSS that broke earlier tests, so rendering problems show up
here instead of after posting:

- ``td``: Verdana 12px, colour #678197, white background, 1px 5px padding, inset border
- ``th``: 9.6px light-blue text on a pale-blue background
- ``p {margin:0 0 10px}``, ``ul/ol {padding-left:0}``, Bootstrap heading sizes (h2 = 30px)

It is an approximation, not the real page: the final check is still one test post.
"""
from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from .renderer import RenderedPost

INTRANET_CSS = """
html,body{margin:0;padding:0;background:#fff}
body{font:12px Verdana,sans-serif;color:#333}
table{border-spacing:0;background:transparent}
td{font-family:Verdana,sans-serif;font-size:12px;color:#678197;background:#fff;padding:1px 5px;border:1px inset #ccc}
th{font-family:'Century Gothic',Verdana,sans-serif;font-size:9.6px;color:#66A3D3;background:#F4F9FE;text-align:left;padding:0;border:1px inset #ccc}
p{margin:0 0 10px}
ul,ol{margin-bottom:10px;padding-left:0}
img{vertical-align:middle;border:0}
h1,h2,h3,h4,h5,h6{font-family:inherit;font-weight:500;line-height:1.1;color:inherit;margin-top:20px;margin-bottom:10px}
h1{font-size:36px}h2{font-size:30px}h3{font-size:24px}h4{font-size:18px}
a{color:#337ab7;text-decoration:none}
.meta td.k{background:#F4F9FE;color:#66A3D3;text-align:center;font-weight:bold;width:90px}
.note{font:12px Verdana;color:#888;padding:8px 12px;background:#fffbe6;border-bottom:1px solid #eee}
"""


def _placeholder(key: str, filename: str) -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="360" viewBox="0 0 1440 360">'
        '<rect width="1440" height="360" fill="#dfe6ee"/>'
        '<rect x="6" y="6" width="1428" height="348" fill="none" stroke="#9fb0c3" stroke-width="3" stroke-dasharray="14 10"/>'
        '<text x="720" y="170" font-family="Verdana,sans-serif" font-size="46" fill="#52667d" text-anchor="middle">'
        f"image placeholder: {html.escape(key)}</text>"
        '<text x="720" y="230" font-family="Verdana,sans-serif" font-size="32" fill="#7a8ca1" text-anchor="middle">'
        f"{html.escape(filename)} (preview only)</text></svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def resolve_images(rendered: RenderedPost, body_html: str, images_dir: Path | None) -> str:
    """Replace ``{{img:key}}`` with a real local image if present, else a labelled placeholder."""
    for ref in rendered.images:
        candidate = (images_dir / ref.filename) if images_dir else None
        uri = _data_uri(candidate) if candidate and candidate.is_file() else _placeholder(ref.key, ref.filename)
        body_html = body_html.replace("{{img:%s}}" % ref.key, uri)
    return body_html


def build_preview_html(rendered: RenderedPost, *, images_dir: str | Path | None = None) -> str:
    body = resolve_images(rendered, rendered.body_html, Path(images_dir) if images_dir else None)
    title = html.escape(rendered.title)
    warnings = ""
    if rendered.warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in rendered.warnings)
        warnings = f'<div class="note"><b>warnings</b><ul style="margin:4px 0 0 18px;padding:0">{items}</ul></div>'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>preview - {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{INTRANET_CSS}</style></head>
<body>
<div class="note">미리보기(인트라넷 보기 화면의 전역 CSS를 흉내 낸 근사치입니다). 이미지가 없으면 회색 자리표시자로 보입니다.</div>
{warnings}
<table class="meta" style="width:100%;margin-top:8px">
<tr><td class="k">제목</td><td colspan="3">{title}</td></tr>
<tr><td class="k">게시판</td><td>{html.escape(rendered.board)}</td><td class="k">post_key</td><td>{html.escape(rendered.post_key)}</td></tr>
<tr><td class="k" style="vertical-align:top">내용</td><td colspan="3" id="content-cell"><span id="IntraBoardView1_Content">{body}</span></td></tr>
</table>
</body></html>"""


def screenshot(html_path: str | Path, png_path: str | Path, *, width: int = 1100) -> None:
    """Full-page Chromium screenshot (needs playwright + a Chromium install)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(Path(html_path).resolve().as_uri())
            page.wait_for_load_state("load")
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(name: str) -> str:
    return _SAFE_NAME.sub("_", name).strip("._") or "post"
