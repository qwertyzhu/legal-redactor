# Changelog

## Unreleased

## 0.12.0 - 2026-10-14

P2：可选 NER、图片 OCR、公章草稿、docx 修订模式、Agent 入口。叠加在 0.11.0（P1）之上。

- **规则修复**：`审判长/审判员/人民陪审员/书记员/法官助理/执行员` 后的人名进 suspects（role=court_staff，ai 模式按带姓氏人名替换，production 不动）；`受X委托 / 接受X的委托` 识别委托人；组织名去掉「本所受」等前缀。语料中原 known_misses（赵测审、孙测五）转为 must_remove。
- **可选 NER**（`pip install 'legal-redactor[ner]'`，选 LTP：比 HanLP 小）：抓无角色词锚点的人名（如「小明」）。**只进 suspects，永不自动替换**；未安装时静默返回空。`LEGAL_REDACTOR_NER=ltp|hanlp|off`。
- **内嵌图片 OCR**（需 Tesseract + chi_sim）：文字层 PDF 中的图片复用扫描件路径 OCR，结构性号码与已确认实体处像素涂黑；docx `word/media/*` OCR 后逐图告警 `IMAGE-PII`（图片不改，提示替换）。`--ocr-images auto|on|off`，缺 Tesseract 时 auto 只告警。
- **公章草稿**（`pip install 'legal-redactor[vision]'`）：`legal-redactor seal-draft IN.pdf` 用 OpenCV Hough 找红色圆章，输出 party-spec 格式区域草稿，不做任何遮挡。
- **docx 修订**：`--revisions accept|reject|keep-and-redact`，默认 accept（原行为）；keep-and-redact 保留 w:ins/w:del 并脱敏其中文字（含 w:delText），作者照旧匿名。目录 verify 同时扫描修订内文字。
- **`legal-redactor doctor`**：查看 Tesseract / OpenCV / NER 是否可用。
- **AGENTS.md** 指向 SKILL.md；Codex plugin 版本对齐 0.12.0。

## 0.11.0 - 2026-10-14

P1：ledger 加固与还原、脱敏等级配置、审计报告、回归语料、单一入口。叠加在 0.10.9（P0）之上。

- **ledger 默认移出输出目录**：写到 `~/.legal-redactor/ledgers/<hash>/ledger.json`（目录 0700、文件 0600；`LEGAL_REDACTOR_LEDGER_ROOT` 可改根目录），输出旁只留 `*.ledger-path.txt` 指针。`--ledger PATH` 指定位置，`--ledger-beside-output` 恢复旧行为。显式传 `--work-dir`（API 的 `work_dir=`）和批量目录模式仍写在旁边，以兼容 `unify`。
- **`--ledger-encrypt`**：可选安装 `pip install 'legal-redactor[crypto]'`；口令取 `LEGAL_REDACTOR_LEDGER_PASS` 或 `restore --ledger-password`。只写 `ledger.json.enc`，不留明文。
- **新命令 `restore`**：用本地 ledger 把在线 AI 回复里的替身还原为原文（txt/md/docx/pdf），替身→原文按最长优先，一个替身对应多个原文时告警。
- **`--profile`**：内置 `ai` / `production` / `publish`（裁判文书上网式：张某、住址到区县、金额模糊）/ `minimal`（只去证件、手机、邮箱、账号）。`legal-redactor profiles` 列出。`--mode` 照旧可用，等价于同名 profile 的类别。
- **审计报告**：每次 `redact` 生成 `*.audit.json` / `*.audit.md`：逐条替换的类别、置信度、规则、文档部件、段落号、前后 10 字上下文；残留风险统计（长数字串、书名号、日期、自然语言疑似、低置信结构性）。状态 PASS / WARN / FAIL。
- **verify 三级**：PASS 退出 0；WARN 默认退出 0、加 `--strict` 退出 1；FAIL 退出 2。`--json` 单文件也可用。目录 verify 自动跳过 ledger / audit 等旁路文件；修复 P0 引入的目录 verify 参数错误。
- **回归语料**：`tests/corpus/` 起诉状、判决书、合同、律师函、负例（法条、法院、订单号、校验失败代码）；CI 断言召回率 ≥ 0.95、保留率 = 1.0。`evals.json` 扩到 19 条。
- **单一入口**：`scripts/redact_cli.py` 在包未安装时给出安装命令并以退出码 3 结束；`redact` / `verify` / `restore` / `profiles` 支持 `--json`。
- **软开关**：`--preserve-hyperlinks`（docx 外链）、`--preserve-bookmarks`（PDF 书签，标题不脱敏）。

