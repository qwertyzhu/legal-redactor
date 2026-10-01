# Changelog

## Unreleased

## 0.10.3 - 2026-10-01

仅 Skill 文档与模板增强；Python 包不变（仍为 0.10.2）。

- Skill 新增「指哪打哪」选择性脱敏决策表：`--keep-categories` 反向枚举、点名人名 entities、`--preserve`、整方 `--redact-party` 的选用对照。
- Skill 新增提速工作流：OCR 只跑一次后在 markdown 上迭代；`--dpi 200`；批量 `--unify`；`verify` 退出码机器化验收。
- Skill 新增环境自检（CLI 在 PATH、Tesseract chi_sim）。
- 新增 `references/redaction-standards.md`：法释〔2016〕19号、个人信息保护法、GB/T 37964-2019、上海律协 AI 指引（2026）与工具参数对照；收录工具识别盲区（出生日期、车牌号、不动产权证号、无锚点姓名）。
- 新增 `references/entities.court-style.template.json`：裁判文书上网式隐名模板（保留姓氏+某、住所地到县/区、金额模糊化）。
- 交付清单新增「反推三问」验收项。

## 0.10.2 - 2026-09-28

- Release 增加 `legal-document-redactor-skillhub.zip`。压缩包根目录就是 `SKILL.md`，可直接上传 Skillhub。不要上传整仓 zip，也不要上传 `.skill`。

## 0.10.1 - 2026-09-28

- Skill 入口改成先跑 `redact --auto-confident`。技能卡片和默认提示不再要求先通读全文。

## 0.10.0 - 2026-09-27

一次扫完发布：不必先让模型手写实体表。

- `redact --auto-confident`：一次扫完高置信姓名、单位、地址、作品名和精确金额，不必先让模型手写 `entities.json`。`production` 仍保留当事人，只自动去掉第三人。
- 结构性识别补上带空格或短横线的手机号、身份证号，以及「账号 / 收款账户」引导的账号。纯数字串不再误判为统一社会信用代码。
- 简称（以下简称「…」）与全称共用同一个替身。
- 文档：移除中英文 README 中过期的 `v0.8` 限制标题，并补齐整方脱敏的 `party-spec`、整枚公章和未选方保留边界。

## 0.9.0 - 2026-09-01

整方脱敏发布：按用户选择遮挡甲方、乙方或双方，并完整覆盖名称、签名及公章。

- 默认 README、社区文件、CLI `--help` 与 Skill 入口改为简体中文优先；英文旁路为 `README.en.md`。
- 扫描件新增整方脱敏：用户可选择只遮甲方、只遮乙方或双方；用已确认标识遮挡名称、联系方式和账号，并用人工复核区域覆盖签名及整枚公章。未选方默认完整保留。
- `redact-scan` 新增 `--redact-party`、`--party-spec` 和可选的 `--also-redact-structural-all`，附虚构模板、Skill 路由说明及回归测试。

## 0.8.0 - 2026-08-29

可用性发布：Windows CLI、可读的 PDF 占位符、面向中文用户的落地页。

- Windows/cp1252：进程启动时把 stdout/stderr 重配为 UTF-8（`errors=replace`），`redact` 成功后打印中文疑似提示不再以退出码 1 结束。
- PDF `redact` 用 PyMuPDF `china-s` 绘制中文替身（`某甲`、`[手机号]`），不再出现 Helvetica `???`。
- CLI 导入 `pymupdf`（不再用已弃用的 `fitz`）；PyMuPDF 1.28+ 上 `--version` 干净。
- 双语 README：60 秒演示、虚构 `ai` / `production` 前后对照、双模式预览图。
- GitHub About 中文优先。Issue 模板禁止粘贴真实卷宗。
- GitHub Release 除 `.skill` 外附带 wheel 与 sdist。
- Pytest 对虚构合同走真实 CLI 双模式，含 cp1252 stdio。

## 0.7.0 - 2026-08-22

Batch two-pass workflow and directory operators.

- `legal-redactor redact DIR --unify` unifies entities first, then redacts every file with `entities.consistent.json`.
- `scan` and `verify` accept directories (optional `--recursive`, `--json`).
- Suspect heuristics add label-anchored **addresses** (`住所地`/`住址`/`地址`…).
- Tests cover unify-first batch stability, directory verify/scan, and address suspects.

