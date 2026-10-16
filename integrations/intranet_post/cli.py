"""Command line helper.

    python -m integrations.intranet_post render integrations/intranet_post/samples/ai_weekly_sample.json --out-dir out --screenshot
    python -m integrations.intranet_post schema --out integrations/intranet_post/schema/post_document.schema.json

``render`` writes: <post_key>.body.html (what the device job receives), <post_key>.rendered.json
(body + images + warnings) and <post_key>.preview.html (+ .png with --screenshot).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import PostDocument
from .preview import build_preview_html, safe_stem, screenshot
from .renderer import RenderError, render_post


def _render(args: argparse.Namespace) -> int:
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rendered = render_post(raw)
    except ValidationError as exc:
        print(f"invalid document:\n{exc}", file=sys.stderr)
        return 2
    except RenderError as exc:
        print(f"render error: {exc}", file=sys.stderr)
        return 3

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(rendered.post_key)
    (out / f"{stem}.body.html").write_text(rendered.body_html, encoding="utf-8")
    (out / f"{stem}.rendered.json").write_text(
        json.dumps(rendered.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    preview_path = out / f"{stem}.preview.html"
    preview_path.write_text(build_preview_html(rendered, images_dir=args.images_dir), encoding="utf-8")
    print(f"body: {len(rendered.body_html)} chars, images: {[i.key for i in rendered.images]}")
    for warning in rendered.warnings:
        print(f"warning: {warning}")
    print(f"wrote {out}/{stem}.*")
    if args.screenshot:
        png = out / f"{stem}.preview.png"
        screenshot(preview_path, png)
        print(f"screenshot: {png}")
    return 0


def _schema(args: argparse.Namespace) -> int:
    text = json.dumps(PostDocument.model_json_schema(), ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="intranet_post")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render a PostDocument JSON file")
    render.add_argument("input")
    render.add_argument("--out-dir", default="out")
    render.add_argument("--images-dir", default=None, help="folder with the real image files (optional)")
    render.add_argument("--screenshot", action="store_true", help="also save a PNG of the preview (needs playwright)")
    render.set_defaults(func=_render)

    schema = sub.add_parser("schema", help="print/write the JSON Schema of PostDocument")
    schema.add_argument("--out", default=None)
    schema.set_defaults(func=_schema)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
