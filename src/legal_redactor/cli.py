"""CLI for legal-redactor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .formats import docx_io, pdf_io, text_io
from .pipeline import detect_only, redact_file, redact_tree, scan_tree, verify_tree
from .verify import scan_residual


def _configure_stdio() -> None:
    """Keep CJK CLI output from crashing Windows consoles (cp1252 / charmap).

    A successful `redact` still prints suspect hints. On GitHub Actions
    windows-latest that encode step used to UnicodeEncodeError and exit 1
    after the files were already written.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            continue


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="legal-redactor",
        description=(
            "中国法律文书本地双模式脱敏："
            "ai=给在线模型去标识；production=交法院/对方前只去证件号、手机、邮箱。"
            "扫描件 PDF：先 `ocr` 再脱敏文本，或用 `redact-scan` 做视觉涂黑。"
            "`redact` 可传入目录做批量处理。"
        ),
    )
    p.add_argument("--version", action="version", version=f"legal-redactor {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser, *, require_mode: bool = True) -> None:
        sp.add_argument(
            "input",
            type=Path,
            help="输入文件，或批量脱敏时的目录",
        )
        if require_mode:
            sp.add_argument(
                "--mode",
                choices=("ai", "production"),
                required=True,
                help="ai=全面去标识；production=保留当事人，去掉证件号/手机/邮箱等",
            )
        sp.add_argument(
            "--entities",
            type=Path,
            default=None,
            help="可选的实体 JSON（姓名、单位、地址、作品名）",
        )
        sp.add_argument(
            "--preserve",
            action="append",
            default=[],
            help="保持原文不变的字符串（可重复）",
        )
        sp.add_argument(
            "--keep-categories",
            action="append",
            default=[],
            metavar="CAT",
            help=(
                "不自动脱敏的结构类别（可重复或逗号分隔）。"
                "例如：uscc, bank_account, case_number, mobile, email, id_card, landline"
            ),
        )
        sp.add_argument(
            "--extra-categories",
            action="append",
            default=[],
            metavar="CAT",
            help="在模式默认之外额外自动脱敏的结构类别",
        )

    pr = sub.add_parser(
        "redact",
        help="脱敏一份文书（或整个目录），写出同格式产物和 ledger",
    )
    add_common(pr)
    pr.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出文件路径；输入为目录时为输出目录",
    )
    pr.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="ledger / 残留报告 / 摘要的目录（默认：输出文件所在目录）",
    )
    pr.add_argument(
        "--recursive",
        action="store_true",
        help="输入为目录时，包含子目录中的支持文件",
    )
    pr.add_argument(
        "--unify",
        action="store_true",
        help=(
            "仅批量：先统一目录内实体，再用 entities.consistent.json 脱敏，保证替身稳定"
        ),
    )
    pr.add_argument(
        "--allow-residual",
        action="store_true",
        help="即使残留扫描仍发现结构性个人信息，也以退出码 0 结束",
    )

    ps = sub.add_parser("scan", help="只检测结构性个人信息 / 生成计划，不写出脱敏文件")
    add_common(ps)
    ps.add_argument("--json", action="store_true", help="打印机器可读 JSON")
    ps.add_argument(
        "--recursive",
        action="store_true",
        help="输入为目录时，包含子目录中的支持文件",
    )

    pv = sub.add_parser("verify", help="对已脱敏文件或目录做残留扫描")
    pv.add_argument("input", type=Path, help="已脱敏文件或目录")
    pv.add_argument("--mode", choices=("ai", "production"), required=True)
    pv.add_argument(
        "--keep-categories",
        action="append",
        default=[],
        metavar="CAT",
        help="含义与 redact/scan 相同：残留扫描忽略这些类别",
    )
    pv.add_argument(
        "--extra-categories",
        action="append",
        default=[],
        metavar="CAT",
        help="含义与 redact/scan 相同",
    )
    pv.add_argument(
        "--recursive",
        action="store_true",
        help="输入为目录时，包含子目录中的支持文件",
    )
    pv.add_argument("--json", action="store_true", help="打印机器可读 JSON（目录模式）")

    pd = sub.add_parser(
        "draft-entities",
        help="根据结构性命中和自然语言疑似提示，起草 entities.json 骨架",
    )
    pd.add_argument("input", type=Path, help="输入 .docx / .pdf / .txt / .md")
    pd.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("entities.draft.json"),
        help="输出 JSON 路径（默认：entities.draft.json）",
    )
    pd.add_argument(
        "--no-suspects",
        action="store_true",
        help="只导出结构性命中（跳过姓名/单位/作品名启发式）",
    )

    pu = sub.add_parser(
        "unify",
        help="为目录生成跨文件 entities.consistent.json 与一致性报告",
    )
    pu.add_argument("input", type=Path, help="源文书目录，或 *.ledger.json 所在目录")
    pu.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="entities.consistent.json 与 consistency.report.* 的输出目录",
    )
    pu.add_argument(
        "--mode",
        choices=("ai", "production"),
        default="ai",
        help="影响当事人行是否写入替身（默认 ai）",
    )
    pu.add_argument(
        "--entities",
        type=Path,
        default=None,
        help="可选的种子 entities.json，会并入统一集合",
    )
    pu.add_argument(
        "--from-ledgers",
        action="store_true",
        help="把输入当作 *.ledger.json 目录，而不是源文书目录",
    )
    pu.add_argument("--recursive", action="store_true", help="递归子目录（源文书模式）")
    pu.add_argument(
        "--no-suspects",
        action="store_true",
        help="扫描源文书时不纳入自然语言疑似提示",
    )

    po = sub.add_parser(
        "ocr",
        help="把扫描件 PDF OCR 成本地 Markdown（需 Tesseract chi_sim）",
    )
    po.add_argument("input", type=Path, help="扫描件 PDF")
    po.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="ocr.md / ocr.normalized.md / ocr_meta.json 的输出目录",
    )
    po.add_argument("--dpi", type=int, default=200)
    po.add_argument("--lang", default="chi_sim+eng")
    po.add_argument("--tesseract", type=Path, default=None, help="tesseract.exe 路径")
    po.add_argument("--tessdata", type=Path, default=None, help="TESSDATA_PREFIX 目录")

    prs = sub.add_parser(
        "redact-scan",
        help="对扫描件 PDF 做结构性个人信息涂黑（交法院视觉路径；需 Tesseract）",
    )
    prs.add_argument("input", type=Path, help="扫描件 PDF")
    prs.add_argument("-o", "--output", type=Path, required=True, help="输出 PDF 路径")
    prs.add_argument(
        "--mode",
        choices=("ai", "production"),
        default="production",
        help="类别集合（默认 production：去掉联系方式/账号，视觉上保留当事人）",
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
            unify_first=bool(getattr(args, "unify", False)),
        )
        print(f"mode:     {batch.mode}")
        print(f"files:    {len(batch.results)}")
        print(f"skipped:  {len(batch.skipped)}")
        if getattr(args, "unify", False):
            print("pass:     unify-first + redact")
        if batch.entities_consistent_path:
            print(f"unified:  {batch.entities_consistent_path}")
        if batch.consistency_path:
            print(f"consistency: {batch.consistency_path}")
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
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "redact":
            return _cmd_redact(args)

        if args.command == "scan":
            src = Path(args.input)
            if src.is_dir():
                payload = scan_tree(
                    src,
                    mode=args.mode,
                    entities_path=args.entities,
                    preserve=args.preserve,
                    keep_categories=_split_cats(args.keep_categories),
                    extra_categories=_split_cats(args.extra_categories),
                    recursive=args.recursive,
                )
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(f"mode:  {payload['mode']}")
                    print(f"files: {payload['files']}")
                    for row in payload["results"]:
                        print(
                            f"  - {row['name']}: replace={row['would_replace']} "
                            f"suspects={row.get('suspect_count', 0)} "
                            f"{row['current_structural_hits']['summary']}"
                        )
                return 0
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

            src = Path(args.input)
            keep = _parse_category_list(_split_cats(args.keep_categories))
            extra = _parse_category_list(_split_cats(args.extra_categories))
            if src.is_dir():
                payload = verify_tree(
                    src,
                    mode=args.mode,
                    keep_categories=keep,
                    extra_categories=extra,
                    recursive=args.recursive,
                )
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(f"mode:   {payload['mode']}")
                    print(f"files:  {payload['files']}")
                    print(f"failed: {payload['failed']}")
                    for row in payload["results"]:
                        status = "PASS" if row["ok"] else "FAIL"
                        print(f"  [{status}] {row['name']}: {row['summary']}")
                        for h in row.get("hits") or []:
                            print(f"    - {h['category']}: {h['text']}")
                return 0 if payload["ok"] else 2

            text = _extract_any(src)
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

        if args.command == "unify":
            from .consistency import unify_directory, unify_from_ledgers

            if args.from_ledgers:
                report = unify_from_ledgers(args.input, args.output_dir, mode=args.mode)
            else:
                if not Path(args.input).is_dir():
                    print("ERROR: unify expects a directory of source documents", file=sys.stderr)
                    return 1
                report = unify_directory(
                    args.input,
                    args.output_dir,
                    mode=args.mode,
                    entities_path=args.entities,
                    recursive=args.recursive,
                    include_suspects=not args.no_suspects,
                )
            print(f"files:      {report.files_scanned}")
            print(f"entities:   {report.entity_count}")
            print(f"conflicts:  {report.conflict_count}")
            print(f"unified:    {report.entities_path}")
            print(f"report:     {report.report_path}")
            if report.conflicts:
                print("CONFLICTS (same original → different replacements):", file=sys.stderr)
                for c in report.conflicts:
                    print(
                        f"  - {c.original}: {', '.join(c.replacements)}",
                        file=sys.stderr,
                    )
                return 2
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
