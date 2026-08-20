# Redaction methodology (PRC legal practice, tool scope)

## Two destinations, two standards

### A. Online AI (`ai`)

Goal: the model cannot re-identify the matter from the text alone.

Minimum bar (aligned with common law-firm confidentiality practice):

- person names → stable aliases (`某甲`)
- organization full names → `某单位A`
- case numbers → `（20XX）XX民初XX号`
- ID / phone / email / bank account / USCC → structural placeholders or delete
- addresses → `某地址N`
- work titles in copyright matters → `某作品` (title often *is* the matter key)
- exact amounts → magnitude or `X` when amount+cause+date would re-identify via public judgments

### B. Court / opponent production (`production`)

Goal: legally usable document that does not gratuitously expose high-risk personal data.

Default keep:

- party names and party organizations
- case numbers
- operative clauses and commercial figures needed to prove the claim

Default remove:

- resident ID numbers
- personal mobiles / emails
- bank account numbers
- USCC when not required on that page (default stripped; keep with `--keep-categories uscc` when the form needs it, or `--preserve` for one exact string)
- third-party natural persons unrelated to the dispute (mark `role=third_party` in entities)

If a court form **requires** an ID number, do not use this tool to invent blanks—use the real filing package under firm procedure.

### Structural category overrides

| Flag | Effect |
|---|---|
| `--keep-categories CAT[,CAT…]` | Do not auto-redact these structural types; residual scan also ignores them |
| `--extra-categories CAT[,CAT…]` | Auto-redact beyond mode default (e.g. strip `case_number` in production) |
| `--preserve TEXT` | Leave one exact string unchanged (any category) |

Known structural categories: `id_card`, `mobile`, `landline`, `email`, `bank_account`, `case_number`, `uscc`.

## Process split

1. **Agent**: classify entities, assign roles, propose aliases.
2. **CLI**: apply longest-first replacements; write same-format file; residual-scan structural patterns.
3. **Human**: approve before send.

## What residual scan proves

It proves that configured **structural** patterns for the mode are gone from extractable text.

It does **not** prove:

- every nickname or abbreviation was caught;
- metadata outside supported fields is clean;
- a determined adversary with external knowledge cannot re-identify.

## File formats

| Input | Output | Engine notes |
|---|---|---|
| `.docx` | `.docx` | Paragraph + table + header/footer text; run styling simplified on rewrite |
| `.pdf` | `.pdf` | Text-layer only; search + redaction annots via PyMuPDF |
| `.txt` / `.md` | same | UTF-8 rewrite |

## Ledger handling

The ledger contains the deanonymization key. Treat it like credentials:

- store only on controlled local/NAS paths;
- never commit to git;
- never paste into online AI;
- destroy or encrypt when the task ends if policy requires.
