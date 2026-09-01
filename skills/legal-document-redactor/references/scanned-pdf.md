# Scanned PDF workflow (OCR + visual black boxes)

Chinese litigation files are often phone/Quark scans with **no text layer**.  
`legal-redactor` supports two local-only paths. Neither uploads the file.

## Choose by destination

| Destination | Command path | Output |
|---|---|---|
| Online AI / internal notes | `ocr` → `redact` on `ocr.normalized.md` | Markdown / DOCX text |
| Court / opponent **visual** copy | `redact-scan` | Same-looking PDF with black boxes on structural PII |
| Hide one whole contracting party | `redact-scan --redact-party ... --party-spec ...` | PDF with only the selected party's identifiers, signatures and seals covered |

## A. OCR then text redaction

```bash
# requires local Tesseract with chi_sim
set TESSDATA_PREFIX=C:\path\to\tessdata

legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode production --entities entities.json -o workdir/out.md
```

`ocr` writes:

- `ocr.md` — raw page text  
- `ocr.normalized.md` — CJK inter-character spaces collapsed (needed for entity replace)  
- `ocr_meta.json` — page stats only  

Then use the normal dual-mode pipeline on the markdown.

## B. Visual black boxes on the scan (`redact-scan`)

```bash
legal-redactor redact-scan scan.pdf -o scan.redacted-production.pdf --mode production
```

Behavior:

- Renders each page → Tesseract word boxes → matches structural patterns  
- Categories follow mode / `--keep-categories` / `--extra-categories`  
- Default `production`: black **id / mobile / landline / email / bank / uscc**; **does not** black party names  
- Writes `*.bbox_hits.json` (**local only** — contains matched strings)

## C. Whole-party visual redaction

Do not use plain `production` when the user asks to hide all information of Party A or Party B. First obtain the user's scope choice:

- `a` = Party A only
- `b` = Party B only
- `both` = both parties

Then build a reviewed local spec from [party-redaction.template.json](party-redaction.template.json) and run:

```bash
legal-redactor redact-scan scan.pdf --mode production \
  --redact-party a --party-spec party-spec.json \
  -o scan.party-a-redacted.pdf
```

Default semantics with `--redact-party`:

- Redact only the selected party's confirmed `identifiers` and reviewed `regions`.
- Preserve every field of an unselected party, including its phone, email and account.
- `--also-redact-structural-all` restores the legacy behavior of additionally covering structural PII for every party.
- A seal must be covered by one region large enough for the full outer ring, entity name, seal number and center mark. OCR text boxes alone are insufficient.
- Signature blocks and handwriting should also use reviewed regions.

### Hard limits (read before court filing)

1. Best-effort OCR coordinates. Skew, stamps, tables, and low DPI cause **misses**.  
2. Human **page-flip review is mandatory**.  
3. Nested digit false-positives are mitigated (bank before mobile) but not impossible.  
4. Broken OCR emails (`name@ domain . com`) may need a second pass or manual box.  
5. This is **not** a full e-discovery redaction suite.
6. Whole-party mode is only as complete as the reviewed identifiers and regions. Missing a seal region is a failed review, even if structural residual scanning passes.

## Environment

| Variable | Purpose |
|---|---|
| `TESSERACT_CMD` | Path to `tesseract.exe` |
| `TESSDATA_PREFIX` or `LEGAL_REDACTOR_TESSDATA` | Directory with `chi_sim.traineddata` |

## Security

- Do not commit `ocr.md`, ledgers, or `bbox_hits.json` from real matters.  
- Court deliverable = redacted PDF only (plus whatever the firm filing package requires).  
- Original scan stays in the matter folder under firm policy.
