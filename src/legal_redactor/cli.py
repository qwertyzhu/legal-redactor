"""CLI for legal-redactor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .formats import docx_io, pdf_io, text_io
from .pipeline import detect_only, redact_file
from .verify import scan_residual


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="legal-redactor",
        description=(
            "Local-first dual-mode redaction for Chinese legal documents. "
            "Modes: ai (aggressive, for online models) | production (selective, for court/opponent)."
        ),
    )
    p.add_argument("--version", action="version", version=f"legal-redactor {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("input", type=Path, help="Input .docx / .pdf / .txt / .md")
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

    pr = sub.add_parser("redact", help="Redact a document and write same-format output + ledger")
    add_common(pr)
    pr.add_argument("-o", "--output", type=Path, default=None, help="Output path (same suffix as input)")
    pr.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for ledger/residual/summary (default: output parent)",
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "redact":
            result = redact_file(
                input_path=args.input,
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
            print(f"replaced:    {len(result.plan.entities)}")
            print(f"scan:        {result.residual.summary}")
            if not result.ok and not args.allow_residual:
                print(
                    "ERROR: residual structural PII remains. "
                    "Add entities or fix mappings; use --allow-residual to override.",
                    file=sys.stderr,
                )
                return 2
            return 0

        if args.command == "scan":
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

    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
