import copy

import pytest
from conftest import make_doc
from pydantic import ValidationError

from integrations.intranet_post import PostDocument

P = {"type": "paragraph", "text": "본문"}


def test_sample_document_is_valid(sample):
    doc = PostDocument.model_validate(sample)
    assert doc.referenced_image_keys() == ["header", "issue1", "chart-keywords"]


@pytest.mark.parametrize(
    "override",
    [
        {"post_key": "bad key!"},
        {"post_key": ""},
        {"board": "https://intra.widemobile.com/x"},  # boards are names, never URLs
        {"board": "Upper"},
        {"series": "has space"},
        {"theme": "does-not-exist"},
        {"schema_version": 2},
        {"unknown_field": 1},
        {"title": "   "},
    ],
)
def test_bad_top_level_fields_are_rejected(override):
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([P], **override))


def test_blocks_must_not_be_empty_or_too_many():
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([]))
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([P] * 201))


def test_unknown_block_type_and_extra_block_field_rejected():
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "raw_html", "html": "<b>x</b>"}]))
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "paragraph", "text": "a", "style": "color:red"}]))


def test_empty_text_rejected_and_control_chars_removed():
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "paragraph", "text": "  \n "}]))
    doc = PostDocument.model_validate(make_doc([{"type": "paragraph", "text": "a\x00b\x07c\r\nd"}]))
    assert doc.blocks[0].text == "abc\nd"


def test_image_key_must_be_declared():
    with pytest.raises(ValidationError, match="not declared"):
        PostDocument.model_validate(make_doc([{"type": "image", "key": "nope"}]))
    with pytest.raises(ValidationError, match="not declared"):
        PostDocument.model_validate(
            make_doc(
                [
                    {
                        "type": "news_card",
                        "title": "t",
                        "url": "https://example.com/a",
                        "source": "s",
                        "summary": "x",
                        "image_key": "nope",
                    }
                ]
            )
        )


@pytest.mark.parametrize("filename", ["a.svg", "a.webp", "A.png", "../a.png", "a.png.exe", "a b.png"])
def test_asset_filenames_only_png_jpg_gif(filename):
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([P], assets=[{"key": "a", "filename": filename}]))


def test_duplicate_asset_keys_and_filenames_rejected():
    with pytest.raises(ValidationError, match="unique"):
        PostDocument.model_validate(
            make_doc([P], assets=[{"key": "a", "filename": "a.png"}, {"key": "a", "filename": "b.png"}])
        )
    with pytest.raises(ValidationError, match="unique"):
        PostDocument.model_validate(
            make_doc([P], assets=[{"key": "a", "filename": "a.png"}, {"key": "b", "filename": "a.png"}])
        )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "ftp://example.com/a",
        "//example.com/a",
        "https://user:pw@example.com/a",
        "https://example.com/a b",
        'https://example.com/"onmouseover=x',
        "https://",
        "",
    ],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "link", "text": "x", "url": url}]))


def test_table_rows_must_match_columns_and_reject_bools():
    cols = [{"label": "a"}, {"label": "b"}]
    with pytest.raises(ValidationError, match="cells"):
        PostDocument.model_validate(make_doc([{"type": "table", "columns": cols, "rows": [["x"]]}]))
    with pytest.raises(ValidationError, match="boolean"):
        PostDocument.model_validate(make_doc([{"type": "table", "columns": cols, "rows": [["x", True]]}]))
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "table", "columns": cols, "rows": [["x", float("nan")]]}]))


def test_table_column_width_and_style_values_are_restricted():
    base = {"type": "table", "columns": [{"label": "a", "width": "40%"}], "rows": [["x"]]}
    PostDocument.model_validate(make_doc([base]))
    for bad_width in ["40", "calc(1px)", "40%; color:red", "1000%"]:
        bad = copy.deepcopy(base)
        bad["columns"][0]["width"] = bad_width
        with pytest.raises(ValidationError):
            PostDocument.model_validate(make_doc([bad]))
    bad = copy.deepcopy(base)
    bad["style"] = {"header_tone": "red"}  # only semantic names are allowed, never raw colours
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([bad]))


def test_news_card_date_and_tags():
    card = {
        "type": "news_card",
        "title": "t",
        "url": "https://example.com/a",
        "source": "s",
        "summary": "x",
    }
    PostDocument.model_validate(make_doc([{**card, "published_date": "2026-10-14"}]))
    for bad in ["2026-13-01", "10/14/2026", "2026-1-4", "20261014"]:
        with pytest.raises(ValidationError):
            PostDocument.model_validate(make_doc([{**card, "published_date": bad}]))
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{**card, "tags": ["a"] * 6}]))
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{**card, "tags": ["x" * 17]}]))


def test_callout_tone_must_be_semantic():
    with pytest.raises(ValidationError):
        PostDocument.model_validate(make_doc([{"type": "callout", "tone": "#ff0000", "text": "x"}]))


def test_json_schema_can_be_generated():
    schema = PostDocument.model_json_schema()
    assert "blocks" in schema["properties"]
