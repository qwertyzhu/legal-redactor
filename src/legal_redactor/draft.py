"""Draft starter entities.json from structural hits (no name invention)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .formats import docx_io, pdf_io, text_io
from .patterns import detect_structural

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}


def extract_text(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.extract_text(path)
    if suffix == ".pdf":
        return pdf_io.extract_text(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return text_io.extract_text(path)
    raise ValueError(f"unsupported file type: {suffix or '(none)'}")


def draft_entities_payload(path: Path, *, text: str | None = None) -> dict[str, Any]:
    """Build a reviewable entities draft. Does not invent natural-language names."""
    path = Path(path)
    body = text if text is not None else extract_text(path)
    hits = detect_structural(body)
    seen: set[str] = set()
    entities: list[dict[str, Any]] = [
        {
            "original": "（在此填写当事人/单位/地址/作品名）",
            "category": "organization",
            "role": "party",
            "notes": "Agent judgment — replace this placeholder row",
        }
    ]
    for hit in hits:
        if hit.text in seen:
            continue
        seen.add(hit.text)
        entities.append(
            {
                "original": hit.text,
                "category": hit.category,
                "role": "structural",
                "source": "structural-draft",
                "notes": (
                    "optional; CLI auto-detects this category unless you need "
                    "a custom replacement"
                ),
            }
        )
    return {
        "entities": entities,
        "_meta": {
            "source": str(path),
            "structural_unique": len(seen),
            "warning": "Review before use. Natural-language names are NOT auto-filled.",
        },
    }


def write_entities_draft(path: Path, output: Path, *, text: str | None = None) -> dict[str, Any]:
    payload = draft_entities_payload(path, text=text)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
