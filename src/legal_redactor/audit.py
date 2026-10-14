"""Per-replacement audit reports and residual-risk heuristics."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .entities import RedactionPlan
from .patterns import detect_structural
from .suspects import detect_suspects
from .verify import ResidualReport

_DIGIT_RUN = re.compile(r"(?<!\d)\d{10,}(?!\d)")
_BOOK = re.compile(r"《[^》\n]{1,40}》")
_BIRTHISH = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")


@dataclass
class AuditEntry:
    original: str
    replacement: str
    category: str
    role: str
    source: str
    confidence: str
    rule: str
    part: str
    paragraph_index: int | None
    context_before: str
    context_after: str
    start: int | None = None
    end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    mode: str
    profile: str | None
    input_path: str
    output_path: str
    status: str  # PASS | WARN | FAIL
    entries: list[AuditEntry] = field(default_factory=list)
    residual_risks: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "profile": self.profile,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "status": self.status,
            "summary": self.summary,
            "entries": [e.to_dict() for e in self.entries],
            "residual_risks": self.residual_risks,
        }

    def dump_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def dump_md(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Audit report — `{self.status}`",
            "",
            f"- mode: `{self.mode}`",
            f"- profile: `{self.profile or '-'}`",
            f"- input: `{self.input_path}`",
            f"- output: `{self.output_path}`",
            f"- replacements: **{len(self.entries)}**",
            f"- summary: {self.summary}",
            "",
            "## Replacements",
            "",
            "| original | replacement | category | confidence | rule | part | ¶ | context |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for e in self.entries:
            ctx = f"…{e.context_before}|{e.context_after}…"
            o = e.original.replace("|", "\\|")
            r = e.replacement.replace("|", "\\|")
            lines.append(
                f"| `{o}` | `{r}` | {e.category} | {e.confidence} | {e.rule} | {e.part} | "
                f"{e.paragraph_index if e.paragraph_index is not None else '-'} | `{ctx}` |"
            )
        lines.extend(["", "## Residual risks", ""])
        for key, val in self.residual_risks.items():
            if isinstance(val, list):
                lines.append(f"- **{key}** ({len(val)}): " + ", ".join(f"`{x}`" for x in val[:12]))
                if len(val) > 12:
                    lines.append(f"  - … {len(val) - 12} more")
            else:
                lines.append(f"- **{key}**: {val}")
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


def _confidence_from_notes(notes: str, source: str) -> str:
    if "confidence=medium" in (notes or "") or "checksum_fail" in (notes or "") or "luhn_fail" in (notes or ""):
        return "medium"
    if source in {"structural", "auto-confident"}:
        return "high"
    if source in {"agent", "manual", "unified"}:
        return "reviewed"
    return "unknown"


def _rule_name(category: str, source: str, notes: str) -> str:
    if source == "structural":
        return f"structural:{category}"
    if source == "auto-confident":
        return notes or "auto-confident"
    if notes:
        return f"{source}:{notes[:40]}"
    return source or "unknown"


def build_audit_entries(source_text: str, plan: RedactionPlan, *, part: str = "body") -> list[AuditEntry]:
    paragraphs = source_text.split("\n")
    # cumulative offsets per paragraph
    offsets: list[int] = []
    pos = 0
    for p in paragraphs:
        offsets.append(pos)
        pos += len(p) + 1
    entries: list[AuditEntry] = []
    for ent in plan.entities:
        if not ent.original or not ent.replacement or ent.original == ent.replacement:
            continue
        start = source_text.find(ent.original)
        para_idx = None
        before = after = ""
        if start >= 0:
            end = start + len(ent.original)
            before = source_text[max(0, start - 10) : start]
            after = source_text[end : end + 10]
            for i, off in enumerate(offsets):
                nxt = offsets[i + 1] if i + 1 < len(offsets) else len(source_text) + 1
                if off <= start < nxt:
                    para_idx = i
                    break
        else:
            start = None
            end = None
        entries.append(
            AuditEntry(
                original=ent.original,
                replacement=ent.replacement,
                category=ent.category,
                role=ent.role,
                source=ent.source,
                confidence=_confidence_from_notes(ent.notes, ent.source),
                rule=_rule_name(ent.category, ent.source, ent.notes),
                part=part,
                paragraph_index=para_idx,
                context_before=before,
                context_after=after,
                start=start,
                end=end if start is not None else None,
            )
        )
    return entries


def residual_risks(output_text: str, *, known_replacements: set[str] | None = None) -> dict[str, Any]:
    known = known_replacements or set()
    digit_runs = [m.group(0) for m in _DIGIT_RUN.finditer(output_text) if m.group(0) not in known]
    # de-dupe preserving order
    seen: set[str] = set()
    digits_u = []
    for d in digit_runs:
        if d not in seen:
            seen.add(d)
            digits_u.append(d)
    books = [m.group(0) for m in _BOOK.finditer(output_text)]
    births = [m.group(0) for m in _BIRTHISH.finditer(output_text)]
    suspects = detect_suspects(output_text)
    nl = [s.text for s in suspects if s.category in {"person", "organization", "address"}]
    struct_left = [
        {"category": h.category, "text": h.text, "notes": h.notes}
        for h in detect_structural(output_text)
        if not h.auto_replace
    ]
    return {
        "long_digit_strings": digits_u[:50],
        "book_titles_remaining": books[:50],
        "date_like_remaining": births[:50],
        "nl_suspects_remaining": nl[:50],
        "low_confidence_structural": struct_left[:50],
        "quasi_identifier_hint": bool(births) and bool(nl),
    }


def decide_status(
    residual: ResidualReport,
    risks: dict[str, Any],
    *,
    deferred_count: int = 0,
) -> tuple[str, str]:
    """Return (PASS|WARN|FAIL, summary)."""
    if residual.hits:
        return "FAIL", residual.summary
    warn_bits: list[str] = []
    if residual.warnings or deferred_count:
        warn_bits.append(
            f"{len(residual.warnings) or deferred_count} low-confidence structural value(s)"
        )
    if risks.get("quasi_identifier_hint"):
        warn_bits.append("possible quasi-identifier combo (date + NL suspect)")
    if risks.get("nl_suspects_remaining"):
        warn_bits.append(f"{len(risks['nl_suspects_remaining'])} NL suspect(s) remain")
    if risks.get("long_digit_strings"):
        warn_bits.append(f"{len(risks['long_digit_strings'])} long digit string(s)")
    if warn_bits:
        return "WARN", "residual PASS with warnings: " + "; ".join(warn_bits)
    return "PASS", residual.summary


def build_audit_report(
    *,
    source_text: str,
    output_text: str,
    plan: RedactionPlan,
    residual: ResidualReport,
    input_path: Path,
    output_path: Path,
    profile: str | None = None,
    part: str = "body",
) -> AuditReport:
    entries = build_audit_entries(source_text, plan, part=part)
    risks = residual_risks(output_text)
    status, summary = decide_status(
        residual, risks, deferred_count=len(plan.deferred_structural)
    )
    return AuditReport(
        mode=plan.mode,
        profile=profile,
        input_path=str(input_path),
        output_path=str(output_path),
        status=status,
        entries=entries,
        residual_risks=risks,
        summary=summary,
    )
