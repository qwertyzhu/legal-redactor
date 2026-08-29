---
name: legal-document-redactor
description: 中国法律文书双模式脱敏：ai（给在线 AI 前激进去标识）与 production（交法院/对方前只去证件号、手机、邮箱等），原格式返回（DOCX→DOCX、PDF→PDF、文本→文本），并生成本地 ledger 与残留扫描。凡用户提到 脱敏、去标识、匿名化、redact、anonymize、strip PII、交给网上AI前处理、出证前遮盖，或需要一份可外传的法律文件副本时使用。
---

# 法律文书脱敏

为本地法律文书生成**同格式**脱敏副本，附带可审计的替换 ledger，以及结构性个人信息残留扫描。

本 skill 驱动 `legal-redactor` Python 包。判断（谁是当事人姓名 / 作品名）留给 Agent；替换与校验由 CLI 确定性完成。

## 先选模式

| 模式 | 使用场景 | 保留 | 去掉 |
|---|---|---|---|
| `ai` | 上传在线 AI、公开样例、不得识别案件的内部笔记 | 仅法律结构 | 姓名、单位、案号、证件、手机、邮箱、账号、信用代码、地址、作品名、精确金额（经 entities） |
| `production` | 交法院或对方当事人 | 当事人身份、案号、有效商业条款 | 证件号、手机、邮箱、银行账号、信用代码，以及你标记为 `third_party` 的实体 |

需要时覆盖结构性默认：

- `--keep-categories uscc` — 起诉材料需要保留统一社会信用代码时
- `--keep-categories uscc,bank_account` — 逗号分隔或重复传入
- `--extra-categories case_number` — 即使 `production` 也去掉案号

**禁止**把 `ai` 模式产物当作起诉材料。  
**禁止**把 ledger（原文→替身映射）贴进在线模型对话。

## 不可妥协的边界

1. 只处理**本地副本**。不要覆盖唯一原件。
2. 输出后缀**必须**与输入一致（`.docx`→`.docx`，`.pdf`→`.pdf`）。
3. PDF 支持：
   - **文字层 PDF**：直接 `redact`。
   - **扫描件 / 纯图片 PDF**：`ocr` 后再对 markdown 脱敏，供 AI/文本使用；交法院视觉涂黑用 `redact-scan`。见 [references/scanned-pdf.md](references/scanned-pdf.md)。
4. 文本产物交付前，残留结构性扫描必须 **PASS**（除非用户明确接受残留风险）。视觉 `redact-scan` 必须**人工翻页**（OCR 框会漏）。
5. 仓库样例和测试均为**虚构**。不要提交真实客户 ledger 或 OCR 导出。
6. 必须人工复核。扫描通过不等于自然语言标识已全部清除。

## 工作流

### 1. 确认模式与路径

不清楚就问：

- 去向：在线 AI，还是法院/对方；
- 输入路径；
- 当事人姓名是保留（`production`）还是去掉（`ai`）。

### 2. 抽取并列出实体（Agent 判断）

阅读文书（或本地抽文本）。编写 `entities.json`：

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

类别：`person` | `organization` | `address` | `work_title` | `amount` | `other`  
角色：`party` | `third_party` | `counsel` | `other`

`production` 模式下，`person` / `organization` / `address` 且 `role=party`、**没有** `replacement` 的行会**保留**。

结构性项（证件 / 手机 / 邮箱 / 银行账号 / 信用代码 / 案号）自动检测；除非要自定义替身，否则不必列出。

起步模板：[references/entities.template.json](references/entities.template.json)。  
见 [references/methodology.md](references/methodology.md) 与 [schemas/entities.schema.json](schemas/entities.schema.json)。

可选：本地起草结构性行 + 自然语言疑似提示：

```bash
legal-redactor draft-entities INPUT.docx -o entities.draft.json
# 只要结构性字段：加 --no-suspects
```

疑似行使用 `source=suspect-hint` 且**没有替身**。写入 AI 模式前先确认 `role` / `replacement`。未经你确认，工具不会把当事人姓名写成最终替身。

然后由你补全或确认自然语言姓名。

### 3. 跑确定性脱敏

在已安装软件包的任意目录：

```bash
legal-redactor redact INPUT.docx --mode ai --entities entities.json -o OUTPUT.docx
legal-redactor redact INPUT.pdf --mode production --entities entities.json -o OUTPUT.pdf
# 起诉材料需要保留信用代码：
legal-redactor redact INPUT.docx --mode production --entities entities.json --keep-categories uscc -o OUTPUT.docx
```

或走仓库包装脚本：

```bash
python skills/legal-document-redactor/scripts/redact_cli.py redact INPUT.docx --mode ai --entities entities.json -o OUTPUT.docx
```

产物写在输出文件旁边（或 `--work-dir`）：

- `*.ledger.json` — 完整映射（**仅本地**）
- `*.residual.json` — 结构性残留扫描
- `*.suspects.json` — 自然语言实体提示（**不会自动替换**）
- `*.summary.md` — 人工对照表

### 4. 校验

```bash
legal-redactor verify OUTPUT.docx --mode ai
```

交付清单：

- [ ] 模式与去向一致
- [ ] 文件格式与输入相同
- [ ] 残留扫描 PASS
- [ ] 抽查当事人姓名（按预期保留或去掉）
- [ ] ledger 未上传任何地方
- [ ] 已告知用户：结构性通过 ≠ 自然语言已完美匿名

### 5. 向用户汇报

返回：

1. 输出路径  
2. 模式  
3. 替换条数  
4. 残留状态  
5. 你无法有把握归类的内容（问，不要猜）

## CLI 速查

```bash
# 只检测
legal-redactor scan contract.docx --mode ai --entities entities.json

# 脱敏
legal-redactor redact contract.docx --mode ai --entities entities.json -o contract.redacted-ai.docx

# 保留某段原文
legal-redactor redact contract.docx --mode production --preserve "北京互联网法院" -o out.docx

# production 起诉材料保留信用代码
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx

# 残留校验必须使用与 redact 相同的 keep/extra 参数
legal-redactor verify out.docx --mode production --keep-categories uscc

# 批量目录（推荐两阶段）
legal-redactor redact ./inbox/ --mode ai --unify -o ./outbox/

# 先手动 unify 再脱敏
legal-redactor unify ./inbox/ -o ./unified/ --mode ai
legal-redactor redact ./inbox/ --mode ai --entities ./unified/entities.consistent.json -o ./outbox/

# 目录扫描 / 校验
legal-redactor scan ./inbox/ --mode ai
legal-redactor verify ./outbox/ --mode ai

# 起草实体骨架
legal-redactor draft-entities contract.docx -o entities.draft.json

# 扫描件 PDF → 文本（给 AI / 笔记）
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode ai --entities entities.json -o workdir/ai.md

# 扫描件 PDF → 视觉涂黑（交法院）
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted-production.pdf
```

## 限制（v0.8）

- PDF：文字层走 `redact`；扫描件走 `ocr` / `redact-scan`（本地 Tesseract + chi_sim）
- `redact-scan` 是 OCR 框尽力而为 — **交法院前必须人工翻页**
- DOCX：单 run 内格式尽量保留；跨 run 实体会折叠段落
- 自然语言姓名需要已确认的 entities JSON；suspects 只是复核提示（现含地址）
- 多文件事项：优先 `redact DIR --unify`，保证替身稳定
- 目录 `verify` 会检查目录内所有支持后缀 — 不要把 ledger 混进校验目录
