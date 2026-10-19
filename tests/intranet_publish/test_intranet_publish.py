"""IntranetPublisher with in-memory store, fake Blob and fake device client (no network, no Mongo)."""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import integrations.intranet_publish as pkg
from integrations.intranet_publish import IntranetPublisher, PublishError, PublishSettings
from integrations.intranet_publish.blob import AzureImageBlobStore, BlobConfigError
from integrations.intranet_publish.service import describe
from integrations.intranet_publish.store import MemoryPublishStore
from integrations.intranet_publish.validation import extract_reason, make_job_id


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
NAMES = ["홍길동", "김철수"]


def run(coro):
    return asyncio.run(coro)


SAMPLE_BODY = (
    '<div style="font-family:sans-serif;font-size:14px">'
    '<div><img src="{{img:header}}" alt="header" style="max-width:100%"></div>'
    '<div>본문</div>'
    '<div><img src="{{img:issue1}}" alt="issue" style="max-width:100%"></div>'
    '<div><img src="{{img:chart-keywords}}" alt="chart" style="max-width:100%"></div>'
    "</div>"
)
SAMPLE_IMAGES = [
    {"key": "header", "filename": "header.png"},
    {"key": "issue1", "filename": "issue1.png"},
    {"key": "chart-keywords", "filename": "chart-keywords.png"},
]


def sample(board="genai", **changes):
    doc = {
        "post_key": "ai-weekly-2026-W42",
        "board": board,
        "title": "[AI 위클리] 10월 3주차 (샘플)",
        "body_html": SAMPLE_BODY,
        "images": [dict(i) for i in SAMPLE_IMAGES],
    }
    doc.update(changes)
    return doc


def assets_for(doc):
    return {a["key"]: (JPG if a["filename"].endswith((".jpg", ".jpeg")) else PNG) for a in doc["images"]}


class FakeBlob:
    """files = what is in Blob; uploads = only what the publisher itself uploaded (must stay empty)."""

    def __init__(self):
        self.uploads = {}
        self.files = {}
        self.minted = 0

    async def upload(self, path, data, content_type):
        self.uploads[path] = (data, content_type)
        self.files[path] = data

    async def head(self, path, length=16):
        data = self.files.get(path)
        return None if data is None else (len(data), bytes(data[:length]) if isinstance(data, (bytes, bytearray)) else b"")

    def read_url(self, path, ttl):
        self.minted += 1
        return f"https://acct.blob.core.windows.net/wm-device-agent-ai/{path}?sig=SECRETSIG{self.minted}"


class FakeDevice:
    def __init__(self, errors=None):
        self.requests = []
        self.errors = list(errors or [])

    async def submit(self, request):
        self.requests.append(request.model_dump())
        if self.errors:
            raise self.errors.pop(0)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


async def no_sleep(_seconds):
    return None


def status_error(code):
    request = httpx.Request("POST", "https://device.example/device-job")
    return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))


@pytest.fixture()
def env():
    class Env:
        pass

    e = Env()
    e.store = MemoryPublishStore()
    e.blob = FakeBlob()
    e.device = FakeDevice()
    e.clock = Clock()
    e.notified = []

    async def notifier(record):
        e.notified.append(record)

    e.publisher = IntranetPublisher(
        store=e.store, blob=e.blob, device=e.device, settings=PublishSettings(), notifier=notifier,
        sleep=no_sleep, clock=e.clock,
    )
    return e


def publish(env, doc=None, **kw):
    doc = doc or sample()
    assets = kw.pop("assets", None)
    if assets is None:
        assets = assets_for(doc)
    for key, data in assets.items():  # what the image upload tool would have put into Blob
        image = next(i for i in doc["images"] if i["key"] == key)
        env.blob.files[f"intranet-board/{doc['post_key']}/{image['filename']}"] = data
    kw.setdefault("thread_id", "user@widemobile.com")
    return run(env.publisher.publish_post(doc, **kw))


