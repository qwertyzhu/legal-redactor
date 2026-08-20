# Scanned PDF workflow (OCR + visual black boxes)

Chinese litigation files are often phone/Quark scans with **no text layer**.  
`legal-redactor` supports two local-only paths. Neither uploads the file.

## Choose by destination

| Destination | Command path | Output |
|---|---|---|
| Online AI / internal notes | `ocr` → `redact` on `ocr.normalized.md` | Markdown / DOCX text |
| Court / opponent **visual** copy | `redact-scan` | Same-looking PDF with black boxes on structural PII |

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

### Hard limits (read before court filing)

1. Best-effort OCR coordinates. Skew, stamps, tables, and low DPI cause **misses**.  
2. Human **page-flip review is mandatory**.  
3. Nested digit false-positives are mitigated (bank before mobile) but not impossible.  
4. Broken OCR emails (`name@ domain . com`) may need a second pass or manual box.  
5. This is **not** a full e-discovery redaction suite.

## Environment

| Variable | Purpose |
|---|---|
| `TESSERACT_CMD` | Path to `tesseract.exe` |
| `TESSDATA_PREFIX` or `LEGAL_REDACTOR_TESSDATA` | Directory with `chi_sim.traineddata` |

## Security

- Do not commit `ocr.md`, ledgers, or `bbox_hits.json` from real matters.  
- Court deliverable = redacted PDF only (plus whatever the firm filing package requires).  
- Original scan stays in the matter folder under firm policy.
