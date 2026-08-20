"""Plain text / markdown helpers."""

from __future__ import annotations

from pathlib import Path

from ..entities import apply_mapping_to_text


def extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def redact_text(input_path: Path, output_path: Path, mapping: list[tuple[str, str]]) -> str:
    original = extract_text(input_path)
    updated = apply_mapping_to_text(original, mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(updated, encoding="utf-8")
    return updated