def callback(job_id, status="error", reason=None, result=None, error_message=None):
    res = dict(result or {})
    if reason:
        res.setdefault("success", status == "success")
        res["reason"] = reason
    return {"job_id": job_id, "status": status, "result": res, "error_message": error_message or (reason if status == "error" else None)}


def _callback_and_drain(env, cb):
    """Retries are background tasks, so the callback and the wait must share one event loop."""

    async def go():
        response = await env.publisher.handle_callback(cb)
        await env.publisher.drain()
        return response

    return run(go())


# ------------------------------------------------------------------ request shape
def test_publish_checks_images_in_blob_and_sends_the_agreed_request(env):
    result = publish(env)
    doc = sample()
    assert result.status == "pending"
    assert result.job_id == "intranet-genai-ai-weekly-2026-W42"
    assert env.blob.uploads == {}  # the publisher never uploads; the image upload tool did

    (req,) = env.device.requests
    assert req["job_id"] == req["parameters"]["job_id"] == result.job_id
    assert req["task"] == "post_intranet_board"
    assert req["thread_id"] == "user@widemobile.com"
    params = req["parameters"]
    assert params["board"] == "genai" and params["mode"] == "create"
    assert "board_url" not in params and "recipients" not in params
    assert "{{img:header}}" in params["body_html"]
    assert all(i["blob_url"].startswith("https://acct.blob.core.windows.net/wm-device-agent-ai/intranet-board/ai-weekly-2026-W42/") for i in params["images"])
    assert {i["key"] for i in params["images"]} == {a["key"] for a in doc["images"]}

    record = run(env.store.get(result.job_id))
    assert record["status"] == "pending" and record["attempt"] == 1
    assert "SECRETSIG" not in json.dumps(record, default=str)  # SAS never stored
    assert all("data" not in i for i in record["images"])  # bytes never stored


def test_work_board_sends_normalised_recipients(env):
    result = publish(env, sample("work"), recipients=["  홍길동 ", "김 철수", "홍길동", "김  철수"])
    params = env.device.requests[0]["parameters"]
    assert params["board"] == "work"
    assert params["recipients"] == ["홍길동", "김 철수"]
    assert result.recipient_count == 2


def test_post_without_images(env):
    doc = sample("work")
    doc["images"] = []
    doc["body_html"] = "<div>이미지 없는 글</div>"
    publish(env, doc, assets={})
    assert env.device.requests[0]["parameters"]["images"] == []
    assert env.blob.uploads == {}


def test_job_id_fits_the_device_agent_rule():
    job_id = make_job_id("work", "a" * 80)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id)


# ------------------------------------------------------------------ input checks (nothing uploaded or sent)
def _rejected(env, code, *args, **kw):
    with pytest.raises(PublishError) as info:
        publish(env, *args, **kw)
    assert info.value.code == code
    assert env.blob.uploads == {} and env.device.requests == []
    return info.value


def test_genai_refuses_recipients(env):
    _rejected(env, "INVALID_RECIPIENTS", sample("genai"), recipients=["홍길동"])


def test_unknown_board(env):
    _rejected(env, "INVALID_BOARD", sample("ai-weekly"))


@pytest.mark.parametrize(
    "recipients",
    [
        "홍길동",
        [f"사람{i}" for i in range(21)],
        ["가" * 31],
        ["   "],
        ["홍길동;"],
        ["<b>"],
        ['a"b'],
        ["a'b"],
        ["a\x00b"],
        [123],
    ],
)
def test_recipient_rules(env, recipients):
    error = _rejected(env, "INVALID_RECIPIENTS", sample("work"), recipients=recipients)
    for name in recipients if isinstance(recipients, list) else []:
        if isinstance(name, str) and name.strip():
            assert name not in error.message


def test_missing_asset(env):
    doc = sample()
    assets = assets_for(doc)
    assets.pop("header")
    error = _rejected(env, "MISSING_ASSET", doc, assets=assets)
    assert error.details == {"keys": ["header"]}


@pytest.mark.parametrize("data", [JPG, b"", b"GIF89a....", "not bytes", PNG[:4]])
def test_image_signature_must_match_extension(env, data):
    doc = sample()
    assets = assets_for(doc)
    assets["header"] = data
    _rejected(env, "INVALID_IMAGE", doc, assets=assets)


