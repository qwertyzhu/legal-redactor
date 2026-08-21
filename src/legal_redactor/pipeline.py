"""End-to-end redaction pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entities import RedactionPlan, build_plan
from .formats import docx_io, pdf_io, text_io
from .verify import ResidualReport, scan_residual

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf"}


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


@dataclass
class BatchRedactResult:
    mode: str
    results: list[RedactResult]
    skipped: list[tuple[Path, str]]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failed(self) -> list[RedactResult]:
        return [r for r in self.results if not r.ok]


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
        if output.suffix == "":
            output = output.with_suffix(suffix or ".txt")
        return text_io.redact_text(path, output, mapping)
    raise ValueError(f"unsupported file type: {suffix or '(none)'}")


def default_output_path(input_path: Path, mode: str) -> Path:
    return input_path.with_name(f"{input_path.stem}.redacted-{mode}{input_path.suffix}")


def _parse_category_list(values: list[str] | None) -> set[str]:
    out: set[str] = set()
    for raw in values or []:
        for part in str(raw).split(","):
            part = part.strip().lower()
            if part:
                out.add(part)
    return out


def _normalize_keep_extra(
    keep_categories: list[str] | set[str] | None,
    extra_categories: list[str] | set[str] | None,
) -> tuple[set[str], set[str]]:
    keep = (
        set(keep_categories)
        if isinstance(keep_categories, set)
        else _parse_category_list(list(keep_categories) if keep_categories else None)
    )
    extra = (
        set(extra_categories)
        if isinstance(extra_categories, set)
        else _parse_category_list(list(extra_categories) if extra_categories else None)
    )
    return keep, extra


def redact_file(
    input_path: Path,
    mode: str,
    output_path: Path | None = None,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    work_dir: Path | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
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

    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)

    source_text = _extract(input_path)
    plan = build_plan(
        source_text,
        mode=mode,
        entities_file=entities_path,
        preserve=preserve,
        keep_categories=keep,
        extra_categories=extra,
    )
    mapping = plan.mapping()
    output_text = _apply(input_path, output_path, mapping)
    residual = scan_residual(
        output_text, mode=mode, keep_categories=keep, extra_categories=extra
    )

    ledger_path = work_dir / f"{output_path.stem}.ledger.json"
    residual_path = work_dir / f"{output_path.stem}.residual.json"
    plan.dump(ledger_path)
    residual.dump(residual_path)

    summary_path = work_dir / f"{output_path.stem}.summary.md"
    summary_path.write_text(
        _render_summary(input_path, output_path, plan, residual, keep=keep, extra=extra),
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


def iter_batch_inputs(input_dir: Path, *, recursive: bool = False) -> list[Path]:
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise NotADirectoryError(input_dir)
    iterator: Iterable[Path]
    if recursive:
        iterator = (p for p in sorted(input_dir.rglob("*")) if p.is_file())
    else:
        iterator = (p for p in sorted(input_dir.iterdir()) if p.is_file())
    return [p for p in iterator if p.suffix.lower() in SUPPORTED_SUFFIXES]


def redact_tree(
    input_dir: Path,
    mode: str,
    output_dir: Path,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    work_dir: Path | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    recursive: bool = False,
) -> BatchRedactResult:
    """Redact every supported file under input_dir into output_dir (flat names)."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    if output_dir.resolve() == input_dir.resolve():
        raise ValueError("output directory must differ from input directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(work_dir) if work_dir else output_dir
    work_root.mkdir(parents=True, exist_ok=True)

    results: list[RedactResult] = []
    skipped: list[tuple[Path, str]] = []
    used_names: set[str] = set()

    for src in iter_batch_inputs(input_dir, recursive=recursive):
        name = f"{src.stem}.redacted-{mode}{src.suffix}"
        if name in used_names:
            # recursive batches can collide on basename — disambiguate
            rel = src.relative_to(input_dir)
            safe = "__".join(rel.with_suffix("").parts)
            name = f"{safe}.redacted-{mode}{src.suffix}"
        used_names.add(name)
        out = output_dir / name
        try:
            result = redact_file(
                input_path=src,
                mode=mode,
                output_path=out,
                entities_path=entities_path,
                preserve=preserve,
                work_dir=work_root,
                keep_categories=keep_categories,
                extra_categories=extra_categories,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - batch continues
            skipped.append((src, str(exc)))

    return BatchRedactResult(mode=mode, results=results, skipped=skipped)


def _render_summary(
    input_path: Path,
    output_path: Path,
    plan: RedactionPlan,
    residual: ResidualReport,
    keep: set[str] | None = None,
    extra: set[str] | None = None,
) -> str:
    lines = [
        f"# Redaction summary",
        "",
        f"- mode: `{plan.mode}`",
        f"- input: `{input_path}`",
        f"- output: `{output_path}`",
        f"- entities replaced: **{len(plan.entities)}**",
        f"- residual scan: **{'PASS' if residual.ok else 'FAIL'}**",
    ]
    if keep:
        lines.append(f"- keep_categories: `{', '.join(sorted(keep))}`")
    if extra:
        lines.append(f"- extra_categories: `{', '.join(sorted(extra))}`")
    lines.extend(
        [
            "",
            "## Replacements",
            "",
            "| original | replacement | category | role | source |",
            "|---|---|---|---|---|",
        ]
    )
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


def detect_only(
    input_path: Path,
    mode: str,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
) -> dict[str, Any]:
    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)
    text = _extract(Path(input_path))
    plan = build_plan(
        text,
        mode=mode,
        entities_file=entities_path,
        preserve=preserve,
        keep_categories=keep,
        extra_categories=extra,
    )
    residual_if_unchanged = scan_residual(
        text, mode=mode, keep_categories=keep, extra_categories=extra
    )
    return {
        "mode": mode,
        "keep_categories": sorted(keep),
        "extra_categories": sorted(extra),
        "plan": plan.to_dict(),
        "would_replace": len(plan.entities),
        "current_structural_hits": residual_if_unchanged.to_dict(),
    }
