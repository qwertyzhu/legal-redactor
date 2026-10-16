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
                default=None,
                help="ai=全面去标识；production=保留当事人，去掉证件号/手机/邮箱等（与 --profile 二选一）",
            )
            sp.add_argument(
                "--profile",
                default=None,
                help="脱敏等级配置：ai | production | publish | minimal（覆盖 --mode）",
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
                "例如：uscc, bank_account, case_number, mobile, email, id_card, landline, "
                "passport, hk_mo_permit, plate, property_cert, wechat_qq, ip, birth_date"
            ),
        )
        sp.add_argument(
            "--extra-categories",
            action="append",
            default=[],
            metavar="CAT",
            help="在模式默认之外额外自动脱敏的结构类别",
        )
        sp.add_argument(
            "--allowlist",
            action="append",
            default=[],
            type=Path,
            metavar="FILE",
            help="白名单文件（每行一个必须原样保留的字符串，可重复）；另自动读取 .redactor/allow.txt",
        )
        sp.add_argument(
            "--no-default-allowlist",
            action="store_true",
            help="不加载内置法律/法院白名单",
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
        "--auto-confident",
        action="store_true",
        help=(
            "一次扫完高置信姓名、单位、地址和作品名（ai），"
            "或第三人姓名（production）。不必先让模型手写 entities.json"
        ),
    )
    pr.add_argument(
        "--allow-residual",
        action="store_true",
        help="即使残留扫描仍发现结构性个人信息，也以退出码 0 结束",
    )
    pr.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="ledger 路径（文件或目录）。默认写到 ~/.legal-redactor/ledgers/<hash>/（0600）",
    )
    pr.add_argument(
        "--ledger-beside-output",
        action="store_true",
        help="旧行为：把 *.ledger.json 写在输出旁（不推荐）",
    )
    pr.add_argument(
        "--ledger-encrypt",
        action="store_true",
        help="加密 ledger（需 cryptography；口令取 LEGAL_REDACTOR_LEDGER_PASS）",
    )
    pr.add_argument(
        "--preserve-hyperlinks",
        action="store_true",
        help="docx：不清理外部超链接 / mailto 目标",
    )
    pr.add_argument(
        "--preserve-bookmarks",
        action="store_true",
        help="pdf：保留书签（书签标题不脱敏，需人工检查）",
    )
    pr.add_argument(
        "--revisions",
        choices=("accept", "reject", "keep-and-redact"),
        default="accept",
        help="docx 修订：accept=接受修订（默认）；reject=拒绝修订；keep-and-redact=保留修订并脱敏 w:ins/w:del 内文字",
    )
    pr.add_argument(
        "--redact-court-staff",
        action="store_true",
        default=None,
        help="同时替换审判长/审判员/人民陪审员/书记员/法官助理/执行员姓名（默认保留；profile 键 redact_court_staff）",
    )
    pr.add_argument(
        "--ocr-images",
        choices=("auto", "on", "off"),
        default="auto",
        help="内嵌图片 OCR（需 Tesseract）：auto=有则用（默认）；on=缺失即报错；off=只列出提醒",
    )
    pr.add_argument("--json", action="store_true", help="打印机器可读 JSON 结果")

    ps = sub.add_parser("scan", help="只检测结构性个人信息 / 生成计划，不写出脱敏文件")
    add_common(ps)
    ps.add_argument("--json", action="store_true", help="打印机器可读 JSON")
    ps.add_argument(
        "--recursive",
        action="store_true",
        help="输入为目录时，包含子目录中的支持文件",
    )

    pv = sub.add_parser(
        "verify",
        help="对已脱敏文件或目录做残留扫描（退出码：0=PASS，1=WARN 且 --strict，2=FAIL）",
    )
    pv.add_argument("input", type=Path, help="已脱敏文件或目录")
    pv.add_argument("--mode", choices=("ai", "production"), default=None)
    pv.add_argument("--profile", default=None, help="与 redact 使用的 profile 一致")
    pv.add_argument(
        "--strict",
        action="store_true",
        help="WARN（低置信残留）也以退出码 1 结束",
    )
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
    pv.add_argument(
        "--allowlist",
        action="append",
        default=[],
        type=Path,
        metavar="FILE",
        help="与 redact 相同的白名单：ai 模式人名/单位残留检查跳过这些字符串",
    )
    pv.add_argument(
        "--preserve",
        action="append",
        default=[],
        help="与 redact 相同的 --preserve（ai 模式残留检查跳过）",
    )
    pv.add_argument(
        "--redact-court-staff",
        action="store_true",
        help="与 redact 相同：法院人员姓名也算残留",
    )
    pv.add_argument("--json", action="store_true", help="打印机器可读 JSON")

    prr = sub.add_parser(
        "restore",
        help="用本地 ledger 把替身还原为原文（例如在线 AI 的回复）",
    )
    prr.add_argument("input", type=Path, help="含替身的文件（.txt/.md/.docx/.pdf）")
    prr.add_argument("--ledger", type=Path, required=True, help="ledger.json / ledger.json.enc 或其目录")
    prr.add_argument("-o", "--output", type=Path, required=True, help="还原后的输出路径")
    prr.add_argument("--ledger-password", default=None, help="加密 ledger 口令（也可用环境变量）")
    prr.add_argument("--json", action="store_true", help="打印机器可读 JSON")

    plp = sub.add_parser("profiles", help="列出内置脱敏等级配置")
    plp.add_argument("--json", action="store_true")

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
    prs.add_argument(
        "--redact-party",
        choices=("a", "b", "both"),
        default=None,
        help="整方脱敏：a=甲方，b=乙方，both=双方；必须同时提供 --party-spec",
    )
    prs.add_argument(
        "--party-spec",
        type=Path,
        default=None,
        help="已人工确认的当事方标识与公章/签署区规范 JSON（仅本地）",
    )
    prs.add_argument(
        "--also-redact-structural-all",
        action="store_true",
        help="整方脱敏之外，仍按 mode 遮挡所有各方的结构性号码（默认只遮所选方）",
    )

    psd = sub.add_parser(
        "seal-draft",
        help="（可选 OpenCV）检测红色圆形公章，输出待人工确认的区域草稿 JSON，不做任何遮挡",
    )
    psd.add_argument("input", type=Path, help="PDF 或图片")
    psd.add_argument("-o", "--output", type=Path, default=None, help="草稿 JSON 路径")
    psd.add_argument("--dpi", type=int, default=150)

    pdoc = sub.add_parser("doctor", help="检查可选组件：Tesseract / OpenCV / NER")
    pdoc.add_argument("--json", action="store_true")

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