## 0.10.9 - 2026-10-14

P0 硬化：白名单与法条保护、扩充结构性类别、校验位置信度、规则修复、docx/PDF 元数据清理、Skill 精简。

- **白名单**：内置法院/法条种子表 `references/allowlist.default.txt`；支持 `--allowlist FILE` 与 `.redactor/allow.txt`。书名号匹配法条形态或后接「第X条」时不再当作品名脱敏。
- **新结构性类别**：passport、hk_mo_permit（需标签）、plate、property_cert、wechat_qq（需标签）、ip、birth_date（`…日出生` 或出生日期标签）。`production` 默认去掉车牌与不动产权证号（法释〔2016〕19号）。
- **校验位**：身份证 / 银行卡 Luhn / 统一社会信用代码 GB 32100。身份证校验失败仍替换但标 medium；信用代码无标签且校验失败、银行卡无前缀且 Luhn 失败 → 只进 suspects，不自动替换。
- **规则修复**：角色词后冒号可选（「法定代表人李测强」）；地址前缀补「住/现住/户籍地/经常居住地」；组织名去掉「开户行/甲乙方」前缀；案号占位符保留「民终/民初」等审级。
- **元数据**：docx 清理 app/custom、作者/rsid、mailto/外链 rels，并对 media/OLE 告警；PDF 删除 XMP、书签、表单、批注与附件。
- **Skill**：SKILL.md 压到短入口；Ollama / 整方 / 扫描件 / CLI 速查迁到 `references/`；版本对齐 0.10.9。

## 0.10.8 - 2026-10-02

仅 Skill 与文档；Python 包不变（仍为 0.10.7）。

- SKILL.md 描述与 README（中英文）补齐自 v0.10.3 起的新能力：选择性脱敏、本机 Ollama 识别、规范基线、self_test 自检；安装命令指向最新 wheel 并写明「Skill 与包一起换」的升级规则。
- 统一表述：工具全程在本机运行，可选的只是「实体识别用在线 Agent 还是本机 Ollama」；去掉「全本地脱敏 / 本机脱敏 / 离线脱敏」等易误解说法。

## 0.10.7 - 2026-10-01

修复包的真实漏检，并补齐 Skill 回归测试。Python 包 0.10.2 → 0.10.7（跳号对齐 Skill 版本）。

- **修复（包）**：邮箱检测改用字符集 lookaround。原正则的 `\b` 把中文字符视为单词字符，导致「邮箱zhang@example.com」这类中文标签紧贴邮箱的写法漏检，且残留扫描误报 PASS。新增回归测试 `test_email_adjacent_to_chinese_label`。
- 新增 `skills/legal-document-redactor/scripts/self_test.py`：Skill 安装自检/回归套件，虚构数据端到端覆盖双模式、指哪打哪、裁判文书式替身、verify 退出码、Ollama 链路（无 Ollama 自动 SKIP）；退出码可用于 CI/Agent 判断。

## 0.10.6 - 2026-10-01

仅 Skill 文档；Python 包不变（仍为 0.10.2）。

- SKILL.md 新增「本机 Ollama 的运行规则」：按输入类型分流（文字层→只需文本模型；扫描件给 AI→Tesseract+文本模型；扫描件涂黑→Tesseract 坐标，视觉模型不参与）；明确实体识别永远用文本模型，视觉模型仅作可选 OCR 增强（出文字不进坐标链路）；公章/签名区域仍人工圈定；补 Ollama 网络边界自检（仅监听 127.0.0.1）。

## 0.10.5 - 2026-10-01

仅 Skill 增强；Python 包不变（仍为 0.10.2）。

- 新增 `scripts/ollama_setup.py`：本机 Ollama 路径的只读环境检测——未装 Ollama 时按设备内存（Apple Silicon 统一内存分档：≥32GB→12-14B / 16-31GB→7-8B / <16GB→4B 勉强或建议用在线 Agent）打印安装与模型推荐命令；已装则列出模型并标出实体识别首选。支持 `--json` 供 Agent 判断。
- SKILL.md 明确识别方式选择时机：默认用在线 Agent，仅在用户要求用本机模型/Ollama 或文书明显高敏时询问一次；首次配置分流（已装→选模型，未装→按设备推荐，征得同意后安装）。

## 0.10.4 - 2026-10-01

仅 Skill 增强；Python 包不变（仍为 0.10.2）。