## 0.6.0 - 2026-08-22

Cross-file consistency for multi-document matters.

- Added `legal-redactor unify DIR -o OUTDIR` to build `entities.consistent.json` with stable aliases across a folder.
- Detects replacement conflicts (same original → different replacements) from source scans or `*.ledger.json`.
- Batch `redact DIR` now writes `entities.consistent.json`, `consistency.report.json`, and `consistency.report.md`.
- Production mode unified rows may omit replacements for party person/org/address (intentional keep).

## 0.5.0 - 2026-08-21

Reviewability release: surface likely natural-language entities without guessing replacements.

- Added heuristic **suspect** detection (role-anchored persons, org suffixes, 《work titles》).
- `scan` prints suspect hints; `redact` writes `*.suspects.json` + summary section.
- `draft-entities` includes suspect rows (`source=suspect-hint`) by default; `--no-suspects` to disable.
- Suspects are **never auto-redacted** — agent/human must confirm role and optional replacement.
- False positives/negatives expected; blocklists cover common court labels and short bank labels.

## 0.4.0 - 2026-08-21

Usability release for real multi-file workflows. Redaction modes and structural
detectors are unchanged.

- Batch `legal-redactor redact DIR -o OUTDIR --mode …` over supported files (optional `--recursive`).
- New `legal-redactor draft-entities` subcommand (scripts/draft_entities.py is now a thin wrapper).
- DOCX: prefer per-run replacements so single-run bold/italic survives; cross-run entities still fall back to paragraph collapse.
- Regression tests for batch redaction, draft-entities, and mixed-run DOCX formatting.

## 0.3.1 - 2026-08-21

Patch release: ships the post-0.3.0 distribution and CI hardening already intended for users.
No change to redaction modes, structural detectors, or OCR behavior.

- Expanded CI to Python 3.10–3.12 on Ubuntu, Windows, and macOS.
- Added a reproducible `.skill` packer (`scripts/pack_skill.py`), `SHA256SUMS.txt`, and tag-triggered GitHub Releases.
- Aligned software versions across `pyproject.toml`, package `__version__`, and `.codex-plugin/plugin.json`.
- Generalized public-tree safety scan beyond filename denylist (emails, mobiles, IDs, case numbers, home paths).
- Corrected skill package limits text for v0.3 OCR / `redact-scan` support.
- Documented release steps in CONTRIBUTING.

## 0.3.0 - 2026-08-20

- Added `legal-redactor ocr` for local Tesseract OCR of scanned PDFs → `ocr.md` + CJK-normalized markdown.
- Added `legal-redactor redact-scan` for production court path: black-box structural PII on image-only PDFs via OCR word boxes.
- Documented scanned-PDF workflow under the skill package (`references/scanned-pdf.md`).
- Clearer error when `redact` is pointed at a textless PDF (points to `ocr` / `redact-scan`).

## 0.2.0 - 2026-08-20

- Added `--keep-categories` / `--extra-categories` on `redact`, `scan`, and `verify` so production filings can keep USCC (or other structural types) without failing residual scan.
- Added fictional full-risk 委托维权服务协议 sample + entities (`examples/fictional/sample_weiquan_text.py`).
- Added entities template under the skill package and `scripts/draft_entities.py` for structural drafts.
- Fixed DOCX handling for Word track-changes (`w:ins` / `w:del`) and run-split strings by flattening revisions and replacing at paragraph level in OOXML.
- Added fictional track-changes regression fixture.
- Declared `lxml` as a direct dependency.

## 0.1.0 - 2026-08-20

- Initial public preview of `legal-redactor`.
- Dual modes: `ai` (aggressive) and `production` (selective for court/opponent).
- Same-format outputs for DOCX, text-layer PDF, and text/markdown.
- Structural detectors: PRC ID, mobile/landline, email, bank account, case number, USCC.
- Agent/manual `entities.json` with stable aliases and role-aware production keeps.
- Residual structural scan, local ledger/summary artifacts.
- Claude/Codex skill package under `skills/legal-document-redactor`.
- Fictional fixtures, pytest suite, and `scripts/run_demo.py`.
