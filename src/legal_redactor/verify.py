"""Residual sensitive-pattern scan after redaction."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .patterns import PatternHit, categories_for_mode, detect_structural

# Exit codes for CLI verify
EXIT_PASS = 0
EXIT_WARN = 1
EXIT_FAIL = 2


@dataclass
class ResidualReport:
    mode: str
    ok: bool
    hits: list[dict[str, Any]]
    summary: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    status: str = "PASS"  # PASS | WARN | FAIL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def scan_residual(
    text: str,
    mode: str,
    *,
    keep_categories: set[str] | None = None,
    extra_categories: set[str] | None = None,
    only_categories: set[str] | None = None,
) -> ResidualReport:
    cats = categories_for_mode(
        mode,
        keep_categories=keep_categories,
        extra_categories=extra_categories,
        only_categories=only_categories,
    )
    all_hits = [h for h in detect_structural(text) if h.category in cats]
    # Checksum/Luhn-failed unlabeled values are never auto-replaced; report, don't fail.
    hits = [h for h in all_hits if h.auto_replace]
    low = [
        {"category": h.category, "text": h.text, "start": h.start, "end": h.end, "notes": h.notes}
        for h in all_hits
        if not h.auto_replace
    ]
    serializable = [
        {"category": h.category, "text": h.text, "start": h.start, "end": h.end} for h in hits
    ]
    ok = len(hits) == 0
    if not ok:
        kinds: dict[str, int] = {}
        for h in hits:
            kinds[h.category] = kinds.get(h.category, 0) + 1
        detail = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
        summary = f"residual scan FAILED for mode={mode}: {detail}"
        status = "FAIL"
    elif low:
        summary = (
            f"residual scan WARN for mode={mode}: "
            f"{len(low)} low-confidence value(s) left for manual review"
        )
        status = "WARN"
    else:
        summary = f"residual scan passed for mode={mode}"
        status = "PASS"
    return ResidualReport(
        mode=mode, ok=ok, hits=serializable, summary=summary, warnings=low, status=status
    )


def hits_from_report(report: ResidualReport) -> list[PatternHit]:
    return [
        PatternHit(h["category"], h["text"], h["start"], h["end"]) for h in report.hits
    ]


def exit_code_for_status(status: str) -> int:
    status = (status or "").upper()
    if status == "FAIL":
        return EXIT_FAIL
    if status == "WARN":
        return EXIT_WARN
    return EXIT_PASS
