[English](README.md) | [简体中文](README.zh-CN.md)

# legal-redactor

[![CI](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml/badge.svg)](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/qwertyzhu/legal-redactor)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](pyproject.toml)

**Local-first dual-mode redaction for Chinese legal documents.**  
Same format out as in: DOCX→DOCX, PDF→PDF (text-layer), text→text.  
Built for lawyers who need (1) a privacy-safe copy before pasting into online AI, and (2) a selective production copy before court or opponent disclosure.

> Early preview. Does not replace lawyer judgment. A passing residual scan is not proof of perfect anonymization.

## Two modes

| Mode | Destination | Default behavior |
|---|---|---|
| `ai` | Online models, shareable notes, public fixtures | Aggressive: names, orgs, case numbers, IDs, phones, emails, accounts, USCC + agent-listed entities |
| `production` | Court / opposing party | Keep parties & case numbers; strip IDs, phones, emails, bank accounts, USCC; optional third-party entities |

Agent (or you) supplies person/org/work-title entities. The CLI applies replacements deterministically and residual-scans structural PII.

## Install

```console
git clone https://github.com/qwertyzhu/legal-redactor.git
cd legal-redactor
python -m pip install -e ".[dev]"
```

Or install from the latest [GitHub Release](https://github.com/qwertyzhu/legal-redactor/releases/latest).

### Claude Code / Codex skill

Copy or junction `skills/legal-document-redactor` into `~/.claude/skills/` (or `~/.agents/skills/`).  
Release assets also include a packed `legal-document-redactor.skill` plus `SHA256SUMS.txt`.

Codex:

```text
$skill-installer
Install skill from qwertyzhu/legal-redactor:
- skills/legal-document-redactor
```

## Quick start

```console
# Scan only
legal-redactor scan contract.docx --mode ai

# Redact for online AI (entities JSON optional but recommended for names)
legal-redactor redact contract.docx --mode ai --entities entities.json -o contract.redacted-ai.docx

# Redact for court/opponent production
legal-redactor redact contract.docx --mode production --entities entities.json -o contract.redacted-production.docx

# Keep USCC on a filing that requires it
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx
legal-redactor verify out.docx --mode production --keep-categories uscc

# Scanned PDF (no text layer)
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode production -o workdir/out.md
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted-production.pdf

# Residual check
legal-redactor verify contract.redacted-ai.docx --mode ai

# Draft entities skeleton (structural + NL suspect hints)
legal-redactor draft-entities contract.docx -o entities.draft.json

# Batch a folder of documents
legal-redactor redact ./matters/ --mode ai --entities entities.json -o ./matters-redacted/

# Unify aliases across a matter folder (before or after batch)
legal-redactor unify ./matters/ -o ./matter-unified/ --mode ai
legal-redactor unify ./matters-redacted/ -o ./check/ --from-ledgers
```

Each successful `redact` also writes:

- `*.ledger.json` — original→replacement map (**keep local; never commit or upload**)
- `*.residual.json` — structural residual report
- `*.suspects.json` — natural-language entity **hints** (not auto-redacted)
- `*.summary.md` — human-readable table

Scanned workflow: `skills/legal-document-redactor/references/scanned-pdf.md`.  
Entity starter: `skills/legal-document-redactor/references/entities.template.json`.  
Optional structural + suspect draft: `legal-redactor draft-entities INPUT.docx`.
Each successful run also writes:

- `*.ledger.json` — original→replacement map (**keep local; never commit or upload**)
- `*.residual.json` — structural residual report
- `*.suspects.json` — NL suspect hints (not auto-redacted)
- `*.summary.md` — human-readable table

Batch also writes `entities.consistent.json` + `consistency.report.*` under the work dir.

## Repository demo (fictional only)

```console
python scripts/run_demo.py --clean
pytest
```

Demo inputs are completely fictional. Any resemblance to real parties is coincidental.

## Architecture

```text
Document → extract text → merge structural detectors + entities.json
        → longest-first replace → write same format
        → residual structural scan → human review
```

Details: [skills/legal-document-redactor/references/methodology.md](skills/legal-document-redactor/references/methodology.md)

## Limits (v0.6)

- PDF: text-layer via `redact`; scans via `ocr` / `redact-scan` (local Tesseract + chi_sim)
- `redact-scan` is OCR-box best-effort — **human page-flip before court filing**
- Natural-language names need confirmed `entities.json`; suspects are hints only
- Cross-file stable aliases require `unify` or reusing `entities.consistent.json`
- DOCX: single-run formatting is preserved when possible; cross-run entities still collapse the paragraph
- Batch redact is flat output names (recursive mode disambiguates colliding basenames)
- Not legal advice; not a substitute for firm confidentiality procedure

## Security

Do not open issues with real client files, ledgers, or live matter identifiers. See [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
