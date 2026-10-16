import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # project root

SAMPLE_PATH = ROOT / "integrations" / "intranet_post" / "samples" / "ai_weekly_sample.json"


@pytest.fixture()
def sample() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def make_doc(blocks, assets=None, **overrides) -> dict:
    doc = {
        "post_key": "test-post-1",
        "series": "test",
        "period": "2026-W01",
        "board": "test-board",
        "title": "테스트",
        "assets": assets or [],
        "blocks": blocks,
    }
    doc.update(overrides)
    return doc
