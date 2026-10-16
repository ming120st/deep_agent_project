from html.parser import HTMLParser

import pytest
from conftest import make_doc
from pydantic import ValidationError

from integrations.intranet_post import ALLOWED_ATTRS, ALLOWED_TAGS, PostDocument, RenderError, is_safe_html, render_post
from integrations.intranet_post.themes import DEFAULT


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def parse(html: str) -> list[tuple[str, dict]]:
    parser = _Tags()
    parser.feed(html)
    return parser.tags


def body(blocks, assets=None, **kw) -> str:
    return render_post(make_doc(blocks, assets, **kw)).body_html


def p(text: str) -> dict:
    return {"type": "paragraph", "text": text}


# ------------------------------------------------------------- whole documents
def test_sample_renders_with_only_allowed_tags_and_attributes(sample):
    rendered = render_post(sample)
    assert is_safe_html(rendered.body_html)
    tags = parse(rendered.body_html)
    assert {t for t, _ in tags} <= ALLOWED_TAGS
    for tag, attrs in tags:
        assert set(attrs) <= ALLOWED_ATTRS, (tag, attrs)
    lowered = rendered.body_html.lower()
    for forbidden in ("<th", "<style", "<script", "<iframe", "<form", "<hr", "<p "):
        assert forbidden not in lowered


def test_every_td_carries_its_own_full_style(sample):
    tds = [attrs for tag, attrs in parse(render_post(sample).body_html) if tag == "td"]
    assert tds
    for attrs in tds:
        style = attrs.get("style", "")
        for needed in ("font-family:", "font-size:", "color:", "background:", "border:", "padding:"):
            assert needed in style, (needed, style)


def test_accepts_model_or_dict_and_is_deterministic(sample):
    first = render_post(sample)
    second = render_post(PostDocument.model_validate(sample))
    assert first.body_html == second.body_html
    assert first.to_dict() == second.to_dict()


def test_invalid_dict_raises_validation_error():
    with pytest.raises(ValidationError):
        render_post({"title": "x"})


def test_body_size_limit():
    big = [p("가" * 4000)] * 120  # ~480k chars
    with pytest.raises(RenderError, match="too large"):
        render_post(make_doc(big))


def test_wrapper_sets_font_and_width():
    html = body([p("x")])
    first_tag, attrs = parse(html)[0]
    assert first_tag == "div"
    assert "Malgun Gothic" in attrs["style"] and "font-size:14px" in attrs["style"]


# --------------------------------------------------------------------- escaping
def test_html_in_text_is_escaped_everywhere():
    evil = '<script>alert(1)</script><img src=x onerror=alert(1)> & "q"'
    blocks = [
        {"type": "heading", "text": evil},
        p(evil),
        {"type": "bullets", "items": [evil]},
        {"type": "callout", "title": evil, "text": evil},
        {"type": "news_card", "title": evil, "url": "https://example.com/a", "source": evil, "summary": evil, "impact": evil, "tags": [evil[:16]]},
        {"type": "table", "caption": evil, "columns": [{"label": evil}], "rows": [[evil]], "footnote": evil},
    ]
    html = body(blocks)
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html
    assert is_safe_html(html)


def test_text_that_looks_like_attributes_is_not_rejected():
    html = body([p("Based on x = y, online=1, once=twice"), {"type": "link", "text": "l", "url": "https://example.com/?a=1&online=1"}])
    assert "online=1" in html
    assert is_safe_html(html)


def test_inline_bold_links_and_newlines():
    html = body([p("**굵게** 그리고 [링크](https://example.com/a?x=1&y=2)\n둘째 줄")])
    assert "<b>굵게</b>" in html
    assert 'href="https://example.com/a?x=1&amp;y=2"' in html
    assert 'rel="noopener noreferrer"' in html and 'target="_blank"' in html
    assert "<br>" in html


def test_bold_markup_inside_a_url_does_not_break_the_href():
    html = body([p("[x](https://example.com/**a**) and **b**")])
    assert 'href="https://example.com/**a**"' in html
    assert html.count("<b>") == 1


def test_bold_inside_link_label():
    assert "<b>hi</b></a>" in body([p("[**hi**](https://example.com)")])


def test_unsafe_markdown_links_stay_plain_text():
    html = body([p("[x](javascript:alert(1)) [y](ftp://example.com/a)")])
    assert "<a " not in html
    assert is_safe_html(html)


def test_alt_text_cannot_trip_the_attribute_rule():
    assets = [{"key": "a", "filename": "a.png", "alt": 'try once=twice "quoted"'}]
    html = body([{"type": "image", "key": "a"}], assets)
    assert "&#61;" in html and "once=twice" not in html
    assert is_safe_html(html)


# ----------------------------------------------------------------------- blocks
def test_headings_have_explicit_sizes_not_bootstrap_defaults():
    html = body([{"type": "heading", "level": 2, "text": "A"}, {"type": "heading", "level": 3, "text": "B"}])
    tags = {t: a["style"] for t, a in parse(html) if t in {"h2", "h3"}}
    assert "font-size:20px" in tags["h2"] and "font-size:16px" in tags["h3"]