- 新增 `scripts/ollama_entities.py`：本地 Ollama 模型识别自然语言实体，产出 entities 草稿（`source=ollama-draft`）。Ollama 只负责「找」，替换仍由 CLI 确定性执行；设备可选本机 Ollama 识别或在线 Agent 识别。默认模型 `qwen3.5:latest`，支持 .docx / 文字层 .pdf / .txt / .md，纯 stdlib HTTP 调用。
- 识别带原文逐字校验（容忍模型在字符间插空格），结构性字段（证件/手机/邮箱等）仍交给确定性扫描，不让模型报。
- SKILL.md 新增「实体识别：在线 Agent 还是本机 Ollama」工作流；环境自检补 `ollama list`。

## 0.10.3 - 2026-10-01

仅 Skill 文档与模板增强；Python 包不变（仍为 0.10.2）。

- Skill 新增「指哪打哪」选择性脱敏决策表：`--keep-categories` 反向枚举、点名人名 entities、`--preserve`、整方 `--redact-party` 的选用对照。
- Skill 新增提速工作流：OCR 只跑一次后在 markdown 上迭代；`--dpi 200`；批量 `--unify`；`verify` 退出码机器化验收。
- Skill 新增环境自检（CLI 在 PATH、Tesseract chi_sim）。
- 新增 `references/redaction-standards.md`：法释〔2016〕19号、个人信息保护法、GB/T 37964-2019、上海律协 AI 指引（2026）与工具参数对照；收录工具识别盲区（出生日期、车牌号、不动产权证号、无锚点姓名）。
- 新增 `references/entities.court-style.template.json`：裁判文书上网式隐名模板（保留姓氏+某、住所地到县/区、金额模糊化）。
- 交付清单新增「反推三问」验收项。

## 0.10.2 - 2026-09-28

- Release 增加 `legal-document-redactor-skillhub.zip`。压缩包根目录就是 `SKILL.md`，可直接上传 Skillhub。不要上传整仓 zip，也不要上传 `.skill`。

## 0.10.1 - 2026-09-28

- Skill 入口改成先跑 `redact --auto-confident`。技能卡片和默认提示不再要求先通读全文。

## 0.10.0 - 2026-09-27

一次扫完发布：不必先让模型手写实体表。

- `redact --auto-confident`：一次扫完高置信姓名、单位、地址、作品名和精确金额，不必先让模型手写 `entities.json`。`production` 仍保留当事人，只自动去掉第三人。
- 结构性识别补上带空格或短横线的手机号、身份证号，以及「账号 / 收款账户」引导的账号。纯数字串不再误判为统一社会信用代码。
- 简称（以下简称「…」）与全称共用同一个替身。
- 文档：移除中英文 README 中过期的 `v0.8` 限制标题，并补齐整方脱敏的 `party-spec`、整枚公章和未选方保留边界。

## 0.9.0 - 2026-09-01

整方脱敏发布：按用户选择遮挡甲方、乙方或双方，并完整覆盖名称、签名及公章。

- 默认 README、社区文件、CLI `--help` 与 Skill 入口改为简体中文优先；英文旁路为 `README.en.md`。
- 扫描件新增整方脱敏：用户可选择只遮甲方、只遮乙方或双方；用已确认标识遮挡名称、联系方式和账号，并用人工复核区域覆盖签名及整枚公章。未选方默认完整保留。
- `redact-scan` 新增 `--redact-party`、`--party-spec` 和可选的 `--also-redact-structural-all`，附虚构模板、Skill 路由说明及回归测试。

## 0.8.0 - 2026-08-29

可用性发布：Windows CLI、可读的 PDF 占位符、面向中文用户的落地页。

- Windows/cp1252：进程启动时把 stdout/stderr 重配为 UTF-8（`errors=replace`），`redact` 成功后打印中文疑似提示不再以退出码 1 结束。
- PDF `redact` 用 PyMuPDF `china-s` 绘制中文替身（`某甲`、`[手机号]`），不再出现 Helvetica `???`。
- CLI 导入 `pymupdf`（不再用已弃用的 `fitz`）；PyMuPDF 1.28+ 上 `--version` 干净。
- 双语 README：60 秒演示、虚构 `ai` / `production` 前后对照、双模式预览图。
- GitHub About 中文优先。Issue 模板禁止粘贴真实卷宗。
- GitHub Release 除 `.skill` 外附带 wheel 与 sdist。
- Pytest 对虚构合同走真实 CLI 双模式，含 cp1252 stdio。

## 0.7.0 - 2026-08-22

