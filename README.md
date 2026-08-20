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

### Claude Code / Codex skill

Copy or junction `skills/legal-document-redactor` into `~/.claude/skills/` (or `~/.agents/skills/`).

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

# Residual check
legal-redactor verify contract.redacted-ai.docx --mode ai
```

Entity starter: `skills/legal-document-redactor/references/entities.template.json`.  
Optional structural draft: `python scripts/draft_entities.py INPUT.docx`.
Each successful run also writes:

- `*.ledger.json` — original→replacement map (**keep local; never commit or upload**)
- `*.residual.json` — structural residual report
- `*.summary.md` — human-readable table

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

## Limits (v0.2)

- PDF: text layer only (no OCR / scanned black-box pipeline)
- Natural-language names need `entities.json` (regex will not catch them reliably)
- DOCX paragraph rewrite may simplify run-level formatting
- Not legal advice; not a substitute for firm confidentiality procedure

## Security

Do not open issues with real client files, ledgers, or live matter identifiers. See [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