def _resolve_mode_profile(args: argparse.Namespace) -> tuple[str, str | None]:
    profile = getattr(args, "profile", None)
    mode = getattr(args, "mode", None)
    if profile:
        from .profiles_loader import load_profile

        prof = load_profile(profile)
        return prof.mode, prof.name
    if not mode:
        raise SystemExit("ERROR: --mode 或 --profile 必须提供一个")
    return mode, None


def _cmd_redact(args: argparse.Namespace) -> int:
    args.mode, profile_name = _resolve_mode_profile(args)
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
            auto_confident=bool(getattr(args, "auto_confident", False)),
            allowlist_files=list(getattr(args, "allowlist", []) or []),
            use_default_allowlist=not getattr(args, "no_default_allowlist", False),
            profile=profile_name,
            ledger_encrypt=bool(getattr(args, "ledger_encrypt", False)),
            preserve_hyperlinks=bool(getattr(args, "preserve_hyperlinks", False)),
            preserve_bookmarks=bool(getattr(args, "preserve_bookmarks", False)),
            revisions=getattr(args, "revisions", "accept"),
            ocr_images=getattr(args, "ocr_images", "auto"),
            redact_court_staff=getattr(args, "redact_court_staff", None),
        )
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "mode": batch.mode,
                        "ok": batch.ok,
                        "files": [
                            {
                                "input": str(r.input_path),
                                "output": str(r.output_path),
                                "status": r.status,
                                "replaced": len(r.plan.entities),
                                "ledger": str(r.ledger_path),
                                "audit": str(r.audit_json_path) if r.audit_json_path else None,
                            }
                            for r in batch.results
                        ],
                        "skipped": [{"input": str(p), "reason": why} for p, why in batch.skipped],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if batch.skipped and not batch.results:
                return 1
            return 2 if (batch.failed and not args.allow_residual) else 0
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
            status = result.status
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
        auto_confident=bool(getattr(args, "auto_confident", False)),
        allowlist_files=list(getattr(args, "allowlist", []) or []),
        use_default_allowlist=not getattr(args, "no_default_allowlist", False),
        profile=profile_name,
        ledger=getattr(args, "ledger", None),
        ledger_encrypt=bool(getattr(args, "ledger_encrypt", False)),
        ledger_beside_output=True if getattr(args, "ledger_beside_output", False) else None,
        preserve_hyperlinks=bool(getattr(args, "preserve_hyperlinks", False)),
        preserve_bookmarks=bool(getattr(args, "preserve_bookmarks", False)),
        revisions=getattr(args, "revisions", "accept"),
        ocr_images=getattr(args, "ocr_images", "auto"),
        redact_court_staff=getattr(args, "redact_court_staff", None),
    )
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "mode": result.mode,
                    "profile": result.profile,
                    "status": result.status,
                    "ok": result.ok,
                    "output": str(result.output_path),
                    "ledger": str(result.ledger_path),
                    "residual": str(result.residual_path),
                    "suspects": str(result.suspects_path) if result.suspects_path else None,
                    "audit_json": str(result.audit_json_path) if result.audit_json_path else None,
                    "audit_md": str(result.audit_md_path) if result.audit_md_path else None,
                    "replaced": len(result.plan.entities),
                    "suspect_count": len(result.suspects),
                    "warnings": list(result.warnings),
                    "summary": result.residual.summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if (result.ok or args.allow_residual) else 2
    print(f"mode:        {result.mode}")
    print(f"output:      {result.output_path}")
    print(f"ledger:      {result.ledger_path}")
    print(f"residual:    {result.residual_path}")
    if result.suspects_path:
        print(f"suspects:    {result.suspects_path} ({len(result.suspects)})")
    print(f"replaced:    {len(result.plan.entities)}")
    print(f"scan:        {result.residual.summary}")
    for h in result.residual.hits:
        print(f"  - {h['category']}: {h['text']}")
    for h in result.residual.warnings[:20]:
        print(f"  ? {h['category']}: {h['text']} ({h.get('notes', '')})")
    print(f"status:      {result.status}")
    if result.audit_md_path:
        print(f"audit:       {result.audit_md_path}")
    for w in result.warnings:
        print(f"WARNING:     {w}")
    if result.suspects:
        print("suspect NL entities (NOT auto-redacted):")
        for s in result.suspects[:20]:
            print(f"  - [{s.category}/{s.role_hint}] {s.text} ({s.reason})")
        if len(result.suspects) > 20:
            print(f"  … {len(result.suspects) - 20} more in suspects file")
    if not result.ok and not args.allow_residual:
        print(
            "ERROR: residual PII remains (structural, or a planned replacement "
            "missing from the output). Add entities or fix mappings; "
            "use --allow-residual to override.",
            file=sys.stderr,
        )
        return 2
    return 0


