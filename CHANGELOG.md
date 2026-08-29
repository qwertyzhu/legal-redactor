# Changelog

## Unreleased

Landing-page and installability.

- Bilingual README: 60-second clone/install/demo, fictional `ai` vs `production` before/after (party name + mobile), single sidecar-file list.
- Package `keywords` and `project.urls`.
- Pytest drives the shipped CLI (`python -m legal_redactor`) on the fictional contract in both modes.
- Windows/cp1252: CLI reconfigures stdout/stderr to UTF-8 (`errors=replace`) at process start so printing CJK suspect hints after a successful `redact` no longer exits 1.
- GitHub About / package description: Chinese-first one-liner (AI upload vs court/opponent production).
- CLI imports `pymupdf` (not deprecated `fitz`), so `--version` no longer prints a deprecation warning on PyMuPDF 1.28+.
- README dual-mode preview PNG rendered from the fictional contract PDFs; issue templates warn against pasting live matters.
- PyPI trusted-publisher workflow is dispatch-only until a publisher is linked; `pip install git+https://...` documented.

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