Batch two-pass workflow and directory operators.

- `legal-redactor redact DIR --unify` unifies entities first, then redacts every file with `entities.consistent.json`.
- `scan` and `verify` accept directories (optional `--recursive`, `--json`).
- Suspect heuristics add label-anchored **addresses** (`住所地`/`住址`/`地址`…).
- Tests cover unify-first batch stability, directory verify/scan, and address suspects.

## 0.6.0 - 2026-08-22

Cross-file consistency for multi-document matters.

- Added `legal-redactor unify DIR -o OUTDIR` to build `entities.consistent.json` with stable aliases across a folder.
- Detects replacement conflicts (same original → different replacements) from source scans or `*.ledger.json`.
- Batch `redact DIR` now writes `entities.consistent.json`, `consistency.report.json`, and `consistency.report.md`.
- Production mode unified rows may omit replacements for party person/org/address (intentional keep).

## 0.5.0 - 2026-08-21

Reviewability release: surface likely natural-language entities without guessing replacements.

- Added heuristic **suspect** detection (role-anchored persons, org suffixes, 《work titles》).
- `scan` prints suspect hints; `redact` writes `*.suspects.json` + summary section.
- `draft-entities` includes suspect rows (`source=suspect-hint`) by default; `--no-suspects` to disable.
- Suspects are **never auto-redacted** — agent/human must confirm role and optional replacement.
- False positives/negatives expected; blocklists cover common court labels and short bank labels.

## 0.4.0 - 2026-08-21

Usability release for real multi-file workflows. Redaction modes and structural
detectors are unchanged.

- Batch `legal-redactor redact DIR -o OUTDIR --mode …` over supported files (optional `--recursive`).
- New `legal-redactor draft-entities` subcommand (scripts/draft_entities.py is now a thin wrapper).
- DOCX: prefer per-run replacements so single-run bold/italic survives; cross-run entities still fall back to paragraph collapse.
- Regression tests for batch redaction, draft-entities, and mixed-run DOCX formatting.

## 0.3.1 - 2026-08-21

Patch release: ships the post-0.3.0 distribution and CI hardening already intended for users.
No change to redaction modes, structural detectors, or OCR behavior.

- Expanded CI to Python 3.10–3.12 on Ubuntu, Windows, and macOS.
- Added a reproducible `.skill` packer (`scripts/pack_skill.py`), `SHA256SUMS.txt`, and tag-triggered GitHub Releases.
- Aligned software versions across `pyproject.toml`, package `__version__`, and `.codex-plugin/plugin.json`.
- Generalized public-tree safety scan beyond filename denylist (emails, mobiles, IDs, case numbers, home paths).
- Corrected skill package limits text for v0.3 OCR / `redact-scan` support.
- Documented release steps in CONTRIBUTING.

## 0.3.0 - 2026-08-20

- Added `legal-redactor ocr` for local Tesseract OCR of scanned PDFs → `ocr.md` + CJK-normalized markdown.
- Added `legal-redactor redact-scan` for production court path: black-box structural PII on image-only PDFs via OCR word boxes.
- Documented scanned-PDF workflow under the skill package (`references/scanned-pdf.md`).
- Clearer error when `redact` is pointed at a textless PDF (points to `ocr` / `redact-scan`).

## 0.2.0 - 2026-08-20

- Added `--keep-categories` / `--extra-categories` on `redact`, `scan`, and `verify` so production filings can keep USCC (or other structural types) without failing residual scan.
- Added fictional full-risk 委托维权服务协议 sample + entities (`examples/fictional/sample_weiquan_text.py`).
- Added entities template under the skill package and `scripts/draft_entities.py` for structural drafts.
- Fixed DOCX handling for Word track-changes (`w:ins` / `w:del`) and run-split strings by flattening revisions and replacing at paragraph level in OOXML.
- Added fictional track-changes regression fixture.
- Declared `lxml` as a direct dependency.

## 0.1.0 - 2026-08-20

- Initial public preview of `legal-redactor`.
- Dual modes: `ai` (aggressive) and `production` (selective for court/opponent).
- Same-format outputs for DOCX, text-layer PDF, and text/markdown.
- Structural detectors: PRC ID, mobile/landline, email, bank account, case number, USCC.
- Agent/manual `entities.json` with stable aliases and role-aware production keeps.
- Residual structural scan, local ledger/summary artifacts.
- Claude/Codex skill package under `skills/legal-document-redactor`.
- Fictional fixtures, pytest suite, and `scripts/run_demo.py`.
