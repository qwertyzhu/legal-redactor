# CLI 速查

```bash
legal-redactor --version
legal-redactor redact contract.docx --mode ai --auto-confident -o out.docx
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx
legal-redactor redact contract.docx --mode ai --allowlist allow.txt -o out.docx
legal-redactor verify out.docx --mode ai            # 0=PASS/WARN, 2=FAIL
legal-redactor verify out.docx --mode ai --strict   # WARN → 1
legal-redactor redact contract.docx --profile publish -o out.docx
legal-redactor redact contract.docx --profile minimal -o out.docx
legal-redactor profiles
legal-redactor redact contract.docx --mode ai --ledger-encrypt -o out.docx   # 需 LEGAL_REDACTOR_LEDGER_PASS
legal-redactor restore ai_reply.md --ledger ~/.legal-redactor/ledgers/<hash>/ -o restored.md
legal-redactor redact contract.docx --mode ai --json -o out.docx
legal-redactor redact contract.docx --mode production --preserve-hyperlinks --preserve-bookmarks -o out.docx
legal-redactor redact ./inbox/ --mode ai --unify -o ./outbox/
legal-redactor draft-entities contract.docx -o entities.draft.json
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted.pdf
python3 scripts/self_test.py
```

`--keep-categories` / `--extra-categories`：`id_card,mobile,landline,email,bank_account,uscc,case_number,passport,hk_mo_permit,plate,property_cert,wechat_qq,ip,birth_date`。

批量优先 `redact DIR --unify`。目录 `verify` 不要把 ledger 混进校验目录。
