# CLI 速查

```bash
legal-redactor --version
legal-redactor redact contract.docx --mode ai --auto-confident -o out.docx
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx
legal-redactor redact contract.docx --mode ai --allowlist allow.txt -o out.docx
legal-redactor verify out.docx --mode ai --strict   # 交付前用这条：0=PASS, 1=WARN, 2=FAIL
legal-redactor verify out.docx --mode ai            # 不加 --strict 时 WARN 也是 0
legal-redactor redact contract.docx --profile publish -o out.docx          # 对外改写，不是上网
legal-redactor redact judgment.docx --profile judgment-online -o out.docx  # 上网；第八条人员 role=article8
legal-redactor redact contract.docx --profile minimal -o out.docx
legal-redactor profiles
legal-redactor redact contract.docx --mode ai --ledger-encrypt -o out.docx   # 需 LEGAL_REDACTOR_LEDGER_PASS
legal-redactor restore ai_reply.md --ledger ~/.legal-redactor/ledgers/<hash>/ -o restored.md
legal-redactor redact contract.docx --mode ai --json -o out.docx
legal-redactor redact contract.docx --mode production --preserve-hyperlinks --preserve-bookmarks -o out.docx
legal-redactor redact ./案件目录/ --mode ai --auto-confident -o ./案件目录/out
# 目录里有 entities.json 才统一替身；否则各文件单独脱敏。同一案还没写表时加 --unify。
# 账本在 ./案件目录/.redactor/ ，不要和 out/ 一起外发。整段网址要去掉时加 --extra-categories url。
legal-redactor draft-entities contract.docx -o entities.draft.json
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode ai --auto-confident -o workdir/ai.md   # 扫描件走这条；无文字层 PDF 直接 redact 会被拒绝
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted.pdf   # 只涂证件号和电话，不能代替上面这条
legal-redactor redact draft.docx --mode ai --revisions keep-and-redact -o out.docx   # accept(默认)|reject|keep-and-redact
legal-redactor redact contract.pdf --mode ai --ocr-images on -o out.pdf   # 内嵌图片 OCR，需 Tesseract
legal-redactor seal-draft scan.pdf -o seals.json    # 公章区域草稿，需 [vision]，只出草稿
legal-redactor redact judgment.docx --mode ai --redact-court-staff -o out.docx   # 法院人员姓名默认保留，此开关改为替换
legal-redactor doctor                               # Tesseract / OpenCV / NER 可用性
python3 scripts/self_test.py
```

`--keep-categories` / `--extra-categories`：`id_card,mobile,landline,email,bank_account,uscc,case_number,passport,hk_mo_permit,plate,property_cert,wechat_qq,ip,mac,url,url_secret,share_code,birth_date`。`url` 默认不替换，整段去掉才加 `--extra-categories url`。

批量：有 `entities.json` 的目录会统一替身，没有则各文件单独处理。目录 `verify` 只校验脱敏稿，不要把 `.redactor/` 混进去。

可选 NER：`pip install 'legal-redactor[ner]'`（LTP）。结果只进 `*.suspects.json`（reason=ner），需人工确认后写进 entities 才会替换。
