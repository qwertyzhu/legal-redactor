---
name: legal-document-redactor
version: 0.10.8
description: 中国法律文书本地脱敏，工具全程在本机运行，默认一条命令扫完，不要先通读全文或手写实体表。ai 用于交给网上模型，production 保留当事人；可整方遮甲方、乙方或双方（名称、签名、整枚公章）；支持只脱敏指定类别或点名实体；实体识别可改用本机 Ollama 模型，避免原文交给在线模型。凡用户提到脱敏、去标识、匿名化、整方遮挡、公章遮挡、用本机模型、不出设备、交给网上 AI 前处理或出证前遮盖时使用。
---

# 法律文书脱敏

**0.10 起先跑这一条，不要先通读全文：**

```bash
legal-redactor redact INPUT.docx --mode ai --auto-confident -o OUTPUT.docx
```

交法院或对方时把 `ai` 换成 `production`。只有这条命令没接住的名字，才补 `entities.json`。

环境自检（本地增补，跑任何命令前花两秒确认）：

```bash
legal-redactor --version                      # 应在 PATH（本机软链 ~/.local/bin/legal-redactor → legal-ops venv）
tesseract --list-langs | grep chi_sim         # 仅扫描件 ocr / redact-scan 前必查
ollama list                                   # 仅用本机 Ollama 识别时必查（见下节）
python3 scripts/self_test.py                  # 新设备安装后/升级后跑一次：9 项全 PASS 才算就绪
```

## 实体识别：在线 Agent 还是本机 Ollama（本地增补）

工具全程在本机运行；替换层永远由 CLI 确定性执行。可选的只是**自然语言实体交给谁识别**：

| 识别者 | 做法 | 适用 |
|---|---|---|
| 在线 Agent（默认） | Agent（Claude 等）读 suspects 补 entities | 文书允许给在线模型看，或实体很少 |
| 本机 Ollama | 本机 Ollama 模型读原文找实体，原文字句不外发 | 不想把原文交给在线模型；或无网环境 |

**何时询问识别方式**（不要每次运行都问）：默认用在线 Agent；仅当用户说「用本机模型/Ollama/不出设备/不要联网」、或文书明显高敏（刑事案件、涉未成年人、未公开并购）时，才主动问一次。

用户选本机 Ollama 后，先跑检测脚本（只读），按结果分流：

```bash
python3 scripts/ollama_setup.py          # 人类可读；--json 供 Agent 判断
```

- **(a) 已装 Ollama** → 脚本列出已安装模型并给出「实体识别首选」，直接用 `--model <首选>` 跑识别；首次使用主动向用户念一遍检测结论即可，之后同设备不必再跑。
- **(b) 未装 Ollama** → 脚本按设备内存给出安装命令和推荐模型（≥32GB→12-14B 级；16-31GB→7-8B 级如 qwen3.5；<16GB→4B 级勉强可用，更建议用在线 Agent 识别）。把命令念给用户，**征得同意后**再执行安装/拉取；拉取完重跑一次本脚本确认就绪。

本机 Ollama 识别与替换（Ollama 只负责「找」，替换仍走 `legal-redactor` 确定性执行）：

```bash
# 1. 本地模型识别 → 草稿（source=ollama-draft）
python3 scripts/ollama_entities.py INPUT.docx -o entities.ollama.draft.json
# 2. 人工/Agent 确认草稿：核对角色、补 replacement 替身（草稿不准时直接改）
# 3. 确认后正常脱敏（不要加 --auto-confident，避免重复替换）
legal-redactor redact INPUT.docx --mode ai --entities entities.ollama.draft.json -o OUTPUT.docx
```

- 默认模型 `qwen3.5:latest`，`--model` 可换；扫描件先 `legal-redactor ocr` 再对 md 跑识别脚本
- 草稿里的 `other` 类（出生日期、车牌号、权证号）正是 `--auto-confident` 的盲区，确认时重点看
- 本地模型会漏，**verify + 反推三问仍是强制项**；用本机模型 ≠ 免人工

本机 Ollama 的运行规则（按输入类型分流）：

| 输入 | 链路 | 需要的本地模型 |
|---|---|---|
| DOCX / 文字层 PDF / txt / md | 直接抽文字 → 文本模型识别 → CLI 替换 | **只需文本模型**（qwen3.5 等） |
| 扫描件 → 给 AI / 出文字版 | Tesseract `ocr` → md → 文本模型识别 → CLI 替换 | Tesseract（非模型）+ 文本模型 |
| 扫描件 → 交法院涂黑 | `redact-scan`（Tesseract 词坐标涂黑） | Tesseract；**视觉模型不参与** |

