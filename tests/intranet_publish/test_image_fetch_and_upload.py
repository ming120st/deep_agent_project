"""이미지 다운로드(내부 주소 차단)와 업로드 Tool, 그리고 게시 Tool의 images 처리."""
import asyncio

import httpx
import pytest

import integrations.intranet_publish.fetch as fetch
import tools.common.intranet_publish.tool as tool_mod
from integrations.intranet_publish import IntranetPublisher, PublishError, PublishSettings
from integrations.intranet_publish.store import MemoryPublishStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CFG = {"configurable": {"thread_id": "t-1"}}


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- URL guard
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.png",
        "http://localhost/a.png",
        "http://10.0.0.5/a.png",
        "http://192.168.1.1/a.png",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a.png",
        "https://example.com:8080/a.png",
        "ftp://example.com/a.png",
        "file:///etc/passwd",
        "https://user:pw@example.com/a.png",
        "not a url",
    ],
)
def test_check_url_blocks_unsafe_addresses(url):
    with pytest.raises(PublishError) as info:
        run(fetch.check_url(url))
    assert info.value.code in ("INVALID_IMAGE_URL", "IMAGE_DOWNLOAD_FAILED")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture()
def public(monkeypatch):
    async def ok(url):
        if "internal" in url:
            raise PublishError("INVALID_IMAGE_URL", "내부 또는 비공개 주소는 사용할 수 없습니다.")

    monkeypatch.setattr(fetch, "check_url", ok)


def test_fetch_returns_bytes_in_memory(public):
    async def go():
        async with _client(lambda r: httpx.Response(200, content=PNG)) as c:
            return await fetch.fetch_image("https://img.example/a.png", client=c)

    assert run(go()) == PNG


def test_fetch_follows_redirect_but_rechecks_target(public):
    def handler(request):
        if request.url.host == "img.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        return httpx.Response(200, content=PNG)

    async def go():
        async with _client(handler) as c:
            await fetch.fetch_image("https://img.example/a.png", client=c)

    with pytest.raises(PublishError) as info:
        run(go())
    assert info.value.code == "INVALID_IMAGE_URL"


def test_fetch_limits(public):
    async def get(handler):
        async with _client(handler) as c:
            return await fetch.fetch_image("https://img.example/a.png", client=c)

    with pytest.raises(PublishError) as info:
        run(get(lambda r: httpx.Response(200, content=b"\x00" * (10 * 1024 * 1024 + 1))))
    assert info.value.code == "INVALID_IMAGE"
    with pytest.raises(PublishError) as info:
        run(get(lambda r: httpx.Response(404)))
    assert info.value.code == "IMAGE_DOWNLOAD_FAILED"
    with pytest.raises(PublishError) as info:  # redirect loop
        run(get(lambda r: httpx.Response(302, headers={"location": "https://img.example/a.png"})))
    assert info.value.code == "IMAGE_DOWNLOAD_FAILED"


# ----------------------------------------------------------------- tools
class FakeBlob:
    def __init__(self):
        self.files = {}

    async def upload(self, path, data, content_type):
        self.files[path] = (data, content_type)

    async def head(self, path, length=16):
        item = self.files.get(path)
        return None if item is None else (len(item[0]), item[0][:length])

    def read_url(self, path, ttl):
        return f"https://x/{path}?sig=1"


class FakeDevice:
    def __init__(self):
        self.requests = []

    async def submit(self, request):
        self.requests.append(request.model_dump())


@pytest.fixture()
def env(monkeypatch):
    pub = IntranetPublisher(store=MemoryPublishStore(), blob=FakeBlob(), device=FakeDevice(), settings=PublishSettings())
    monkeypatch.setattr(tool_mod, "get_publisher", lambda: pub)

    async def fake_fetch(url):
        if "bad" in url:
            raise PublishError("IMAGE_DOWNLOAD_FAILED", "이미지를 받지 못했습니다 (HTTP 404).")
        return PNG

    monkeypatch.setattr(tool_mod, "fetch_image", fake_fetch)
    return pub


def upload(**kw):
    kw.setdefault("post_key", "p-1")
    kw.setdefault("filename", "header.png")
    kw.setdefault("image_url", "https://img.example/a.png")
    return run(tool_mod.upload_intranet_image.ainvoke(kw))


def publish(**kw):
    kw.setdefault("post_key", "p-1")
    kw.setdefault("board", "genai")
    kw.setdefault("title", "제목")
    kw.setdefault("body_html", '<div><img src="{{img:header}}" alt="h"></div>')
    return run(tool_mod.publish_intranet_post.ainvoke(kw, config=CFG))


def test_upload_then_publish_with_images(env):
    out = upload()
    assert out == {"success": True, "filename": "header.png", "size": len(PNG)}
    assert list(env.blob.files) == ["intranet-board/p-1/header.png"]
    assert env.blob.files["intranet-board/p-1/header.png"][1] == "image/png"

    out = publish(images=[{"key": "header", "filename": "header.png"}])
    assert out["success"] is True and out["status"] == "pending"
    (req,) = env.device.requests
    (image,) = req["parameters"]["images"]
    assert image["key"] == "header" and "intranet-board/p-1/header.png" in image["blob_url"]


def test_publish_without_upload_returns_missing_asset_result(env):
    out = publish(images=[{"key": "header", "filename": "header.png"}])
    assert out["success"] is False and out["error"]["code"] == "MISSING_ASSET"
    assert "업로드 Tool" in out["error"]["message"] and env.device.requests == []


def test_publish_cannot_point_at_another_posts_images(env):
    upload(post_key="other-post")
    out = publish(post_key="p-1", images=[{"key": "header", "filename": "header.png"}])
    assert out["error"]["code"] == "MISSING_ASSET"


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"image_url": "https://bad.example/a.png"}, "IMAGE_DOWNLOAD_FAILED"),
        ({"filename": "x.svg"}, "INVALID_DOCUMENT"),
        ({"filename": "../x.png"}, "INVALID_DOCUMENT"),
        ({"post_key": "a/b"}, "INVALID_DOCUMENT"),
        ({"filename": "x.jpg"}, "INVALID_IMAGE"),  # png content with a jpg name
    ],
)
def test_upload_failures_are_tool_results(env, kw, code):
    out = upload(**kw)
    assert out["success"] is False and out["error"]["code"] == code
    assert env.blob.files == {}


def test_already_posted_post_is_not_blocked_by_expired_images(env):
    upload()
    first = publish(images=[{"key": "header", "filename": "header.png"}])
    run(env.handle_callback({"job_id": first["job_id"], "status": "success", "result": {"post_id": "1"}}))
    env.blob.files.clear()  # Blob lifecycle rule removed the images
    again = publish(images=[{"key": "header", "filename": "header.png"}])
    assert again["success"] is True and again["status"] == "succeeded"
