"""LangChain Tool 래퍼: 거부 결과가 예외가 아니라 도구 결과(dict)로 돌아가는지."""
import asyncio

import pytest

import tools.common.intranet_publish.tool as tool_mod
from integrations.intranet_publish import IntranetPublisher, PublishSettings
from integrations.intranet_publish.store import MemoryPublishStore

CFG = {"configurable": {"thread_id": "t-1", "space_id": "spaces/AAA", "google_thread_name": "spaces/AAA/threads/BBB"}}
GOOD = "<div style=\"font-size:14px\">본문</div>"


class FakeBlob:
    async def upload(self, *a):
        pass

    def read_url(self, path, ttl):
        return "https://x/y?sig=1"


class FakeDevice:
    def __init__(self):
        self.requests = []

    async def submit(self, request):
        self.requests.append(request.model_dump())


@pytest.fixture()
def publisher(monkeypatch):
    pub = IntranetPublisher(store=MemoryPublishStore(), blob=FakeBlob(), device=FakeDevice(), settings=PublishSettings())
    monkeypatch.setattr(tool_mod, "get_publisher", lambda: pub)
    return pub


def call(**kw):
    kw.setdefault("post_key", "p-1")
    kw.setdefault("board", "genai")
    kw.setdefault("title", "제목")
    kw.setdefault("body_html", GOOD)
    return asyncio.run(tool_mod.publish_intranet_post.ainvoke(kw, config=CFG))


def test_success_returns_pending_job(publisher):
    out = call()
    assert out["success"] is True and out["status"] == "pending" and out["job_id"] == "intranet-genai-p-1"
    (req,) = publisher.device.requests
    assert req["thread_id"] == "t-1" and req["parameters"]["body_html"] == GOOD
    record = asyncio.run(publisher.store.get(out["job_id"]))
    assert record["notify"] == {"space_id": "spaces/AAA", "thread_name": "spaces/AAA/threads/BBB"}


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"body_html": GOOD + "<script>alert(1)</script>"}, "UNSAFE_BODY"),
        ({"body_html": GOOD + '<img src="{{img:x}}">'}, "INVALID_DOCUMENT"),
        ({"board": "nope"}, "INVALID_BOARD"),
        ({"board": "genai", "recipients": ["홍길동"]}, "INVALID_RECIPIENTS"),
        ({"post_key": "bad key!"}, "INVALID_DOCUMENT"),
    ],
)
def test_rejection_is_a_tool_result_not_an_exception(publisher, kw, code):
    out = call(**kw)
    assert out["success"] is False and out["error"]["code"] == code and out["error"]["message"]
    assert publisher.device.requests == []


def test_status_tool(publisher):
    job_id = call()["job_id"]
    out = asyncio.run(tool_mod.check_intranet_post_status.ainvoke({"job_id": job_id}))
    assert out["success"] and out["status"] == "pending"
    out = asyncio.run(tool_mod.check_intranet_post_status.ainvoke({"job_id": "intranet-genai-none"}))
    assert out["success"] is False and out["error"]["code"] == "NOT_FOUND"


def test_missing_thread_id(publisher):
    out = asyncio.run(tool_mod.publish_intranet_post.ainvoke(
        {"post_key": "p-2", "board": "genai", "title": "t", "body_html": GOOD}, config={"configurable": {}}))
    assert out["success"] is False and out["error"]["code"] == "NO_THREAD"