def test_image_too_large(env):
    doc = sample()
    assets = assets_for(doc)
    assets["header"] = PNG + b"\x00" * (10 * 1024 * 1024)
    _rejected(env, "INVALID_IMAGE", doc, assets=assets)


def test_invalid_document(env):
    _rejected(env, "INVALID_DOCUMENT", {"title": "x"}, assets={})


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "<style>td{color:red}</style>",
        '<div onclick="x()">a</div>',
        '<a href="javascript:alert(1)">a</a>',
        "<iframe src=x></iframe>",
        "<form></form>",
    ],
)
def test_unsafe_body_is_never_sent(env, bad):
    doc = sample()
    doc["body_html"] += bad
    _rejected(env, "UNSAFE_BODY", doc)


@pytest.mark.parametrize(
    "body",
    [
        '<img src="https://evil.example/x.png">',
        '<img src="data:image/png;base64,AAAA">',
        '<img alt="x">',
        '<div>{{img:unknown}}</div>',
        "   ",
    ],
)
def test_body_image_rules(env, body):
    doc = sample()
    doc["body_html"] = body
    doc["images"] = []
    _rejected(env, "INVALID_DOCUMENT", doc, assets={})


def test_undeclared_placeholder_is_rejected(env):
    doc = sample()
    doc["body_html"] += '<img src="{{img:other}}">'
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_body_size_limit(env):
    doc = sample()
    doc["body_html"] = "<div>" + "가" * 300_000 + "</div>"
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_unused_image_is_uploaded_with_a_warning(env):
    doc = sample()
    doc["body_html"] = '<div><img src="{{img:header}}" alt="h"></div>'
    result = publish(env, doc)
    assert any("issue1" in w for w in result.warnings)
    assert len(env.device.requests[0]["parameters"]["images"]) == 3


def test_duplicate_image_keys_or_files_are_rejected(env):
    doc = sample()
    doc["images"].append({"key": "header", "filename": "other.png"})
    _rejected(env, "INVALID_DOCUMENT", doc)
    doc = sample()
    doc["images"].append({"key": "other", "filename": "header.png"})
    _rejected(env, "INVALID_DOCUMENT", doc)


def test_unknown_fields_are_rejected(env):
    doc = sample()
    doc["theme"] = "default"
    _rejected(env, "INVALID_DOCUMENT", doc)


# ------------------------------------------------------------------ submit errors
def test_connection_errors_are_retried_then_fail(env):
    env.device.errors = [httpx.ConnectError("down")] * 3
    with pytest.raises(PublishError):
        publish(env)
    assert len(env.device.requests) == 3
    record = run(env.store.get("intranet-genai-ai-weekly-2026-W42"))
    assert record["status"] == "failed" and record["reason"] == "DEVICE_REQUEST_FAILED"
    assert len(env.notified) == 1


def test_connection_error_then_success(env):
    env.device.errors = [httpx.ConnectTimeout("slow")]
    assert publish(env).status == "pending"
    assert len(env.device.requests) == 2


def test_rejected_request_fails_without_retry(env):
    env.device.errors = [status_error(401)]
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "DEVICE_REQUEST_REJECTED"
    assert len(env.device.requests) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), status_error(500)])
def test_ambiguous_submit_waits_for_the_callback(env, error):
    env.device.errors = [error]
    assert publish(env).status == "pending"
    assert len(env.device.requests) == 1  # never re-sent: the device_agent may be running it


# ------------------------------------------------------------------ callbacks
def test_success_callback_is_recorded_once(env):
    job_id = publish(env).job_id
    cb = callback(job_id, "success", result={"success": True, "post_id": "526400", "post_url": "https://intra/x?Num=526400", "secret": "drop"})
    assert run(env.publisher.handle_callback(cb))["status"] == "succeeded"
    assert run(env.publisher.handle_callback(cb))["status"] == "duplicate"
    record = run(env.store.get(job_id))
    assert record["status"] == "succeeded" and record["result"]["post_id"] == "526400"
    assert "secret" not in record["result"]
    assert len(env.notified) == 1
    assert "526400" in describe(record)