def test_lists_get_explicit_padding_and_ordered_variant():
    html = body([{"type": "bullets", "items": ["a", "b"]}, {"type": "bullets", "items": ["c"], "ordered": True}])
    tags = parse(html)
    assert ("ul" in [t for t, _ in tags]) and ("ol" in [t for t, _ in tags])
    for tag, attrs in tags:
        if tag in {"ul", "ol"}:
            assert "padding:0 0 0 22px" in attrs["style"]


def test_callout_uses_theme_tone_colours():
    html = body([{"type": "callout", "tone": "warn", "text": "x"}])
    tone = DEFAULT.tones["warn"]
    assert tone.bg in html and tone.border in html and tone.text in html


def test_divider_is_not_an_hr():
    html = body([{"type": "divider"}])
    assert "<hr" not in html


def test_news_card_parts():
    card = {
        "type": "news_card",
        "title": "제목",
        "url": "https://example.com/a",
        "source": "매체",
        "published_date": "2026-10-14",
        "summary": "요약",
        "impact": "의미",
        "tags": ["CS", "개발"],
    }
    html = body([card])
    for expected in ["제목", "매체 · 2026-10-14", "요약", "우리에게 의미", "의미", "CS", "개발", 'href="https://example.com/a"']:
        assert expected in html
    minimal = body([{k: v for k, v in card.items() if k not in {"impact", "tags", "published_date"}}])
    assert "우리에게 의미" not in minimal and "·" not in minimal


# ----------------------------------------------------------------------- tables
def table(rows, columns=2, **extra) -> dict:
    return {"type": "table", "columns": [{"label": f"c{i}"} for i in range(columns)], "rows": rows, **extra}


def test_number_formatting():
    html = body([table([[1234567, 3.14159], [2.0, 1000.5]])])
    for expected in ["1,234,567", "3.14", ">2<", "1,000.5"]:
        assert expected in html


def test_empty_cell_renders_nbsp_and_url_cell_renders_anchor():
    html = body([table([["", {"text": "go", "url": "https://example.com/x"}]])])
    assert "&nbsp;" in html
    assert 'href="https://example.com/x"' in html


def test_striped_rows_alternate_background():
    html = body([table([["a", "b"], ["c", "d"], ["e", "f"]], style={"striped": True})])
    assert html.count(f"background:{DEFAULT.zebra}") == 2  # only row index 1, two cells
    html = body([table([["a", "b"], ["c", "d"]], style={"striped": False})])
    assert DEFAULT.zebra not in html


def test_header_tone_and_first_column_and_compact():
    primary = body([table([["a", "b"]])])
    assert f"background:{DEFAULT.primary}" in primary
    neutral = body([table([["a", "b"]], style={"header_tone": "neutral"})])
    assert f"background:{DEFAULT.neutral_head_bg}" in neutral and f"background:{DEFAULT.primary}" not in neutral
    tds = [a["style"] for t, a in parse(body([table([["a", "b"]], style={"first_col_bold": True})])) if t == "td"]
    body_row = tds[2:]  # after the two header cells
    assert "font-weight:700" in body_row[0] and "font-weight:400" in body_row[1]
    compact = body([table([["a", "b"]], style={"compact": True})])
    assert "padding:5px 8px" in compact and "font-size:12px" in compact


def test_cell_tone_overrides_row_background():
    html = body([table([["a", {"text": "높음", "tone": "warn"}], ["c", "d"]], style={"striped": True})])
    assert DEFAULT.tones["warn"].bg in html and DEFAULT.tones["warn"].text in html


def test_caption_footnote_and_column_width():
    html = body([table([["a", "b"]], caption="캡션", footnote="각주", columns=2)])
    assert "캡션" in html and "각주" in html
    wide = {"type": "table", "columns": [{"label": "a", "width": "40%"}, {"label": "b"}], "rows": [["x", "y"]]}
    assert "width:40%" in body([wide])


# ----------------------------------------------------------------------- images
ASSETS = [
    {"key": "a", "filename": "a.png", "alt": "에이"},
    {"key": "b", "filename": "b.jpg"},
    {"key": "unused", "filename": "u.png"},
]


def test_images_are_placeholders_deduplicated_in_first_use_order_with_warnings():
    blocks = [
        {"type": "image", "key": "b", "width": 640, "caption": "cap"},
        {"type": "news_card", "title": "t", "url": "https://example.com/a", "source": "s", "summary": "x", "image_key": "a"},
        {"type": "image", "key": "b"},
    ]
    rendered = render_post(make_doc(blocks, ASSETS))
    assert [i.key for i in rendered.images] == ["b", "a"]
    assert [i.filename for i in rendered.images] == ["b.jpg", "a.png"]
    assert rendered.body_html.count("{{img:b}}") == 2 and rendered.body_html.count("{{img:a}}") == 1
    assert "width:640px;max-width:100%" in rendered.body_html
    assert any("unused" in w for w in rendered.warnings)
    assert 'src="http' not in rendered.body_html  # never a real URL: the device_agent substitutes it


def test_blob_path_convention():
    rendered = render_post(make_doc([{"type": "image", "key": "a"}], ASSETS))
    assert rendered.blob_path("a.png") == "intranet-board/test/2026-W01/a.png"
    assert set(rendered.to_dict()) == {"post_key", "series", "period", "board", "title", "body_html", "images", "warnings"}
