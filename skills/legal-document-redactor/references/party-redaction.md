# 整方脱敏（甲方 / 乙方 / 双方）

用户要求遮挡整方信息时，先让用户选边：`a` / `b` / `both`。

整方至少包括：主体全称与简称、关联公司、法定代表人/联系人、地址、邮编、电话、邮箱、证件/信用代码、银行账户、签名、签署日期，以及**整枚公章**。

```bash
legal-redactor redact-scan scan.pdf --mode production --redact-party a \
  --party-spec party-spec.json -o scan.party-a-redacted.pdf
```

模板：[party-redaction.template.json](party-redaction.template.json)。公章必须用 `regions` 覆盖整枚印章，不能只靠 OCR。未选方默认全部保留。逐页人工确认。
