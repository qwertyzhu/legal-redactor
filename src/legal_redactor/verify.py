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


NL_RESIDUAL_CATEGORIES = frozenset({"person", "organization", "address"})
NL_NOTE = "nl_residual"


def nl_residual(
    text: str,
    *,
    allowlist: set[str] | None = None,
    known: set[str] | None = None,
    redact_court_staff: bool = False,
) -> list[dict[str, Any]]:
    """Name/org/address candidates still present in redacted output (ai mode).

    Heuristic, so reported as WARN — but it is the only check that sees a party
    name the structural scan cannot.
    """
    from .suspects import detect_suspects

    out: list[dict[str, Any]] = []
    for s in detect_suspects(text, known=known, allowlist=allowlist):
        if s.category not in NL_RESIDUAL_CATEGORIES:
            continue
        if s.role_hint == "court_staff" and not redact_court_staff:
            continue
        if "某" in s.text or "[" in s.text:  # our own aliases / placeholders
            continue
        out.append(
            {
                "category": s.category,
                "text": s.text,
                "start": s.start,
                "end": s.end,
                "notes": f"{NL_NOTE}:{s.reason}",
            }
        )
    return out


def unreplaced_originals(
    output_text: str, mapping: list[tuple[str, str]], *, join_lines: bool = False
) -> list[str]:
    """Planned originals that survived into the output — a definite leak.

    join_lines: PDF text extraction breaks a wrapped entity with a newline.
    """
    hay = output_text.replace("\n", "") if join_lines else output_text
    replacements = "\n".join(r for _, r in mapping)
    return [o for o, _ in mapping if o and o in hay and o not in replacements]


def add_unreplaced(report: ResidualReport, leftovers: list[str]) -> ResidualReport:
    if not leftovers:
        return report
    report.hits.extend(
        {"category": "unreplaced_entity", "text": o, "start": -1, "end": -1} for o in leftovers
    )
    report.ok = False
    report.status = "FAIL"
    report.summary = (
        f"residual scan FAILED for mode={report.mode}: "
        f"{len(leftovers)} planned replacement(s) still present in output"
    )
    return report


def scan_residual(
    text: str,
    mode: str,
    *,
    keep_categories: set[str] | None = None,
    extra_categories: set[str] | None = None,
    only_categories: set[str] | None = None,
    nl: bool = False,
    allowlist: set[str] | None = None,
    known: set[str] | None = None,
    redact_court_staff: bool = False,
) -> ResidualReport:
    """Structural residual scan; nl=True also lists leftover names in ai mode."""
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
    # production keeps party names by design; only ai promises to remove them.
    if nl and mode.lower().strip() == "ai":
        low += nl_residual(
            text, allowlist=allowlist, known=known, redact_court_staff=redact_court_staff
        )
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
        n_nl = sum(1 for w in low if str(w.get("notes", "")).startswith(NL_NOTE))
        bits = []
        if len(low) - n_nl:
            bits.append(f"{len(low) - n_nl} low-confidence structural value(s)")
        if n_nl:
            bits.append(f"{n_nl} name/org/address candidate(s) still in output")
        summary = f"residual scan WARN for mode={mode}: {'; '.join(bits)} — review manually"
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
