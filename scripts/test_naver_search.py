"""NAVER API HUB 검색 API 연결 테스트 (다른 코드는 건드리지 않는 단독 스크립트).

    python scripts/test_naver_search.py
    python scripts/test_naver_search.py --query "생성형 AI" --display 3

.env 의 NAVER_API_CLIENT_ID / NAVER_API_CLIENT_SECRET 으로 뉴스, 블로그, 웹문서, 카페글을 한 번씩 검색해서
각각 되는지 보여 줍니다. 키 값은 출력하지 않습니다.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://naverapihub.apigw.ntruss.com/search/v1"
ENDPOINTS = [("news", "뉴스"), ("blog", "블로그"), ("webkr", "웹문서"), ("cafearticle", "카페글")]


def load_env(path: Path) -> dict[str, str]:
    """Minimal .env reader so the script needs no extra packages."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def clean(text: str) -> str:
    return html.unescape(re.sub(r"</?b>", "", text or "")).strip()


def call(kind: str, client_id: str, client_secret: str, query: str, display: int) -> tuple[int | None, dict | str]:
    params = urllib.parse.urlencode({"query": query, "display": display, "start": 1, "sort": "date" if kind == "news" else "sim"})
    request = urllib.request.Request(f"{BASE_URL}/{kind}?{params}", method="GET")
    request.add_header("X-NCP-APIGW-API-KEY-ID", client_id)
    request.add_header("X-NCP-APIGW-API-KEY", client_secret)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:400]
    except Exception as exc:  # network, DNS, timeout, bad JSON
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--query", default="생성형 AI")
    parser.add_argument("--display", type=int, default=3)
    args = parser.parse_args()

    env = {**load_env(ROOT / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("NAVER_")}}
    client_id = env.get("NAVER_API_CLIENT_ID", "")
    client_secret = env.get("NAVER_API_CLIENT_SECRET", "")
    print(f"NAVER_API_CLIENT_ID     : {'설정됨 (%d자)' % len(client_id) if client_id else '없음'}")
    print(f"NAVER_API_CLIENT_SECRET : {'설정됨 (%d자)' % len(client_secret) if client_secret else '없음'}")
    if not client_id or not client_secret:
        print("\n.env에 두 값이 없습니다. 이름이 정확한지, 따옴표나 공백이 섞이지 않았는지 확인하세요.")
        return 2
    if client_id != client_id.strip() or client_secret != client_secret.strip():
        print("경고: 키 앞뒤에 공백이 있습니다.")

    print(f"\n검색어: {args.query!r}, display={args.display}\n")
    ok = 0
    for kind, label in ENDPOINTS:
        status, body = call(kind, client_id, client_secret, args.query, args.display)
        if status == 200 and isinstance(body, dict):
            ok += 1
            items = body.get("items", [])
            print(f"[성공] {label} ({kind}): HTTP 200, 전체 {body.get('total')}건 중 {len(items)}건")
            for item in items[:args.display]:
                when = item.get("pubDate") or item.get("postdate") or ""
                print(f"    - {clean(item.get('title', ''))[:60]}  {when}")
        else:
            print(f"[실패] {label} ({kind}): HTTP {status}")
            print(f"    {body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:400]}")
    print(f"\n결과: {ok}/{len(ENDPOINTS)} 성공")
    if ok == 0:
        print("모두 실패했다면: 401/403은 키가 틀렸거나 이 API가 앱에 연결되지 않은 경우, 네트워크 오류는 접속 환경 문제입니다.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