def _cmd_seal_draft(args: argparse.Namespace) -> int:
    from .image_scan import detect_seals, seal_detection_available, seal_draft_for_pdf

    src = Path(args.input)
    if not seal_detection_available():
        print("seal-draft 需要 OpenCV：pip install 'legal-redactor[vision]'", file=sys.stderr)
        return 3
    if src.suffix.lower() == ".pdf":
        payload = seal_draft_for_pdf(src, dpi=args.dpi)
    else:
        payload = {
            "draft": True,
            "available": True,
            "source": str(src),
            "regions": [
                {"page": 1, "rect": s["rect"], "category": "seal", "note": "DRAFT seal"}
                for s in detect_seals(src.read_bytes())
            ],
        }
    out = args.output or src.with_name(f"{src.stem}.seal-draft.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"seal drafts: {len(payload['regions'])} -> {out}（仅草稿，需人工确认后写入 party spec）")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .image_scan import ocr_available, seal_detection_available
    from .ner import ner_status

    info = {
        "tesseract": ocr_available(),
        "opencv": seal_detection_available(),
        "ner": ner_status(),
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for k, v in info.items():
            print(f"{k}: {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "redact":
            return _cmd_redact(args)

        if args.command == "seal-draft":
            return _cmd_seal_draft(args)

        if args.command == "doctor":
            return _cmd_doctor(args)

        if args.command == "scan":
            args.mode, _scan_profile = _resolve_mode_profile(args)
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
                    allowlist_files=list(getattr(args, "allowlist", []) or []),
                    use_default_allowlist=not getattr(args, "no_default_allowlist", False),
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
                allowlist_files=list(getattr(args, "allowlist", []) or []),
                use_default_allowlist=not getattr(args, "no_default_allowlist", False),
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
            from .verify import exit_code_for_status

            vmode = args.mode
            only = None
            if getattr(args, "profile", None):
                from .profiles_loader import load_profile

                prof = load_profile(args.profile)
                vmode = prof.mode
                only = prof.categories
            if not vmode:
                print("ERROR: --mode 或 --profile 必须提供一个", file=sys.stderr)
                return 2
            src = Path(args.input)
            keep = _parse_category_list(_split_cats(args.keep_categories))
            extra = _parse_category_list(_split_cats(args.extra_categories))
            strict = bool(getattr(args, "strict", False))
            staff = bool(getattr(args, "redact_court_staff", False))
            if src.is_dir():
                payload = verify_tree(
                    src,
                    mode=vmode,
                    keep_categories=keep,
                    extra_categories=extra,
                    recursive=args.recursive,
                    only_categories=only,
                    redact_court_staff=staff,
                    allowlist_files=list(args.allowlist or []),
                )
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    print(f"mode:   {payload['mode']}")
                    print(f"files:  {payload['files']}")
                    print(f"failed: {payload['failed']}")
                    print(f"warned: {payload['warned']}")
                    print(f"status: {payload['status']}")
                    for row in payload["results"]:
                        print(f"  [{row['status']}] {row['name']}: {row['summary']}")
                        for h in row.get("hits") or []:
                            print(f"    - {h['category']}: {h['text']}")
                status = payload["status"]
            else:
                from .pipeline import resolve_allowlist

                text = _extract_any(src)
                report = scan_residual(
                    text,
                    mode=vmode,
                    keep_categories=keep,
                    extra_categories=extra,
                    only_categories=only,
                    nl=True,
                    allowlist=resolve_allowlist(src, list(args.allowlist or [])),
                    known={p.strip() for p in (args.preserve or []) if p.strip()},
                    redact_court_staff=staff,
                )
                if args.json:
                    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
                else:
                    print(report.summary)
                    print(f"status: {report.status}")
                    for h in report.hits:
                        print(f"  - {h['category']}: {h['text']}")
                    for h in report.warnings:
                        print(f"  ? {h['category']}: {h['text']} ({h.get('notes', '')})")
                status = report.status
            if status == "WARN" and not strict:
                return 0
            return exit_code_for_status(status)

        if args.command == "restore":
            from .ledger_store import restore_file

            out_text, warns = restore_file(
                args.input,
                args.ledger,
                args.output,
                password=getattr(args, "ledger_password", None),
            )
            if args.json:
                print(
                    json.dumps(
                        {"output": str(args.output), "warnings": warns, "ok": True},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"restored: {args.output}")
                for w in warns:
                    print(f"WARNING:  {w}")
            return 0

        if args.command == "profiles":
            from .profiles_loader import list_profiles, load_profile

            rows = [load_profile(n).to_dict() for n in list_profiles()]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for r in rows:
                    print(f"{r['name']:<11} mode={r['mode']:<10} alias={r['alias_style']:<7} {r['description']}")
            return 0

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
                redact_party=args.redact_party,
                party_spec_path=args.party_spec,
                also_redact_structural_all=args.also_redact_structural_all,
            )
            print(f"mode:    {result.mode}")
            print(f"output:  {result.output_path}")
            print(f"hits:    {len(result.hits)}")
            if result.party_scope:
                print(f"party:   {result.party_scope}")
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
