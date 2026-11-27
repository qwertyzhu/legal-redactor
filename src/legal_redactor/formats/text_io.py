"""Plain text / markdown helpers."""

from __future__ import annotations

import re
from bisect import bisect_left
from pathlib import Path

from ..entities import apply_mapping_to_text, find_mapping_spans


def _read_text(path: Path, *, newline: str | None = None) -> str:
    try:
        with path.open(encoding="utf-8", newline=newline) as fh:
            return fh.read()
    except UnicodeDecodeError:
        with path.open(encoding="gb18030", newline=newline) as fh:
            return fh.read()


def extract_text(path: Path) -> str:
    return _read_text(path)


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _apply_text_mapping(text: str, mapping: list[tuple[str, str]]) -> str:
    if "\r" not in text or not any("\n" in s or "\r" in s for s, _ in mapping):
        return apply_mapping_to_text(text, mapping)

    # Match in the same newline-normalized view as extraction, then edit the raw text.
    crlfs = [m.start() - i for i, m in enumerate(re.finditer(r"\r\n", text))]
    normalized_mapping = [(_normalize_line_endings(s), r) for s, r in mapping]
    spans = find_mapping_spans(_normalize_line_endings(text), normalized_mapping)
    parts: list[str] = []
    last = 0
    for start, end, replacement in spans:
        start += bisect_left(crlfs, start)
        end += bisect_left(crlfs, end)
        parts.extend((text[last:start], replacement))
        last = end
    parts.append(text[last:])
    return "".join(parts)


def redact_text(input_path: Path, output_path: Path, mapping: list[tuple[str, str]]) -> str:
    # Keep the input's original line endings outside redacted spans.
    original = _read_text(input_path, newline="")
    updated = _apply_text_mapping(original, mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(updated)
    return updated
