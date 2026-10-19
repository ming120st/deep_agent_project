"""Publish a sample post (manual end-to-end check). The body is plain HTML written by hand or by an agent.

    python scripts/publish_intranet_sample.py                       # check only, nothing uploaded or sent
    python scripts/publish_intranet_sample.py --html my_body.html   # check your own body
    python scripts/publish_intranet_sample.py --board work --recipients "홍길동" --confirm --wait
    python scripts/publish_intranet_sample.py --board genai --confirm --confirm-public --wait

Without --confirm the post is only validated (same checks as the publisher). With --confirm the
images are put into Blob (like the image upload tool does) and the device job is sent. Every ``{{img:key}}`` in the body gets a generated
solid-colour PNG named ``key.png``. The callback goes to the BOA server named in the device_agent's
BOA_CALLBACK_URL, so that server must run this code and use the same MongoDB as this script for --wait.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COLOURS = [(43, 95, 171), (32, 140, 110), (200, 120, 30), (120, 80, 160)]
FONT = "font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif"
SAMPLE_BODY = f"""<div style="{FONT};font-size:14px;line-height:1.6;color:#222222;max-width:860px">
<div style="margin:0 0 14px 0;text-align:center"><img src="{{{{img:header}}}}" alt="헤더" style="max-width:100%;height:auto;border:0;vertical-align:middle"></div>
<div style="margin:0 0 14px 0;padding:12px 16px;background:#e8f1fb;border:1px solid #b8d3f0;border-left:5px solid #2b5fab;border-radius:6px;color:#174a7e;font-size:14px;line-height:1.65"><div style="margin:0 0 4px 0;font-weight:700;font-size:14px">샘플 게시글</div><div style="margin:0">HTML 직접 게시 확인용 글입니다.</div></div>
<h2 style="margin:30px 0 14px 0;padding:0 0 8px 0;{FONT};font-size:20px;line-height:1.35;font-weight:700;color:#1d457f;border-bottom:2px solid #2b5fab">이미지 확인</h2>
<div style="margin:0 0 14px 0;text-align:center"><img src="{{{{img:issue1}}}}" alt="이슈 1" style="max-width:100%;height:auto;border:0;vertical-align:middle"></div>
<div style="margin:0 0 14px 0;text-align:center"><img src="{{{{img:chart}}}}" alt="차트" style="max-width:100%;height:auto;border:0;vertical-align:middle"></div>
</div>"""


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Minimal PNG encoder so the script needs no image library."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def build_post(args: argparse.Namespace) -> tuple[dict, dict[str, bytes]]:
    body = args.html.read_text(encoding="utf-8") if args.html else SAMPLE_BODY
    keys = list(dict.fromkeys(re.findall(r"\{\{img:([a-z0-9-]+)\}\}", body)))
    title = args.title
    if args.board == "work" and not title.startswith("[테스트]"):
        title = f"[테스트] {title}"
    post = {
        "post_key": args.post_key,
        "board": args.board,
        "title": title,
        "body_html": body,
        "images": [{"key": k, "filename": f"{k}.png"} for k in keys],
    }
    assets = {k: solid_png(*((1200, 240) if k == "header" else (800, 400)), COLOURS[i % len(COLOURS)]) for i, k in enumerate(keys)}
    return post, assets


async def publish(post: dict, assets: dict[str, bytes], args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from integrations.device_job.client import close_device_job_client, init_device_job_client
    from integrations.intranet_publish import PublishError, get_publisher

    await init_device_job_client()
    publisher = get_publisher()
    try:
        try:
            for image in post["images"]:  # what upload_intranet_image does for an agent
                await publisher.blob.upload(f"intranet-board/{post['post_key']}/{image['filename']}", assets[image["key"]], "image/png")
            result = await publisher.publish_post(post, recipients=args.recipients, thread_id="intranet-publish-script")
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
    parser.add_argument("--html", type=Path, help="본문 HTML 파일 (기본: 내장 샘플)")
    parser.add_argument("--title", default="인트라넷 HTML 게시 샘플")
    parser.add_argument("--board", choices=["work", "genai"], default="work")
    parser.add_argument("--recipients", type=lambda s: [x for x in s.split(",") if x.strip()], default=None,
                        help="쉼표로 구분한 이름 (work 전용)")
    parser.add_argument("--post-key", default="sample-html-1", help="같은 글을 다시 올릴 때는 바꾸세요")
    parser.add_argument("--confirm", action="store_true", help="실제로 업로드하고 게시 요청")
    parser.add_argument("--confirm-public", action="store_true", help="공개 게시판(genai)에 올릴 때 필요")
    parser.add_argument("--wait", action="store_true", help="콜백 결과까지 기다림")
    parser.add_argument("--wait-minutes", type=float, default=12)
    args = parser.parse_args()

    post, assets = build_post(args)
    if not args.confirm:
        from pydantic import ValidationError

        from integrations.intranet_publish.post import parse_post
        from integrations.intranet_publish.safety import is_safe_html

        try:
            parsed = parse_post(post)
        except ValidationError as exc:
            for e in exc.errors():
                print(f"입력 오류: {list(e.get('loc', ()))} {e.get('msg')}")
            return 2
        print(f"검사만 했습니다 (업로드/요청 없음). board={parsed.board} body={len(parsed.body_html)}자 images={len(parsed.images)}")
        print(f"안전 검사: {'통과' if is_safe_html(parsed.body_html) else '실패 (금지된 태그/속성 포함)'}")
        for warning in parsed.warnings:
            print(f"경고: {warning}")
        return 0
    if args.board == "genai" and not args.confirm_public:
        print("genai는 전 직원 공개 게시판입니다. 정말 올리려면 --confirm-public을 같이 주세요.")
        return 2
    return asyncio.run(publish(post, assets, args))


if __name__ == "__main__":
    raise SystemExit(main())
