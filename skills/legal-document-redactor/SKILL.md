---
name: legal-document-redactor
description: "Local-first Chinese legal document redaction (脱敏/去标识/匿名化/隐名). Use when the user asks to redact, anonymize, de-identify, strip PII, 脱敏, 去标识, 匿名化, 隐名, seal/party cover-up, or prepare filings/AI uploads. Do NOT use for general contract drafting, legal research, citation lookup, or non-Chinese PII pipelines."
---

# 法律文书脱敏

本地 CLI 做**确定性替换**；Agent 只补自然语言实体。不要先通读全文手写实体表。

## 先选模式

| 模式 | 去向 | 行为 |
|---|---|---|
| `ai` | 在线模型 / 可外传笔记 | 激进：姓名单位案号证件联系方式等 |
| `production` | 法院 / 对方 | 保留当事人与案号；去掉证件/手机/邮箱/账号/信用代码/车牌/不动产权证等 |

**禁止**把 `ai` 产物当起诉材料。**禁止**把 ledger（原文→替身）贴进在线对话。

## 一条命令

```bash
legal-redactor redact INPUT.docx --mode ai --auto-confident -o OUTPUT.docx
```

出证把 `ai` 换成 `production`。只有这条没接住的名字，再补 `entities.json`。

可选：

- `--profile publish` — 公开传播 / 裁判文书上网式（张某、住址到区县、金额模糊）；`--profile minimal` 只去证件手机邮箱账号；`legal-redactor profiles` 列出全部
- `--allowlist FILE` / 项目内 `.redactor/allow.txt` — 必须原样保留（法院、法条、当事人自定义）
- `--keep-categories` / `--extra-categories` — 结构性类别微调
- `--preserve "原文"` — 单段保留
- 整方遮挡扫描件：见 [references/party-redaction.md](references/party-redaction.md)
- 扫描件 OCR / 涂黑：见 [references/scanned-pdf.md](references/scanned-pdf.md)
- 本机 Ollama 识别：见 [references/ollama.md](references/ollama.md)
- CLI 速查：见 [references/cli-cheatsheet.md](references/cli-cheatsheet.md)

## 校验与交付

```bash
legal-redactor verify OUTPUT.docx --mode ai && echo PASS || echo FAIL
```

`verify` 的 mode/profile、keep/extra 必须与 `redact` 一致。退出码：0=PASS（WARN 也为 0，加 `--strict` 时 WARN=1）、2=FAIL。WARN 时打开 `*.audit.md` 看低置信项和残留风险，逐条问用户。

在线 AI 回复里带着替身时，用本地 ledger 还原（ledger 永不外传）：

```bash
legal-redactor restore AI_REPLY.md --ledger LEDGER_PATH -o AI_REPLY.restored.md
```

`LEDGER_PATH` 见 `redact` 输出的 `ledger:` 行或输出旁的 `*.ledger-path.txt`。

交付清单：

- [ ] 模式与去向一致；格式与输入相同
- [ ] 残留扫描 PASS（低置信命中在 suspects，需人工看）
- [ ] 抽查当事人姓名（按预期保留或去掉）；法条/法院未被误脱
- [ ] ledger 未上传任何地方
- [ ] 反推三问：无残留可识别信息？上下文组合反推不出当事人？未过度脱敏？

旁路产物：`*.audit.md` / `*.audit.json`、`*.residual.json`、`*.suspects.json`、`*.summary.md` 在输出旁；ledger 默认在 `~/.legal-redactor/ledgers/<hash>/`（0600），输出旁只留 `*.ledger-path.txt` 指针。高敏事项加 `--ledger-encrypt`（口令放 `LEGAL_REDACTOR_LEDGER_PASS`）。

## 不可妥协

1. 只处理**本地副本**，不覆盖唯一原件。
2. 输出后缀与输入一致。
3. 扫描件视觉涂黑必须人工翻页。
4. 结构性 PASS ≠ 自然语言已完美匿名。
5. 规范基线见 [references/redaction-standards.md](references/redaction-standards.md)。

实体模板：[references/entities.template.json](references/entities.template.json)、[references/entities.court-style.template.json](references/entities.court-style.template.json)。方法论：[references/methodology.md](references/methodology.md)。
