#!/usr/bin/env python3
"""Draft a starter entities.json from structural hits + optional name hints.

Does NOT invent party names. It only:
1) dumps structural detections as optional override rows;
2) leaves an empty shell for you/agent to fill natural-language entities.

Usage:
  python scripts/draft_entities.py path/to/doc.docx -o entities.draft.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_redactor.formats import docx_io, pdf_io, text_io  # noqa: E402
from legal_redactor.patterns import detect_structural  # noqa: E402


def _extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.extract_text(path)
    if suffix == ".pdf":
        return pdf_io.extract_text(path)
    return text_io.extract_text(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Draft entities.json skeleton from a document")
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("entities.draft.json"))
    args = p.parse_args(argv)

    text = _extract(args.input)
    hits = detect_structural(text)
    seen: set[str] = set()
    entities: list[dict] = [
        {
            "original": "（在此填写当事人/单位/地址/作品名）",
            "category": "organization",
            "role": "party",
            "notes": "Agent judgment — replace this placeholder row",
        }
    ]
    for h in hits:
        if h.text in seen:
            continue
        seen.add(h.text)
        entities.append(
            {
                "original": h.text,
                "category": h.category,
                "role": "structural",
                "source": "structural-draft",
                "notes": "optional; CLI auto-detects this category unless you need a custom replacement",
            }
        )

    payload = {
        "entities": entities,
        "_meta": {
            "source": str(args.input),
            "structural_unique": len(seen),
            "warning": "Review before use. Natural-language names are NOT auto-filled.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output} ({len(entities)} rows, {len(seen)} structural)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
