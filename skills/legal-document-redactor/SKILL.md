---
name: legal-document-redactor
description: Redact Chinese legal documents (contracts, pleadings, client data) in dual modes—ai (aggressive desensitization before online AI upload) and production (selective redaction before court or opponent production)—returning the same file format (DOCX→DOCX, PDF→PDF, text→text) plus a local ledger and residual scan. Use whenever the user mentions 脱敏, 去标识, 匿名化, redact, anonymize, strip PII, 交给网上AI前处理, 出证前遮盖, or needs a privacy-safe copy of a legal file.
---

# Legal Document Redactor

Produce a **same-format** redacted copy of a local legal document, with an auditable replacement ledger and a residual structural-PII scan.

This skill drives the `legal-redactor` Python package. Keep judgment (what is a party name / work title) in the agent; keep replacement and verification deterministic in the CLI.

## Choose a mode first

| Mode | Use when | Keeps | Removes |
|---|---|---|---|
| `ai` | Upload to online AI, public fixtures, internal notes that must not identify the matter | Legal structure only | Names, orgs, case nos, IDs, phones, emails, accounts, USCC, addresses, work titles, exact amounts (via entities) |
| `production` | Produce to court or opposing party | Party identity, case number, operative commercial terms | ID numbers, phones, emails, bank accounts, USCC, and any entity you mark `third_party` |

Override structural defaults when needed:

- `--keep-categories uscc` — keep unified social credit codes on a filing that requires them
- `--keep-categories uscc,bank_account` — comma-separated or repeatable
- `--extra-categories case_number` — strip case numbers even in `production`

**Never** use `ai` mode output as a court filing.  
**Never** paste the ledger (original→replacement map) into an online model chat.

## Non-negotiable safeguards

1. Work on **local copies**. Do not overwrite the only original.
2. Output suffix **must** match input (`.docx`→`.docx`, `.pdf`→`.pdf`).
3. v0.3 PDF support:
   - **Text-layer PDF**: `redact` as before.
   - **Scanned / image-only PDF**: `ocr` then redact markdown for AI/text use; `redact-scan` for court visual black boxes. See [references/scanned-pdf.md](references/scanned-pdf.md).
4. Residual structural scan must **PASS** before delivery of text outputs (unless the user explicitly accepts residual risk). Visual `redact-scan` requires **human page-flip** (OCR boxes can miss).
5. Examples and tests in the repo are **fictional**. Do not commit real client ledgers or OCR dumps.
6. Human review is required. A passing scan does not prove every natural-language identifier was caught.

## Workflow

### 1. Confirm mode and paths

Ask if unclear:

- destination: online AI vs court/opponent;
- input path;
- whether party names must remain (`production`) or go (`ai`).

### 2. Extract and list entities (agent judgment)

Read the document (or run a local text extract). Build `entities.json`:

```json
{
  "entities": [
    {"original": "郝测一", "category": "person", "role": "party"},
    {"original": "北测文化传播有限公司", "category": "organization", "role": "party"},
    {"original": "《星河测例》", "category": "work_title", "role": "other", "replacement": "某作品"},
    {"original": "1280000", "category": "amount", "role": "other", "replacement": "X"}
  ]
}
```

Categories: `person` | `organization` | `address` | `work_title` | `amount` | `other`  
Roles: `party` | `third_party` | `counsel` | `other`

In `production` mode, `person` / `organization` / `address` with `role=party` and **no** `replacement` are **kept**.

Structural items (ID / mobile / email / bank / USCC / case no.) are auto-detected; you do not need to list them unless you want custom replacements.

Starter template: [references/entities.template.json](references/entities.template.json).  
See [references/methodology.md](references/methodology.md) and [schemas/entities.schema.json](schemas/entities.schema.json).

Optional local draft of structural rows + NL suspect hints:

```bash
legal-redactor draft-entities INPUT.docx -o entities.draft.json
# structural only: add --no-suspects
```

Suspect rows use `source=suspect-hint` and **no replacement**. Confirm `role` / `replacement` before AI-mode use. The tool never invents party names as final aliases without your confirmation.

Then fill or confirm natural-language names yourself.

### 3. Run deterministic redaction

From any directory (package installed):

```bash
legal-redactor redact INPUT.docx --mode ai --entities entities.json -o OUTPUT.docx
legal-redactor redact INPUT.pdf --mode production --entities entities.json -o OUTPUT.pdf
# court form needs USCC kept:
legal-redactor redact INPUT.docx --mode production --entities entities.json --keep-categories uscc -o OUTPUT.docx
```

Or via the repo wrapper:

```bash
python skills/legal-document-redactor/scripts/redact_cli.py redact INPUT.docx --mode ai --entities entities.json -o OUTPUT.docx
```

Artifacts written next to the output (or `--work-dir`):

- `*.ledger.json` — full mapping (**local only**)
- `*.residual.json` — structural residual scan
- `*.suspects.json` — NL entity hints (**not auto-redacted**)
- `*.summary.md` — human table

### 4. Verify

```bash
legal-redactor verify OUTPUT.docx --mode ai
```

Delivery checklist:

- [ ] mode matches destination
- [ ] same file format as input
- [ ] residual scan PASS
- [ ] spot-check party names (kept or removed as intended)
- [ ] ledger not uploaded anywhere
- [ ] user told: structural pass ≠ perfect NL anonymization

### 5. Report to the user

Return:

1. output path  
2. mode  
3. counts replaced  
4. residual status  
5. anything you could not confidently classify (ask, do not guess)

## CLI cheatsheet

```bash
# detect only
legal-redactor scan contract.docx --mode ai --entities entities.json

# redact
legal-redactor redact contract.docx --mode ai --entities entities.json -o contract.redacted-ai.docx

# keep an extra string unchanged
legal-redactor redact contract.docx --mode production --preserve "北京互联网法院" -o out.docx

# keep structural category (e.g. USCC) on production filings
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx

# residual verify must use the same keep/extra flags as redact
legal-redactor verify out.docx --mode production --keep-categories uscc

# batch a folder
legal-redactor redact ./inbox/ --mode ai --entities entities.json -o ./outbox/

# unify cross-file aliases (recommended before multi-doc AI upload)
legal-redactor unify ./inbox/ -o ./unified/ --mode ai
legal-redactor redact ./inbox/ --mode ai --entities ./unified/entities.consistent.json -o ./outbox/

# draft entities skeleton
legal-redactor draft-entities contract.docx -o entities.draft.json

# scanned PDF → text (for AI / notes)
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode ai --entities entities.json -o workdir/ai.md

# scanned PDF → visual black boxes (court production)
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted-production.pdf
```

## Limits (v0.6)

- PDF: text-layer via `redact`; scans via `ocr` / `redact-scan` (local Tesseract + chi_sim)
- `redact-scan` is OCR-box best-effort — **human page-flip before court filing**
- DOCX: single-run formatting preserved when possible; cross-run entities collapse the paragraph
- Natural-language names require confirmed entities JSON; suspects are review hints only
- Multi-doc matters: run `unify` (or reuse batch `entities.consistent.json`) so aliases stay stable
- Cross-file consistent aliases are per-run unless you reuse the same entities file