@pytest.mark.parametrize("reason", ["LOGIN_REQUIRED", "IMAGE_UPLOAD_FAILED", "SUBMIT_FAILED"])
def test_retryable_failures_are_resent_with_fresh_sas_then_give_up(env, reason):
    job_id = publish(env).job_id
    first_urls = [i["blob_url"] for i in env.device.requests[0]["parameters"]["images"]]

    assert _callback_and_drain(env, callback(job_id, reason=reason))["status"] == "retry_wait"
    assert len(env.device.requests) == 2
    second = env.device.requests[1]
    assert second["job_id"] == job_id  # same job id: the ledger stays consistent
    assert [i["blob_url"] for i in second["parameters"]["images"]] != first_urls
    assert run(env.store.get(job_id))["attempt"] == 2

    _callback_and_drain(env, callback(job_id, reason=reason))
    assert len(env.device.requests) == 3
    result = run(env.publisher.handle_callback(callback(job_id, reason=reason)))
    assert result["status"] == "failed"
    assert len(env.device.requests) == 3
    assert len(env.notified) == 1


@pytest.mark.parametrize(
    "reason",
    ["BODY_REJECTED", "PLACEHOLDER_UNRESOLVED", "EDITOR_NOT_FOUND", "INVALID_PARAMS", "RECIPIENT_NOT_FOUND", "RECIPIENT_AMBIGUOUS", "RECIPIENT_FAILED"],
)
def test_final_failures_are_not_retried(env, reason):
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    result = _callback_and_drain(env, callback(job_id, reason=reason))
    assert result["status"] == "failed"
    assert len(env.device.requests) == 1


def test_recipient_errors_are_passed_by_index_only(env, caplog):
    caplog.set_level(logging.DEBUG)
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    errors = [{"index": 1, "reason": "AMBIGUOUS"}]
    run(env.publisher.handle_callback(callback(job_id, reason="RECIPIENT_AMBIGUOUS", result={"recipient_errors": errors})))
    record = run(env.store.get(job_id))
    assert record["result"]["recipient_errors"] == errors
    text = describe(record)
    assert "1번(AMBIGUOUS)" in text
    for name in NAMES:
        assert name not in text and name not in caplog.text


@pytest.mark.parametrize(
    "cb",
    [
        lambda j: callback(j, reason="SUBMIT_UNCERTAIN"),
        lambda j: callback(j, reason="VERIFY_FAILED"),
        lambda j: callback(j, reason="SOMETHING_NEW"),
        lambda j: {"job_id": j, "status": "error", "result": {}, "error_message": "Traceback: boom"},
    ],
)
def test_uncertain_or_unknown_needs_a_person(env, cb):
    job_id = publish(env).job_id
    result = _callback_and_drain(env, cb(job_id))
    assert result["status"] == "needs_check"
    assert len(env.device.requests) == 1
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "NEEDS_MANUAL_CHECK"


def test_callback_timeout_then_late_success(env):
    job_id = publish(env).job_id
    env.clock.now += timedelta(minutes=11)
    assert run(env.publisher.expire_stale()) == 1
    record = run(env.store.get(job_id))
    assert (record["status"], record["reason"]) == ("needs_check", "CALLBACK_TIMEOUT")
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True, "post_id": "1"})))
    assert run(env.store.get(job_id))["status"] == "succeeded"


def test_callback_timeout_then_late_pre_click_failure(env):
    job_id = publish(env).job_id
    env.clock.now += timedelta(minutes=11)
    run(env.publisher.handle_callback(callback(job_id, reason="LOGIN_REQUIRED")))
    record = run(env.store.get(job_id))
    assert (record["status"], record["reason"]) == ("failed", "LOGIN_REQUIRED")
    assert len(env.device.requests) == 1  # no automatic retry after a timeout


def test_not_timed_out_yet(env):
    publish(env)
    env.clock.now += timedelta(minutes=9)
    assert run(env.publisher.expire_stale()) == 0


