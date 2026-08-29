"""Render the README dual-mode preview PNG from the fictional contract.

Content comes from the shipped `redact` path on markdown (so CJK
placeholders stay intact). Layout is typeset with a local CJK font.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples" / "fictional"))

import pymupdf as fitz  # noqa: E402

from legal_redactor.pipeline import redact_file  # noqa: E402
from sample_contract_text import FICTIONAL_CONTRACT  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "images" / "dual-mode-preview.png"
ENTITIES_AI = ROOT / "examples" / "fictional" / "entities_ai.json"
ENTITIES_PROD = ROOT / "examples" / "fictional" / "entities_production.json"
MAX_LINES = 16


def _cjk_fontfile() -> str | None:
    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def _excerpt(text: str) -> list[str]:
    lines = text.splitlines()
    return lines[:MAX_LINES]


def render_preview(output: Path, work_dir: Path) -> Path:
    fontfile = _cjk_fontfile()
    if not fontfile:
        raise RuntimeError("no CJK font for preview (msyh/simhei/PingFang/NotoSansCJK)")

    work_dir.mkdir(parents=True, exist_ok=True)
    src = work_dir / "sample.md"
    src.write_text(FICTIONAL_CONTRACT, encoding="utf-8")

    columns: list[tuple[str, str]] = [("原文 original", FICTIONAL_CONTRACT)]
    for mode, entities, label in (
        ("ai", ENTITIES_AI, "ai · 给在线模型"),
        ("production", ENTITIES_PROD, "production · 给出证"),
    ):
        out = work_dir / f"sample.redacted-{mode}.md"
        result = redact_file(
            src,
            mode=mode,
            output_path=out,
            entities_path=entities,
            work_dir=work_dir / mode,
        )
        if not result.ok:
            raise RuntimeError(f"{mode} residual failed: {result.residual.summary}")
        body = out.read_text(encoding="utf-8")
        if mode == "ai" and "郝测一" in body:
            raise RuntimeError("ai excerpt still contains party name")
        if mode == "production" and "郝测一" not in body:
            raise RuntimeError("production excerpt lost party name")
        if "13900001111" in body:
            raise RuntimeError(f"{mode} excerpt still contains mobile")
        columns.append((label, body))

    col_w = 320
    gap = 16
    header = 36
    footer = 16
    line_h = 14
    fontsize = 9
    col_h = header + MAX_LINES * line_h + footer
    canvas_w = gap + len(columns) * (col_w + gap)
    canvas_h = col_h + gap
    doc = fitz.open()
    try:
        page = doc.new_page(width=canvas_w, height=canvas_h)
        page.insert_font(fontname="cjk", fontfile=fontfile)
        for i, (label, body) in enumerate(columns):
            x = gap + i * (col_w + gap)
            box = fitz.Rect(x, gap, x + col_w, gap + col_h - 8)
            page.draw_rect(box, color=(0.85, 0.85, 0.85), fill=(0.98, 0.98, 0.98), width=0.4)
            page.insert_text(
                (x + 10, gap + 22),
                label,
                fontsize=11,
                fontname="cjk",
                color=(0.15, 0.15, 0.15),
            )
            y = gap + header
            for line in _excerpt(body):
                page.insert_text(
                    (x + 10, y),
                    line[:42],
                    fontsize=fontsize,
                    fontname="cjk",
                    color=(0.1, 0.1, 0.1),
                )
                y += line_h
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(output))
    finally:
        doc.close()
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.work_dir is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="legal-redactor-preview-") as tmp:
            print(render_preview(args.output, Path(tmp)))
    else:
        print(render_preview(args.output, args.work_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
