"""End-to-end redaction pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .allowlist import load_allowlist
from .audit import AuditReport, build_audit_report
from .entities import RedactionPlan, build_plan, load_entities_file
from .ledger_store import (
    resolve_ledger_path,
    write_ledger_payload,
    write_ledger_pointer,
)
from .profiles_loader import Profile, resolve_profile
from .formats import docx_io, pdf_io, text_io
from .patterns import categories_for_mode
from .suspects import Suspect, detect_suspects
from .verify import ResidualReport, add_unreplaced, scan_residual, unreplaced_originals

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
    suspects: list[Suspect]
    suspects_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    audit: AuditReport | None = None
    audit_json_path: Path | None = None
    audit_md_path: Path | None = None
    profile: str | None = None

    @property
    def ok(self) -> bool:
        return self.residual.ok

    @property
    def status(self) -> str:
        if self.audit is not None:
            return self.audit.status
        return self.residual.status


@dataclass
class BatchRedactResult:
    mode: str
    results: list[RedactResult]
    skipped: list[tuple[Path, str]]
    consistency_path: Path | None = None
    entities_consistent_path: Path | None = None

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failed(self) -> list[RedactResult]:
        return [r for r in self.results if not r.ok]


REVISION_MODES = ("accept", "reject", "keep-and-redact")
OCR_IMAGE_MODES = ("auto", "on", "off")


def _extract(path: Path, *, revisions: str = "accept") -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.extract_text(path, revisions=revisions)
    if suffix == ".pdf":
        return pdf_io.extract_text(path)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return text_io.extract_text(path)
    raise ValueError(f"unsupported file type: {suffix or '(none)'}")


def _apply(
    path: Path,
    output: Path,
    mapping: list[tuple[str, str]],
    warnings: list[str] | None = None,
    *,
    preserve_hyperlinks: bool = False,
    preserve_bookmarks: bool = False,
    revisions: str = "accept",
    ocr_images: str = "auto",
    image_categories: set[str] | None = None,
) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.redact_docx(
            path,
            output,
            mapping,
            warnings=warnings,
            preserve_hyperlinks=preserve_hyperlinks,
            revisions=revisions,
            ocr_images=ocr_images,
            image_categories=image_categories,
        )
    if suffix == ".pdf":
        return pdf_io.redact_pdf(
            path,
            output,
            mapping,
            warnings=warnings,
            preserve_bookmarks=preserve_bookmarks,
            ocr_images=ocr_images,
            image_categories=image_categories,
        )
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


def resolve_allowlist(
    input_path: Path | None,
    allowlist_files: list[Path] | None = None,
    *,
    include_defaults: bool = True,
) -> set[str]:
    dirs = [Path.cwd()]
    if input_path is not None:
        base = Path(input_path)
        dirs.append(base if base.is_dir() else base.parent)
    return load_allowlist(
        extra_files=[Path(p) for p in (allowlist_files or [])],
        search_dirs=dirs,
        include_defaults=include_defaults,
    )


def _ner_suspects(text: str, known: set[str]) -> list[Suspect]:
    """Optional NER names (suspects only, never auto-replaced). [] if not installed."""
    from .ner import detect_ner_suspects

    return detect_ner_suspects(text, known=known)


def _deferred_suspects(plan: RedactionPlan) -> list[Suspect]:
    out: list[Suspect] = []
    seen: set[str] = set()
    for hit in plan.deferred_structural:
        if hit.text in seen:
            continue
        seen.add(hit.text)
        out.append(
            Suspect(
                text=hit.text,
                category=hit.category,
                reason=hit.notes or f"confidence={hit.confidence}",
                start=hit.start,
                end=hit.end,
                role_hint="unknown",
            )
        )
    return out


def redact_file(
    input_path: Path,
    mode: str,
    output_path: Path | None = None,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    work_dir: Path | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    auto_confident: bool = False,
    allowlist_files: list[Path] | None = None,
    use_default_allowlist: bool = True,
    allowlist: set[str] | None = None,
    profile: str | Profile | None = None,
    ledger: Path | None = None,
    ledger_encrypt: bool = False,
    ledger_beside_output: bool | None = None,
    preserve_hyperlinks: bool = False,
    preserve_bookmarks: bool = False,
    revisions: str = "accept",
    ocr_images: str = "auto",
    redact_court_staff: bool | None = None,
) -> RedactResult:
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    prof: Profile | None
    if isinstance(profile, Profile):
        prof = profile
    elif profile:
        prof = resolve_profile(profile=str(profile), mode=None)
    else:
        prof = None
    if prof is not None:
        mode = prof.mode
        preserve = list(preserve or []) + list(prof.preserve)
        if not auto_confident:
            auto_confident = prof.auto_confident
    # Backward compat: explicit work_dir means caller wants sidecars there (incl. ledger)
    if ledger_beside_output is None:
        ledger_beside_output = work_dir is not None and ledger is None

    mode = mode.lower().strip()
    output_path = Path(output_path) if output_path else default_output_path(input_path, mode)
    if output_path.suffix.lower() != input_path.suffix.lower():
        raise ValueError(
            f"output suffix {output_path.suffix!r} must match input suffix {input_path.suffix!r}"
        )

    work_dir = Path(work_dir) if work_dir else output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)

    if allowlist is None:
        allowlist = resolve_allowlist(
            input_path, allowlist_files, include_defaults=use_default_allowlist
        )
    if revisions not in REVISION_MODES:
        raise ValueError(f"revisions must be one of {REVISION_MODES}")
    if ocr_images not in OCR_IMAGE_MODES:
        raise ValueError(f"ocr_images must be one of {OCR_IMAGE_MODES}")
    source_text = _extract(input_path, revisions=revisions)
    only = prof.categories if prof is not None else None
    if prof is not None and prof.categories is None:
        keep |= prof.keep_categories
        extra |= prof.extra_categories
    plan = build_plan(
        source_text,
        mode=mode,
        entities_file=entities_path,
        preserve=preserve,
        keep_categories=keep,
        extra_categories=extra,
        auto_confident=auto_confident,
        allowlist=allowlist,
        only_categories=only,
        alias_style=prof.alias_style if prof else "generic",
        address_granularity=prof.address_granularity if prof else "full",
        amount_fuzzy=prof.amount_fuzzy if prof else True,
        redact_court_staff=(
            redact_court_staff
            if redact_court_staff is not None
            else (prof.redact_court_staff if prof else False)
        ),
    )
    mapping = plan.mapping()
    warnings: list[str] = []
    output_text = _apply(
        input_path,
        output_path,
        mapping,
        warnings=warnings,
        preserve_hyperlinks=preserve_hyperlinks,
        preserve_bookmarks=preserve_bookmarks,
        revisions=revisions,
        ocr_images=ocr_images,
        image_categories=categories_for_mode(
            mode, keep_categories=keep, extra_categories=extra, only_categories=only
        ),
    )
    staff = (
        redact_court_staff
        if redact_court_staff is not None
        else (prof.redact_court_staff if prof else False)
    )
    residual = scan_residual(
        output_text,
        mode=mode,
        keep_categories=keep,
        extra_categories=extra,
        only_categories=only,
        nl=True,
        allowlist=allowlist,
        known={p.strip() for p in (preserve or []) if p and p.strip()},
        redact_court_staff=staff,
    )
    add_unreplaced(
        residual,
        unreplaced_originals(
            output_text, mapping, join_lines=input_path.suffix.lower() == ".pdf"
        ),
    )

    known = {e.original for e in plan.entities}
    known |= {p.strip() for p in (preserve or []) if p and p.strip()}
    # Also treat entities file originals as known even if production kept parties
    for raw in load_entities_file(entities_path):
        original = str(raw.get("original") or raw.get("text") or "").strip()
        if original:
            known.add(original)
    known |= set(allowlist)
    suspects = _deferred_suspects(plan) + detect_suspects(
        source_text, known=known, allowlist=allowlist
    )
    suspects += _ner_suspects(source_text, known | {x.text for x in suspects})

    ledger_target = resolve_ledger_path(
        input_path,
        mode=mode,
        output_path=output_path,
        ledger=ledger,
        legacy_beside_output=bool(ledger_beside_output),
    )
    if ledger_beside_output and ledger is None:
        ledger_target = work_dir / f"{output_path.stem}.ledger.json"
    ledger_payload = plan.to_dict()
    ledger_payload["input_path"] = str(input_path)
    ledger_payload["output_path"] = str(output_path)
    ledger_payload["profile"] = prof.name if prof else None
    ledger_path = write_ledger_payload(ledger_target, ledger_payload, encrypt=ledger_encrypt)
    if not ledger_beside_output:
        write_ledger_pointer(work_dir, output_path.stem, ledger_path)
    residual_path = work_dir / f"{output_path.stem}.residual.json"
    suspects_path = work_dir / f"{output_path.stem}.suspects.json"
    residual.dump(residual_path)
    suspects_path.write_text(
        json.dumps(
            {
                "mode": mode,
                "count": len(suspects),
                "warning": (
                    "Hints only. Not auto-redacted. Confirm role/replacement in entities.json."
                ),
                "suspects": [s.to_dict() for s in suspects],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_path = work_dir / f"{output_path.stem}.summary.md"
    summary_path.write_text(
        _render_summary(
            input_path,
            output_path,
            plan,
            residual,
            keep=keep,
            extra=extra,
            suspects=suspects,
            warnings=warnings,
        ),
        encoding="utf-8",
    )

    audit = build_audit_report(
        source_text=source_text,
        output_text=output_text,
        plan=plan,
        residual=residual,
        input_path=input_path,
        output_path=output_path,
        profile=prof.name if prof else None,
        part=_audit_part_label(input_path),
    )
    audit_json_path = work_dir / f"{output_path.stem}.audit.json"
    audit_md_path = work_dir / f"{output_path.stem}.audit.md"
    audit.dump_json(audit_json_path)
    audit.dump_md(audit_md_path)

    return RedactResult(
        input_path=input_path,
        output_path=output_path,
        mode=mode,
        plan=plan,
        residual=residual,
        ledger_path=ledger_path,
        residual_path=residual_path,
        output_text=output_text,
        suspects=suspects,
        suspects_path=suspects_path,
        warnings=warnings,
        audit=audit,
        audit_json_path=audit_json_path,
        audit_md_path=audit_md_path,
        profile=prof.name if prof else None,
    )


def _audit_part_label(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "word/document.xml+parts"
    if suffix == ".pdf":
        return "pdf:text-layer"
    return "text"


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
    unify_first: bool = False,
    auto_confident: bool = False,
    allowlist_files: list[Path] | None = None,
    use_default_allowlist: bool = True,
    profile: str | None = None,
    ledger_encrypt: bool = False,
    preserve_hyperlinks: bool = False,
    preserve_bookmarks: bool = False,
    revisions: str = "accept",
    ocr_images: str = "auto",
    redact_court_staff: bool | None = None,
) -> BatchRedactResult:
    """Redact every supported file under input_dir into output_dir (flat names).

    unify_first: scan the folder first, write entities.consistent.json, and use it
    as the entities file for every document so aliases stay stable across the batch.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    if output_dir.resolve() == input_dir.resolve():
        raise ValueError("output directory must differ from input directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(work_dir) if work_dir else output_dir
    work_root.mkdir(parents=True, exist_ok=True)

    effective_entities = entities_path
    pre_entities_path: Path | None = None
    if unify_first:
        from .consistency import unify_directory

        pre = unify_directory(
            input_dir,
            work_root,
            mode=mode,
            entities_path=entities_path,
            recursive=recursive,
            include_suspects=True,
        )
        pre_entities_path = pre.entities_path
        effective_entities = pre.entities_path

    results: list[RedactResult] = []
    skipped: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    batch_allow = resolve_allowlist(
        input_dir, allowlist_files, include_defaults=use_default_allowlist
    )

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
                entities_path=effective_entities,
                preserve=preserve,
                work_dir=work_root,
                keep_categories=keep_categories,
                extra_categories=extra_categories,
                auto_confident=auto_confident,
                allowlist=batch_allow,
                profile=profile,
                ledger_encrypt=ledger_encrypt,
                ledger_beside_output=True,
                preserve_hyperlinks=preserve_hyperlinks,
                preserve_bookmarks=preserve_bookmarks,
                revisions=revisions,
                ocr_images=ocr_images,
                redact_court_staff=redact_court_staff,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - batch continues
            skipped.append((src, str(exc)))

    consistency_path = None
    entities_consistent_path = pre_entities_path
    if results:
        from .consistency import unify_from_ledgers

        report = unify_from_ledgers(work_root, work_root, mode=mode)
        consistency_path = report.report_path
        # Prefer post-ledger path; keep pre-unify path if post missing
        entities_consistent_path = report.entities_path or pre_entities_path

    return BatchRedactResult(
        mode=mode,
        results=results,
        skipped=skipped,
        consistency_path=consistency_path,
        entities_consistent_path=entities_consistent_path,
    )


def verify_tree(
    input_dir: Path,
    mode: str,
    *,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    recursive: bool = False,
    only_categories: set[str] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Residual-scan every supported file under a directory.

    Ledger / audit / residual sidecars are skipped (they carry originals by design).
    """
    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)
    allow = resolve_allowlist(input_dir, _ignored.get("allowlist_files"))
    rows: list[dict[str, Any]] = []
    sidecar_suffixes = (
        ".ledger.json",
        ".residual.json",
        ".suspects.json",
        ".audit.json",
        ".summary.md",
        ".audit.md",
        ".ledger-path.txt",
    )
    for path in iter_batch_inputs(input_dir, recursive=recursive):
        if path.name.endswith(sidecar_suffixes) or path.name.startswith(("entities.consistent", "consistency.report")):
            continue
        # Superset view: deleted/inserted revision text is scanned too.
        text = _extract(path, revisions="keep-and-redact")
        report = scan_residual(
            text,
            mode=mode,
            keep_categories=keep,
            extra_categories=extra,
            only_categories=only_categories,
            nl=True,
            allowlist=allow,
            redact_court_staff=bool(_ignored.get("redact_court_staff")),
        )
        rows.append(
            {
                "path": str(path),
                "name": path.name,
                "ok": report.ok,
                "status": report.status,
                "summary": report.summary,
                "hits": report.hits,
                "warnings": report.warnings,
            }
        )
    failed = [r for r in rows if r["status"] == "FAIL"]
    warned = [r for r in rows if r["status"] == "WARN"]
    status = "FAIL" if failed else ("WARN" if warned else "PASS")
    return {
        "mode": mode,
        "files": len(rows),
        "failed": len(failed),
        "warned": len(warned),
        "ok": len(failed) == 0,
        "status": status,
        "results": rows,
    }

def scan_tree(
    input_dir: Path,
    mode: str,
    *,
    entities_path: Path | None = None,
    preserve: list[str] | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    recursive: bool = False,
    allowlist_files: list[Path] | None = None,
    use_default_allowlist: bool = True,
) -> dict[str, Any]:
    """detect_only for every supported file under a directory."""
    rows = []
    for path in iter_batch_inputs(input_dir, recursive=recursive):
        payload = detect_only(
            path,
            mode=mode,
            entities_path=entities_path,
            preserve=preserve,
            keep_categories=keep_categories,
            extra_categories=extra_categories,
            allowlist_files=allowlist_files,
            use_default_allowlist=use_default_allowlist,
        )
        payload["path"] = str(path)
        payload["name"] = path.name
        rows.append(payload)
    return {
        "mode": mode,
        "files": len(rows),
        "results": rows,
    }


def _render_summary(
    input_path: Path,
    output_path: Path,
    plan: RedactionPlan,
    residual: ResidualReport,
    keep: set[str] | None = None,
    extra: set[str] | None = None,
    suspects: list[Suspect] | None = None,
    warnings: list[str] | None = None,
) -> str:
    lines = [
        f"# Redaction summary",
        "",
        f"- mode: `{plan.mode}`",
        f"- input: `{input_path}`",
        f"- output: `{output_path}`",
        f"- entities replaced: **{len(plan.entities)}**",
        f"- residual scan: **{'PASS' if residual.ok else 'FAIL'}**",
        f"- NL suspects (not auto-redacted): **{len(suspects or [])}**",
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
    if suspects:
        lines.extend(
            [
                "## Suspect natural-language entities",
                "",
                "Hints only — **not** replaced. Confirm in `entities.json` before relying on AI-mode anonymity.",
                "",
                "| text | category | role_hint | reason |",
                "|---|---|---|---|",
            ]
        )
        for s in suspects:
            t = s.text.replace("|", "\\|")
            lines.append(f"| `{t}` | {s.category} | {s.role_hint} | {s.reason} |")
        lines.append("")
    if warnings:
        lines.extend(["## Warnings (manual review)", ""])
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Keep the ledger file local. Do not commit it or paste it into online AI chats.",
            "- Structural PASS does not prove every natural-language name was caught.",
            "- Suspect list is heuristic; false positives/negatives are expected.",
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
    allowlist_files: list[Path] | None = None,
    use_default_allowlist: bool = True,
) -> dict[str, Any]:
    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)
    allowlist = resolve_allowlist(
        Path(input_path), allowlist_files, include_defaults=use_default_allowlist
    )
    text = _extract(Path(input_path))
    plan = build_plan(
        text,
        mode=mode,
        entities_file=entities_path,
        preserve=preserve,
        keep_categories=keep,
        extra_categories=extra,
        allowlist=allowlist,
    )
    residual_if_unchanged = scan_residual(
        text, mode=mode, keep_categories=keep, extra_categories=extra
    )
    known = {e.original for e in plan.entities}
    known |= {p.strip() for p in (preserve or []) if p and p.strip()}
    for raw in load_entities_file(entities_path):
        original = str(raw.get("original") or raw.get("text") or "").strip()
        if original:
            known.add(original)
    known |= set(allowlist)
    suspects = _deferred_suspects(plan) + detect_suspects(
        text, known=known, allowlist=allowlist
    )
    suspects += _ner_suspects(text, known | {x.text for x in suspects})
    return {
        "mode": mode,
        "keep_categories": sorted(keep),
        "extra_categories": sorted(extra),
        "plan": plan.to_dict(),
        "would_replace": len(plan.entities),
        "current_structural_hits": residual_if_unchanged.to_dict(),
        "suspects": [s.to_dict() for s in suspects],
        "suspect_count": len(suspects),
    }
