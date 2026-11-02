"""Plain text / markdown helpers."""

from __future__ import annotations

from pathlib import Path

from ..entities import apply_mapping_to_text


def extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def redact_text(input_path: Path, output_path: Path, mapping: list[tuple[str, str]]) -> str:
    # newline="" both ways: keep the input's LF/CRLF instead of the platform default.
    with open(input_path, encoding="utf-8", newline="") as fh:
        original = fh.read()
    updated = apply_mapping_to_text(original, mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(updated)
    return updated