- **实体识别永远用文本模型**，不需要视觉模型。OCR 层（Tesseract）的任务是把图像变成「文字 + 坐标」，涂黑依赖坐标，视觉模型给的坐标不可靠，不能替代。
- 可选增强：扫描质量差、Tesseract 错字多时，可用本机视觉模型（如 `glm-ocr`）先把页面图像转成文字稿，人工校对后再走文本模型识别——视觉模型只出文字，不进坐标链路；这是可选项，不是默认流程。
- 公章 / 签名区域识别目前不支持本地模型自动圈定，仍按 party-spec 人工标 regions。
- 网络边界：确认 Ollama 仅监听本机（`lsof -nP -iTCP:11434 -sTCP:LISTEN` 应显示 `127.0.0.1`），未设 `OLLAMA_HOST=0.0.0.0`；ledger、草稿、OCR 中间产物全部留在本地。

为本地法律文书生成**同格式**脱敏副本，附带可审计的替换 ledger，以及结构性个人信息残留扫描。

本 skill 驱动 `legal-redactor` Python 包。结构性字段和高置信当事人由 CLI 一次扫完；只有 `--auto-confident` 没接住、或用户明确要求核对的名字，才由 Agent 补进 `entities.json`。

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

规范基线（本地增补）：去向是「公开传播 / 公众号案例」时按裁判文书上网标准（法释〔2016〕19号）做隐名，替身模板见 [references/entities.court-style.template.json](references/entities.court-style.template.json)；四套规范与工具参数的完整对照、以及工具识别盲区清单（出生日期、车牌号、不动产权证号、无锚点姓名——`--auto-confident` 全部接不住），见 [references/redaction-standards.md](references/redaction-standards.md)。

## 指哪打哪：用户指定只脱敏某部分（本地增补）

用户说"只脱敏 X"时，不要套默认模式，按下表选参数；拿不准就问用户。

| 用户要求 | 做法 |
|---|---|
| 只脱敏某一类结构性字段（如只去身份证号） | `--mode production` + `--keep-categories` 反向枚举其余类。例：只去证件号 → `--keep-categories mobile,email,bank_account,uscc,case_number,landline` |
| 只替换用户点名的名称 / 人名，其他一概不动 | entities.json 只列这些条目（可带 `replacement`），**不加 `--auto-confident`**，且用 `--keep-categories id_card,mobile,landline,email,bank_account,uscc,case_number` 把结构性字段也全部放行 |
| 某段原文 / 某名称必须原样保留 | `--preserve "原文"`，可重复传入 |
| 只遮甲方 / 乙方（含名称、签名、整枚公章） | 扫描件 `redact-scan --redact-party a\|b\|both --party-spec party-spec.json`（见下节，必须先让用户选边） |
| 额外去掉 production 默认保留的案号 | `--extra-categories case_number` |
| 金额不脱敏 | ai 模式下金额只在 entities 列出时才处理，不列即可 |

`--keep-categories` 可选值：`id_card, mobile, landline, email, bank_account, uscc, case_number`（逗号分隔或重复传入）。

注意：`keep/extra-categories` 只管结构性字段。姓名、单位、地址、作品名等自然语言实体只能用 entities.json 控制——"只去结构性号码、一个名字都不动"的正确做法是 `production --keep-categories ...` 且不加 `--auto-confident`。

## 整方脱敏：必须让用户选边

用户要求“遮挡甲方/乙方全部信息”“连名称和公章一起遮”时，不得直接套用默认 `production`。如果上下文尚未明确，先让用户选择：

- `a`：只遮甲方；
- `b`：只遮乙方；
- `both`：甲乙双方都遮。

“整方信息”至少包括：主体全称与简称、关联公司、法定代表人/联系人、地址、邮编、电话、邮箱、证件/信用代码、银行账户、签名、签署日期，以及**整枚公章（含外圈、名称、编号和中心图案）**。未选中的一方默认全部保留，包括其联系方式和账号。

扫描件整方脱敏需要本地 `party-spec.json`：

- `identifiers` 列出已确认的名称、别名、联系人和结构性字段；
- `regions` 使用页面归一化坐标 `[x0,y0,x1,y1]` 标记公章、签名和整块签署栏；
- 公章不能只靠 OCR 文字框，必须用覆盖整枚印章的 `regions`；
- 模板见 [references/party-redaction.template.json](references/party-redaction.template.json)。

## 不可妥协的边界

1. 只处理**本地副本**。不要覆盖唯一原件。
2. 输出后缀**必须**与输入一致（`.docx`→`.docx`，`.pdf`→`.pdf`）。
3. PDF 支持：
   - **文字层 PDF**：直接 `redact`。
   - **扫描件 / 纯图片 PDF**：`ocr` 后再对 markdown 脱敏，供 AI/文本使用；交法院视觉涂黑用 `redact-scan`。见 [references/scanned-pdf.md](references/scanned-pdf.md)。
4. 文本产物交付前，残留结构性扫描必须 **PASS**（除非用户明确接受残留风险）。视觉 `redact-scan` 必须**人工翻页**（OCR 框会漏）。
5. 仓库样例和测试均为**虚构**。不要提交真实客户 ledger 或 OCR 导出。
6. 必须人工复核。扫描通过不等于自然语言标识已全部清除；整方脱敏必须逐页确认所选方名称和每枚公章均不可见、未选方未被误遮。