# ------------------------------------------------------------------ publishing the same post again
def test_republish_after_success_does_not_send(env):
    job_id = publish(env).job_id
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True})))
    assert publish(env).status == "succeeded"
    assert len(env.device.requests) == 1


def test_republish_while_in_progress_is_refused(env):
    publish(env)
    with pytest.raises(PublishError) as info:
        publish(env)
    assert info.value.code == "IN_PROGRESS"


def test_republish_after_input_failure_is_allowed(env):
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    run(env.publisher.handle_callback(callback(job_id, reason="RECIPIENT_NOT_FOUND")))
    publish(env, sample("work"), recipients=["홍길동"])
    assert len(env.device.requests) == 2
    assert run(env.store.get(job_id))["attempt"] == 1


# ------------------------------------------------------------------ secrets in logs
def test_no_secrets_or_names_in_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    job_id = publish(env, sample("work"), recipients=NAMES).job_id
    _callback_and_drain(env, callback(job_id, reason="LOGIN_REQUIRED"))
    assert len(env.device.requests) == 2
    run(env.publisher.handle_callback(callback(job_id, "success", result={"success": True, "post_id": "9"})))
    assert "SECRETSIG" not in caplog.text
    for name in NAMES:
        assert name not in caplog.text
    assert job_id in caplog.text


# ------------------------------------------------------------------ misc
def test_extract_reason():
    assert extract_reason({"result": {"reason": "LOGIN_REQUIRED"}}) == "LOGIN_REQUIRED"
    assert extract_reason({"result": {}, "error_message": "VERIFY_FAILED"}) == "VERIFY_FAILED"
    assert extract_reason({"result": {}, "error_message": "알 수 없는 job_type: x"}) == "UNKNOWN"
    assert extract_reason({}) == "UNKNOWN"


def test_try_handle_callback_ignores_other_jobs(monkeypatch):
    def boom():
        raise AssertionError("must not build the publisher for other jobs")

    monkeypatch.setattr(pkg, "get_publisher", boom)
    assert run(pkg.try_handle_callback({"job_id": "3f0c-uuid", "status": "success"})) is None


def test_try_handle_callback_routes_owned_jobs(env, monkeypatch):
    job_id = publish(env).job_id
    monkeypatch.setattr(pkg, "get_publisher", lambda: env.publisher)
    assert run(pkg.try_handle_callback({"job_id": "intranet-genai-unknown", "status": "success"})) is None
    response = run(pkg.try_handle_callback({"job_id": job_id, "status": "success", "result": {"success": True}}))
    assert response["status"] == "succeeded"


# ------------------------------------------------------------------ Azure SAS (offline: SAS is computed locally)
KEY = base64.b64encode(b"k" * 64).decode()
CONN = f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={KEY};EndpointSuffix=core.windows.net"


def test_sas_is_read_only_https_and_short_lived():
    store = AzureImageBlobStore(CONN, "wm-device-agent-ai")
    assert store.host == "acct.blob.core.windows.net"
    url = store.read_url("intranet-board/ai-weekly-2026-W42/header.png", timedelta(hours=1))
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.hostname == "acct.blob.core.windows.net"
    assert parts.path == "/wm-device-agent-ai/intranet-board/ai-weekly-2026-W42/header.png"
    query = parse_qs(parts.query)
    assert query["sp"] == ["r"] and query["spr"] == ["https"]
    expiry = datetime.strptime(query["se"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(minutes=55) < expiry - datetime.now(timezone.utc) <= timedelta(hours=1)


def test_blob_config_errors_do_not_echo_the_connection_string():
    with pytest.raises(BlobConfigError):
        AzureImageBlobStore("", "c")
    sas_only = "BlobEndpoint=https://acct.blob.core.windows.net/;SharedAccessSignature=sv=2024&sig=TOPSECRET"
    with pytest.raises(BlobConfigError) as info:
        AzureImageBlobStore(sas_only, "c")
    assert "TOPSECRET" not in str(info.value)
