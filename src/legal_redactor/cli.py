"""CLI for legal-redactor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .formats import docx_io, pdf_io, text_io
from .pipeline import detect_only, redact_file, redact_tree
from .verify import scan_residual


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="legal-redactor",
        description=(
            "Local-first dual-mode redaction for Chinese legal documents. "
            "Modes: ai (aggressive, for online models) | production (selective, for court/opponent). "
            "Scanned PDFs: use `ocr` then redact text, or `redact-scan` for visual black boxes. "
            "Pass a directory to `redact` for batch processing."
        ),
    )
    p.add_argument("--version", action="version", version=f"legal-redactor {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser, *, require_mode: bool = True) -> None:
        sp.add_argument(
            "input",
            type=Path,
            help="Input file, or directory for batch redact",
        )
        if require_mode:
            sp.add_argument(
                "--mode",
                choices=("ai", "production"),
                required=True,
                help="ai=full desensitization; production=keep parties, strip high-risk contact/account data",
            )
        sp.add_argument(
            "--entities",
            type=Path,
            default=None,
            help="Optional JSON list of entities (names, orgs, addresses, work titles)",
        )
        sp.add_argument(
            "--preserve",
            action="append",
            default=[],
            help="Exact string to leave unchanged (repeatable)",
        )
        sp.add_argument(
            "--keep-categories",
            action="append",
            default=[],
            metavar="CAT",
            help=(
                "Structural categories to NOT auto-redact (repeatable or comma-separated). "
                "Examples: uscc, bank_account, case_number, mobile, email, id_card, landline"
            ),
        )
        sp.add_argument(
            "--extra-categories",
            action="append",
            default=[],
            metavar="CAT",
            help="Extra structural categories to auto-redact beyond the mode default",
        )

    pr = sub.add_parser(
        "redact",
        help="Redact a document (or directory of documents) and write same-format output + ledger",
    )
    add_common(pr)
    pr.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file path, or output directory when input is a directory",
    )
    pr.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for ledger/residual/summary (default: output parent)",
    )
    pr.add_argument(
        "--recursive",
        action="store_true",
        help="When input is a directory, include supported files in subfolders",
    )
    pr.add_argument(
        "--allow-residual",
        action="store_true",
        help="Exit 0 even if residual scan finds remaining structural PII",
    )

    ps = sub.add_parser("scan", help="Detect structural PII / build plan without writing redacted file")
    add_common(ps)
    ps.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    pv = sub.add_parser("verify", help="Residual-scan an already redacted file")
    pv.add_argument("input", type=Path)
    pv.add_argument("--mode", choices=("ai", "production"), required=True)
    pv.add_argument(
        "--keep-categories",
        action="append",
        default=[],
        metavar="CAT",
        help="Same meaning as in redact/scan — residual ignores these categories",
    )
    pv.add_argument(
        "--extra-categories",
        action="append",
        default=[],
        metavar="CAT",
        help="Same meaning as in redact/scan",
    )

    pd = sub.add_parser(
        "draft-entities",
        help="Draft entities.json skeleton from structural hits + NL suspect hints",
    )
    pd.add_argument("input", type=Path, help="Input .docx / .pdf / .txt / .md")
    pd.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("entities.draft.json"),
        help="Output JSON path (default: entities.draft.json)",
    )
    pd.add_argument(
        "--no-suspects",
        action="store_true",
        help="Only dump structural hits (skip person/org/work-title heuristics)",
    )

    po = sub.add_parser(
        "ocr",
        help="OCR a scanned PDF to local markdown (requires Tesseract chi_sim)",
    )
    po.add_argument("input", type=Path, help="Scanned PDF")
    po.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for ocr.md / ocr.normalized.md / ocr_meta.json",
    )
    po.add_argument("--dpi", type=int, default=200)
    po.add_argument("--lang", default="chi_sim+eng")
    po.add_argument("--tesseract", type=Path, default=None, help="Path to tesseract.exe")
    po.add_argument("--tessdata", type=Path, default=None, help="TESSDATA_PREFIX directory")

    prs = sub.add_parser(
        "redact-scan",
        help="Black-box structural PII on a scanned PDF (court visual path; requires Tesseract)",
    )
    prs.add_argument("input", type=Path)
    prs.add_argument("-o", "--output", type=Path, required=True, help="Output PDF path")
    prs.add_argument(
        "--mode",
        choices=("ai", "production"),
        default="production",
        help="Category set (default production: strip contacts/accounts, keep parties visually)",
    )
    prs.add_argument("--dpi", type=int, default=200)
    prs.add_argument("--lang", default="chi_sim+eng")
    prs.add_argument("--work-dir", type=Path, default=None)
    prs.add_argument("--keep-categories", action="append", default=[], metavar="CAT")
    prs.add_argument("--extra-categories", action="append", default=[], metavar="CAT")
    prs.add_argument("--tesseract", type=Path, default=None)
    prs.add_argument("--tessdata", type=Path, default=None)

    return p


def _extract_any(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_io.extract_text(path)
    if suffix == ".pdf":
        return pdf_io.extract_text(path)
    return text_io.extract_text(path)


def _split_cats(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        out.extend(part.strip() for part in str(raw).split(",") if part.strip())
    return out


def _cmd_redact(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if src.is_dir():
        if args.output is None:
            print("ERROR: batch redact requires -o/--output directory", file=sys.stderr)
            return 1
        out = Path(args.output)
        if out.exists() and out.is_file():
            print("ERROR: batch output must be a directory, not a file", file=sys.stderr)
            return 1
        batch = redact_tree(
            input_dir=src,
            mode=args.mode,
            output_dir=out,
            entities_path=args.entities,
            preserve=args.preserve,
            work_dir=args.work_dir,
            keep_categories=_split_cats(args.keep_categories),
            extra_categories=_split_cats(args.extra_categories),
            recursive=args.recursive,
        )
        print(f"mode:     {batch.mode}")
        print(f"files:    {len(batch.results)}")
        print(f"skipped:  {len(batch.skipped)}")
        for result in batch.results:
            status = "PASS" if result.ok else "FAIL"
            print(
                f"  [{status}] {result.input_path.name} -> {result.output_path.name} "
                f"replaced={len(result.plan.entities)}"
            )
        for path, reason in batch.skipped:
            print(f"  [SKIP] {path.name}: {reason}", file=sys.stderr)
        if batch.skipped and not batch.results:
            return 1
        if batch.failed and not args.allow_residual:
            print(
                "ERROR: one or more files failed residual scan. "
                "Fix entities or use --allow-residual.",
                file=sys.stderr,
            )
            return 2
        return 0

    if not src.is_file():
        print(f"ERROR: input not found: {src}", file=sys.stderr)
        return 1

    result = redact_file(
        input_path=src,
        mode=args.mode,
        output_path=args.output,
        entities_path=args.entities,
        preserve=args.preserve,
        work_dir=args.work_dir,
        keep_categories=_split_cats(args.keep_categories),
        extra_categories=_split_cats(args.extra_categories),
    )
    print(f"mode:        {result.mode}")
    print(f"output:      {result.output_path}")
    print(f"ledger:      {result.ledger_path}")
    print(f"residual:    {result.residual_path}")
    if result.suspects_path:
        print(f"suspects:    {result.suspects_path} ({len(result.suspects)})")
    print(f"replaced:    {len(result.plan.entities)}")
    print(f"scan:        {result.residual.summary}")
    if result.suspects:
        print("suspect NL entities (NOT auto-redacted):")
        for s in result.suspects[:20]:
            print(f"  - [{s.category}/{s.role_hint}] {s.text} ({s.reason})")
        if len(result.suspects) > 20:
            print(f"  … {len(result.suspects) - 20} more in suspects file")
    if not result.ok and not args.allow_residual:
        print(
            "ERROR: residual structural PII remains. "
            "Add entities or fix mappings; use --allow-residual to override.",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "redact":
            return _cmd_redact(args)

        if args.command == "scan":
            if Path(args.input).is_dir():
                print("ERROR: scan expects a single file (not a directory)", file=sys.stderr)
                return 1
            payload = detect_only(
                args.input,
                mode=args.mode,
                entities_path=args.entities,
                preserve=args.preserve,
                keep_categories=_split_cats(args.keep_categories),
                extra_categories=_split_cats(args.extra_categories),
            )
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"mode:     {payload['mode']}")
                if payload.get("keep_categories"):
                    print(f"keep:     {', '.join(payload['keep_categories'])}")
                if payload.get("extra_categories"):
                    print(f"extra:    {', '.join(payload['extra_categories'])}")
                print(f"replace:  {payload['would_replace']} entities")
                cur = payload["current_structural_hits"]
                print(f"current:  {cur['summary']}")
                for e in payload["plan"]["entities"]:
                    print(f"  - [{e['category']}/{e['role']}] {e['original']} -> {e['replacement']}")
                suspects = payload.get("suspects") or []
                print(f"suspects: {len(suspects)} (hints only, not auto-redacted)")
                for s in suspects[:30]:
                    print(
                        f"  ? [{s['category']}/{s.get('role_hint', 'unknown')}] "
                        f"{s['text']} ({s.get('reason', '')})"
                    )
                if len(suspects) > 30:
                    print(f"  … {len(suspects) - 30} more")
            return 0

        if args.command == "verify":
            from .pipeline import _parse_category_list

            text = _extract_any(Path(args.input))
            keep = _parse_category_list(_split_cats(args.keep_categories))
            extra = _parse_category_list(_split_cats(args.extra_categories))
            report = scan_residual(text, mode=args.mode, keep_categories=keep, extra_categories=extra)
            print(report.summary)
            if report.hits:
                for h in report.hits:
                    print(f"  - {h['category']}: {h['text']}")
            return 0 if report.ok else 2

        if args.command == "draft-entities":
            from .draft import write_entities_draft

            payload = write_entities_draft(
                args.input,
                args.output,
                include_suspects=not args.no_suspects,
            )
            meta = payload.get("_meta") or {}
            print(
                f"wrote {args.output} "
                f"({len(payload.get('entities', []))} rows, "
                f"{meta.get('structural_unique', 0)} structural, "
                f"{meta.get('suspect_unique', 0)} suspects)"
            )
            print(
                "NOTE: suspect rows are hints only. Confirm role/replacement; "
                "nothing is auto-redacted until listed in entities for the chosen mode."
            )
            return 0

        if args.command == "ocr":
            from .ocr_engine import ocr_pdf

            result = ocr_pdf(
                args.input,
                args.output_dir,
                dpi=args.dpi,
                lang=args.lang,
                tesseract_cmd=args.tesseract,
                tessdata_dir=args.tessdata,
            )
            print(f"pages:      {len(result.pages)}")
            print(f"raw:        {result.raw_path}")
            print(f"normalized: {result.normalized_path}")
            print(f"meta:       {result.meta_path}")
            print(f"chars:      {len(result.text_normalized)}")
            print("next: legal-redactor redact <normalized.md> --mode production -o out.md")
            return 0

        if args.command == "redact-scan":
            from .pipeline import _parse_category_list
            from .scan_pdf import redact_scanned_pdf

            result = redact_scanned_pdf(
                args.input,
                args.output,
                mode=args.mode,
                dpi=args.dpi,
                lang=args.lang,
                keep_categories=_parse_category_list(_split_cats(args.keep_categories)),
                extra_categories=_parse_category_list(_split_cats(args.extra_categories)),
                tesseract_cmd=args.tesseract,
                tessdata_dir=args.tessdata,
                work_dir=args.work_dir,
            )
            print(f"mode:    {result.mode}")
            print(f"output:  {result.output_path}")
            print(f"hits:    {len(result.hits)}")
            print(f"bbox:    {result.hits_path}")
            print(
                "NOTE: best-effort OCR boxes. Human page-flip review required before court filing."
            )
            return 0

    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