## 工作流

### 1. 确认模式与路径

不清楚就问：

- 去向：在线 AI，还是法院/对方；
- 输入路径；
- 当事人姓名是保留（`production`）还是去掉（`ai`）。
- 是否需要整方脱敏；若需要，选择甲方、乙方或双方。

### 2. 先跑一条命令，不要先通读全文

默认不要把整篇文书读进对话、再手写 `entities.json`。那会多好几轮，而且容易漏改、替身不稳定。

```bash
legal-redactor redact INPUT.docx --mode ai --auto-confident -o OUTPUT.docx
legal-redactor redact INPUT.docx --mode production --auto-confident -o OUTPUT.docx
```

`--auto-confident` 在同一次进程里完成：

- 证件、手机（含 `138 0000 2222`）、邮箱、账号（含「账号：」「收款账户：」引导）、信用代码，以及 `ai` 模式下的案号
- `ai`：高置信姓名、单位、地址、作品名；精确金额改成量级。「以下简称」的简称和全称共用同一个替身
- `production`：当事人、案号、地址、作品名保留；第三人姓名去掉；证件和联系方式仍去掉

然后只看旁边的 `*.suspects.json`。里面剩下的是**没有**自动替换的提示。空的就不要再开一轮补实体。非空时只问用户那些条目。

仍要手写 `entities.json` 的情况：用户给了自定义替身，或要保留某个本应去掉的名称。

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

可选，且不是默认路径：

```bash
legal-redactor draft-entities INPUT.docx -o entities.draft.json
# 只要结构性字段：加 --no-suspects
```

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

机器化验收（本地增补）：用退出码判断，FAIL 就不要交付、回到脱敏步骤修正；`verify` 的 keep/extra 参数必须与 `redact` 完全一致：

```bash
legal-redactor verify OUTPUT.docx --mode ai && echo PASS || echo FAIL
```

交付清单：

- [ ] 模式与去向一致
- [ ] 文件格式与输入相同
- [ ] 残留扫描 PASS
- [ ] 抽查当事人姓名（按预期保留或去掉）
- [ ] ledger 未上传任何地方
- [ ] 已告知用户：结构性通过 ≠ 自然语言已完美匿名
- [ ] 反推三问（本地增补，上海律协 AI 指引 2.3.4）：无残留可识别信息？上下文组合（生日+性别+区县、作品名+案由）反推不出当事人？未过度脱敏导致事实缺失？

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

# 脱敏（推荐：不必先写实体表）
legal-redactor redact contract.docx --mode ai --auto-confident -o contract.redacted-ai.docx

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

# 扫描件 PDF → 只遮甲方全部信息（名称、公章等来自已确认的 party spec）
legal-redactor redact-scan scan.pdf --mode production --redact-party a \
  --party-spec party-spec.json -o scan.party-a-redacted.pdf

# 只遮乙方：--redact-party b；双方都遮：--redact-party both
# 如还要额外遮掉所有各方的结构性号码，再加 --also-redact-structural-all
```

## 提速工作流（本地增补）

瓶颈排序：Tesseract OCR ≫ PDF/DOCX 解析 > 替换本身。提速全部围绕"少跑 OCR、少跑重复轮次"：

1. **文字层 PDF / DOCX 直接 `redact`，绝不 `ocr`**——先快速判断有没有文字层（能选中文字即有）。
2. **扫描件给 AI 用：`ocr` 只跑一次**，拿到 `ocr.normalized.md` 后，所有实体表调整、"指哪打哪"迭代都在 md 上 `redact`（秒级）。禁止每改一次实体表就重跑 OCR。
3. **扫描件可 `--dpi 200` 提速**（默认 300）；发现公章、小字漏识别时回到 300。
4. **多文件一次跑**：`redact DIR --unify -o OUTDIR`，不要逐个文件循环调用。
5. 批量后只 `verify OUTDIR` 一次，用退出码验收，不通读全文。

## 限制

- PDF：文字层走 `redact`；扫描件走 `ocr` / `redact-scan`（本地 Tesseract + chi_sim）
- `redact-scan` 是 OCR 框尽力而为 — **交法院前必须人工翻页**
- 整方脱敏必须使用 `--redact-party` + `--party-spec`；名称靠确认后的 identifiers，公章/签名靠 reviewed regions
- DOCX：单 run 内格式尽量保留；跨 run 实体会折叠段落
- 自然语言姓名：优先 `--auto-confident`。没接住的才看 suspects，不要为了已经替换的名字再通读全文
- 多文件事项：优先 `redact DIR --unify`，保证替身稳定
- 目录 `verify` 会检查目录内所有支持后缀 — 不要把 ledger 混进校验目录
