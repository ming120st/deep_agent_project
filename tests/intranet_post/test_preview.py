import base64

from integrations.intranet_post import render_post
from integrations.intranet_post.preview import build_preview_html, safe_stem

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_preview_replaces_every_placeholder_with_a_placeholder_image(sample):
    html = build_preview_html(render_post(sample))
    assert "{{img:" not in html
    assert "data:image/svg+xml" in html
    assert "IntraBoardView1_Content" in html


def test_preview_uses_real_images_when_available(sample, tmp_path):
    (tmp_path / "header.png").write_bytes(PNG_1X1)
    html = build_preview_html(render_post(sample), images_dir=tmp_path)
    assert "data:image/png;base64," in html
    assert "{{img:" not in html


def test_preview_shows_warnings_and_escapes_title(sample):
    sample["title"] = "<b>x</b> 제목"
    html = build_preview_html(render_post(sample))
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_safe_stem():
    assert safe_stem("ai-weekly-2026-W42") == "ai-weekly-2026-W42"
    assert safe_stem("../../etc/passwd") == "etc_passwd"
