"""Plain text / markdown helpers."""

from __future__ import annotations

from pathlib import Path

from ..entities import apply_mapping_to_text


def _read_text(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with path.open(encoding="gb18030", newline="") as fh:
            return fh.read()


def extract_text(path: Path) -> str:
    return _read_text(path)


def redact_text(input_path: Path, output_path: Path, mapping: list[tuple[str, str]]) -> str:
    original = _read_text(input_path)
    updated = apply_mapping_to_text(original, mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(updated)
    return updated
