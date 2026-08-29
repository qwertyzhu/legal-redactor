[English](README.md) | [简体中文](README.zh-CN.md)

# legal-redactor

[![CI](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml/badge.svg)](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml)
[![许可证](https://img.shields.io/github/license/qwertyzhu/legal-redactor)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](pyproject.toml)

**中国法律文书本地脱敏：给 AI 之前去标识，交法院 / 对方之前只去掉证件号、手机、邮箱。**  
输入什么格式，就返回什么格式：DOCX→DOCX，PDF→PDF（文字层），文本→文本。  
两个刚需：

1. **给在线 AI 之前**——激进脱敏，降低事项可反查性；  
2. **交法院 / 给被告之前**——保留主体与案号，去掉证件号、手机、邮箱、账号等高敏项。

> 早期预览。不替代律师判断。残留扫描通过 ≠ 自然语言标识已全部清除。

## 双模式

| 模式 | 去向 | 默认行为 |
|---|---|---|
| `ai` | 在线模型、可外传笔记、公开样例 | 激进：姓名/单位/案号/证件/手机/邮箱/账号/信用代码 + entities 清单 |
| `production` | 法院 / 对方当事人 | 保留当事人与案号；去掉证件号、手机、邮箱、银行账号、信用代码；可标记第三人 |

姓名、单位、作品名等由 Agent（或你）写入 `entities.json`；CLI 做确定性替换与结构性残留扫描。

## 60 秒上手

```console
git clone https://github.com/qwertyzhu/legal-redactor.git
cd legal-redactor
python -m pip install -e ".[dev]"
legal-redactor --version
python scripts/run_demo.py --clean
```

`run_demo.py` 会对仓库内的**虚构合同**分别跑 `ai` 与 `production`，并按原格式写出 `md` / `docx` / `pdf` 到 `demo-output/`。两次运行都应打印全部残留扫描通过。加 `--clean` 可重复跑，结果确定。

## 虚构样例：脱敏前后

样例当事人是 **郝测一**，样例手机是 **13900001111**（完全虚构）。

| 字段 | 原文 | `ai` | `production` |
|---|---|---|---|
| 当事人姓名 | 郝测一 | 去掉（替身 `某甲`） | **保留** |
| 手机 | 13900001111 | 去掉 | 去掉 |
| 案号 | （2024）京0491民初1234号 | 去掉 | **保留** |

![虚构合同 PDF 首页上部：原文 / ai / production](docs/images/dual-mode-preview.png)

```text
# 原文（节选）
法定代表人：郝测一
联系电话：13900001111
关联案号示例：（2024）京0491民初1234号

# --mode ai 之后
法定代表人：某甲
联系电话：[手机号]
关联案号示例：（20XX）XX民初XX号

# --mode production 之后
法定代表人：郝测一
联系电话：[手机号]
关联案号示例：（2024）京0491民初1234号
```

`ai` 产物不得当作起诉材料。`*.ledger.json` 禁止上传。

## 安装

以 clone 后的可编辑安装为准（当前尚未发布到 PyPI）：

```console
python -m pip install -e ".[dev]"
legal-redactor --help    # 列出 redact / scan / verify
```

只要 CLI，装 GitHub Release 的 wheel：

```console
python -m pip install https://github.com/qwertyzhu/legal-redactor/releases/download/v0.8.0/legal_redactor-0.8.0-py3-none-any.whl
```

也可从最新 [GitHub Release](https://github.com/qwertyzhu/legal-redactor/releases/latest) 获取。

### Claude Code / Codex Skill

把 `skills/legal-document-redactor` 复制或 junction 到 `~/.claude/skills/`（或 `~/.agents/skills/`）。  
Release 资产另附打包好的 `legal-document-redactor.skill` 与 `SHA256SUMS.txt`。

## 命令行

```console
legal-redactor scan contract.docx --mode ai

legal-redactor redact contract.docx --mode ai --entities entities.json -o contract.redacted-ai.docx

legal-redactor redact contract.docx --mode production --entities entities.json -o contract.redacted-production.docx

# 起诉材料需要保留统一社会信用代码时：
legal-redactor redact contract.docx --mode production --keep-categories uscc -o out.docx
legal-redactor verify out.docx --mode production --keep-categories uscc

# 扫描件 PDF（无文字层）
legal-redactor ocr scan.pdf -o workdir/
legal-redactor redact workdir/ocr.normalized.md --mode production -o workdir/out.md
legal-redactor redact-scan scan.pdf --mode production -o scan.redacted-production.pdf

legal-redactor verify contract.redacted-ai.docx --mode ai

# 生成 entities 草稿（结构性字段 + 疑似姓名/单位/作品提示）
legal-redactor draft-entities contract.docx -o entities.draft.json

# 批量脱敏（推荐两阶段：先统一替身再脱敏）
legal-redactor redact ./matters/ --mode ai --unify -o ./matters-redacted/

# 或手动 unify 后再脱敏
legal-redactor unify ./matters/ -o ./matter-unified/ --mode ai
legal-redactor redact ./matters/ --mode ai --entities ./matter-unified/entities.consistent.json -o ./matters-redacted/

# 目录扫描 / 残留校验
legal-redactor scan ./matters/ --mode ai
legal-redactor verify ./matters-redacted/ --mode ai
```

每次成功运行还会生成：

- `*.ledger.json` — 原文→替身映射（**仅本地；禁止提交 git / 禁止贴进在线 AI**）
- `*.residual.json` — 结构性残留报告
- `*.suspects.json` — 自然语言疑似实体提示（**不会自动替换**）
- `*.summary.md` — 可读对照表

批量时还会在 work dir 生成 `entities.consistent.json` 与 `consistency.report.*`。

扫描件说明见 `skills/legal-document-redactor/references/scanned-pdf.md`。  
`entities.json` 可从模板复制：`skills/legal-document-redactor/references/entities.template.json`。  
也可先跑 `legal-redactor draft-entities INPUT.docx` 生成结构性草稿 + 疑似实体提示，再人工确认角色与替身。

## 测试

```console
python -m pytest
python scripts/run_demo.py --clean
```

演示材料完全虚构。

## 限制（v0.8）

- PDF：文字层直接 `redact`；扫描件用 `ocr` / `redact-scan`（需本机 Tesseract + chi_sim）
- `redact-scan` 为 OCR 坐标涂黑，**交法院前必须人工翻页**
- 自然语言姓名需写入并确认 `entities.json`；suspects 只是提示
- 多文件上 AI：优先 `redact DIR --unify`，保证跨文件替身一致
- DOCX：单 run 内替换尽量保留加粗/斜体；跨 run 实体仍会折叠段落
- 批量输出为扁平文件名（递归时自动消歧）
- 目录 `verify` 会检查目录内所有支持后缀（勿把 ledger.json 和正文混在同一校验目录）
- 不是法律意见，不能替代律所保密流程

## 安全

不要在公开 Issue 里贴真实卷宗或 ledger。见 [SECURITY.md](SECURITY.md)。

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
