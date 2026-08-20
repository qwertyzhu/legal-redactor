"""End-to-end redaction pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .entities import RedactionPlan, build_plan
from .formats import docx_io, pdf_io, text_io
from .verify import ResidualReport, scan_residual

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}


@dataclass
class RedactResult:
    input_path: Path
    output_path: Path
    mode: str
    plan: RedactionPlan
    residual: ResidualReport
    ledger_path: Path
    residual_path: Path
    output_text: str

    @property
    def ok(self) -> bool:
        return self.residual.ok


def _extract(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.extract_text(path)
    if suffix == ".pdf":
        return pdf_io.extract_text(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return text_io.extract_text(path)
    raise ValueError(f"unsupported file type: {suffix or '(none)'}")


def _apply(path: Path, output: Path, mapping: list[tuple[str, str]]) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.redact_docx(path, output, mapping)
    if suffix == ".pdf":
        return pdf_io.redact_pdf(path, output, mapping)
    if suffix in TEXT_SUFFIXES or suffix == "":
        # keep output suffix if provided; default to input suffix
        if output.suffix == "":
            output = output.with_suffix(suffix or ".txt")
        return text_io.redact_text(path, output, mapping)
    raise ValueError(f"unsupported file type: {suffix or '(none)'}")


def default_output_path(input_path: Path, mode: str) -> Path:
    return input_path.with_name(f"{input_path.stem}.redacted-{mode}{input_path.suffix}")


def redact_file(
    input_path: Path,
    mode: str,
    output_path: Path | None = None,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    work_dir: Path | None = None,
) -> RedactResult:
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    mode = mode.lower().strip()
    output_path = Path(output_path) if output_path else default_output_path(input_path, mode)
    if output_path.suffix.lower() != input_path.suffix.lower():
        raise ValueError(
            f"output suffix {output_path.suffix!r} must match input suffix {input_path.suffix!r}"
        )

    work_dir = Path(work_dir) if work_dir else output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    source_text = _extract(input_path)
    plan = build_plan(source_text, mode=mode, entities_file=entities_path, preserve=preserve)
    mapping = plan.mapping()
    output_text = _apply(input_path, output_path, mapping)
    residual = scan_residual(output_text, mode=mode)

    ledger_path = work_dir / f"{output_path.stem}.ledger.json"
    residual_path = work_dir / f"{output_path.stem}.residual.json"
    plan.dump(ledger_path)
    residual.dump(residual_path)

    # Side-car summary for humans
    summary_path = work_dir / f"{output_path.stem}.summary.md"
    summary_path.write_text(
        _render_summary(input_path, output_path, plan, residual),
        encoding="utf-8",
    )

    return RedactResult(
        input_path=input_path,
        output_path=output_path,
        mode=mode,
        plan=plan,
        residual=residual,
        ledger_path=ledger_path,
        residual_path=residual_path,
        output_text=output_text,
    )


def _render_summary(
    input_path: Path,
    output_path: Path,
    plan: RedactionPlan,
    residual: ResidualReport,
) -> str:
    lines = [
        f"# Redaction summary",
        "",
        f"- mode: `{plan.mode}`",
        f"- input: `{input_path}`",
        f"- output: `{output_path}`",
        f"- entities replaced: **{len(plan.entities)}**",
        f"- residual scan: **{'PASS' if residual.ok else 'FAIL'}**",
        "",
        "## Replacements",
        "",
        "| original | replacement | category | role | source |",
        "|---|---|---|---|---|",
    ]
    for e in plan.entities:
        o = e.original.replace("|", "\\|")
        r = e.replacement.replace("|", "\\|")
        lines.append(f"| `{o}` | `{r}` | {e.category} | {e.role} | {e.source} |")
    lines.extend(["", f"## Residual", "", residual.summary, ""])
    if residual.hits:
        lines.append("Remaining hits:")
        for h in residual.hits:
            lines.append(f"- `{h['category']}`: `{h['text']}`")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Keep the ledger file local. Do not commit it or paste it into online AI chats.",
            "- Structural PASS does not prove every natural-language name was caught.",
            "- Human review is required before court filing or opponent production.",
            "",
        ]
    )
    return "\n".join(lines)


def detect_only(input_path: Path, mode: str, entities_path: Path | None = None) -> dict[str, Any]:
    text = _extract(Path(input_path))
    plan = build_plan(text, mode=mode, entities_file=entities_path)
    residual_if_unchanged = scan_residual(text, mode=mode)
    return {
        "mode": mode,
        "plan": plan.to_dict(),
        "would_replace": len(plan.entities),
        "current_structural_hits": residual_if_unchanged.to_dict(),
    }
