"""Publish the sample post (manual end-to-end check).

    python scripts/publish_intranet_sample.py                       # render only, nothing uploaded or sent
    python scripts/publish_intranet_sample.py --board work --recipients "홍길동" --confirm --wait
    python scripts/publish_intranet_sample.py --board genai --confirm --confirm-public --wait

Without --confirm the post is only rendered into a temp folder (files are overwritten each run).
With --confirm the images go to Blob and the device job is sent. The callback goes to the BOA
server named in the device_agent's BOA_CALLBACK_URL, so that server must run this code and use
the same MongoDB as this script for --wait to see the result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "integrations" / "intranet_post" / "samples" / "ai_weekly_sample.json"
COLOURS = [(43, 95, 171), (32, 140, 110), (200, 120, 30), (120, 80, 160)]


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Minimal PNG encoder so the script needs no image library."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def sample_assets(doc: dict) -> dict[str, bytes]:
    assets = {}
    for i, asset in enumerate(doc["assets"]):
        if not asset["filename"].lower().endswith(".png"):
            raise SystemExit(f"샘플 이미지 '{asset['key']}'는 png가 아니라 자동 생성할 수 없습니다.")
        size = (1200, 240) if asset["key"] == "header" else (800, 400)
        assets[asset["key"]] = solid_png(*size, COLOURS[i % len(COLOURS)])
    return assets


def render_only(doc: dict) -> None:
    from integrations.intranet_post import render_post
    from integrations.intranet_post.preview import build_preview_html

    rendered = render_post(doc)
    out = Path(tempfile.gettempdir()) / "intranet_post_preview"
    out.mkdir(exist_ok=True)
    (out / "body.html").write_text(rendered.body_html, encoding="utf-8")
    (out / "preview.html").write_text(build_preview_html(rendered), encoding="utf-8")
    print(f"렌더만 했습니다 (업로드/요청 없음). board={rendered.board} images={len(rendered.images)}")
    print(f"미리보기: {out / 'preview.html'}")
    for warning in rendered.warnings:
        print(f"경고: {warning}")


async def publish(doc: dict, args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from integrations.device_job.client import close_device_job_client, init_device_job_client
    from integrations.intranet_publish import PublishError, get_publisher

    await init_device_job_client()
    publisher = get_publisher()
    try:
        try:
            result = await publisher.publish_post(
                doc,
                assets=sample_assets(doc),
                recipients=args.recipients,
                thread_id="intranet-publish-script",
            )
        except PublishError as exc:
            print(f"요청 전 중단: {exc.code} {exc.message}")
            if exc.details:
                print(json.dumps(exc.details, ensure_ascii=False))
            return 2
        print(f"전송: job_id={result.job_id} status={result.status} images={result.image_count} recipients={result.recipient_count}")
        if not args.wait or result.status != "pending":
            return 0

        deadline = time.monotonic() + args.wait_minutes * 60
        last = None
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            status = await publisher.get_status(result.job_id) or {}
            if status.get("status") != last:
                last = status.get("status")
                print(f"  상태: {last} {status.get('reason') or ''}".rstrip())
            if last in ("succeeded", "failed", "needs_check"):
                print(json.dumps(status.get("result") or {}, ensure_ascii=False, indent=2))
                return 0 if last == "succeeded" else 1
        print("기다리는 시간이 지났습니다. 콜백을 받는 BOA 서버에 이 코드가 배포됐는지, 같은 MongoDB를 쓰는지 확인하세요.")
        return 1
    finally:
        await close_device_job_client()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", type=Path, default=SAMPLE, help="게시 문서 JSON (기본: 샘플)")
    parser.add_argument("--board", choices=["work", "genai"], default="work")
    parser.add_argument("--recipients", type=lambda s: [x for x in s.split(",") if x.strip()], default=None,
                        help="쉼표로 구분한 이름 (work 전용)")
    parser.add_argument("--post-key", help="post_key 덮어쓰기 (같은 글을 다시 올릴 때)")
    parser.add_argument("--confirm", action="store_true", help="실제로 업로드하고 게시 요청")
    parser.add_argument("--confirm-public", action="store_true", help="공개 게시판(genai)에 올릴 때 필요")
    parser.add_argument("--wait", action="store_true", help="콜백 결과까지 기다림")
    parser.add_argument("--wait-minutes", type=float, default=12)
    args = parser.parse_args()

    doc = json.loads(args.doc.read_text(encoding="utf-8"))
    doc["board"] = args.board  # the file is not modified
    if args.post_key:
        doc["post_key"] = args.post_key
    if not doc["title"].startswith("[테스트]"):  # samples are fake news; never look real on a shared board
        doc["title"] = f"[테스트] {doc['title']}"

    if not args.confirm:
        render_only(doc)
        return 0
    if args.board == "genai" and not args.confirm_public:
        print("genai는 전 직원 공개 게시판입니다. 정말 올리려면 --confirm-public을 같이 주세요.")
        return 2
    return asyncio.run(publish(doc, args))


if __name__ == "__main__":
    raise SystemExit(main())
