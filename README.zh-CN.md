[English](README.md) | [简体中文](README.zh-CN.md)

# legal-redactor

[![CI](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml/badge.svg)](https://github.com/qwertyzhu/legal-redactor/actions/workflows/ci.yml)
[![许可证](https://img.shields.io/github/license/qwertyzhu/legal-redactor)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](pyproject.toml)

**面向中国法律文书的本地优先、双模式脱敏工具。**  
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

## 安装

```console
git clone https://github.com/qwertyzhu/legal-redactor.git
cd legal-redactor
python -m pip install -e ".[dev]"
```

### Claude Code / Codex Skill

把 `skills/legal-document-redactor` 复制或 junction 到 `~/.claude/skills/`（或 `~/.agents/skills/`）。

## 快速使用

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
```

扫描件说明见 `skills/legal-document-redactor/references/scanned-pdf.md`。  
`entities.json` 可从模板复制：`skills/legal-document-redactor/references/entities.template.json`。  
也可先跑 `python scripts/draft_entities.py INPUT.docx` 生成结构性草稿，再手工补姓名/单位。
每次成功运行还会生成：

- `*.ledger.json` — 原文→替身映射（**仅本地；禁止提交 git / 禁止贴进在线 AI**）
- `*.residual.json` — 结构性残留报告
- `*.summary.md` — 可读对照表

## 仓库虚构演示

```console
python scripts/run_demo.py --clean
pytest
```

演示材料完全虚构。

## 限制（v0.3）

- PDF：文字层直接 `redact`；扫描件用 `ocr` / `redact-scan`（需本机 Tesseract + chi_sim）
- `redact-scan` 为 OCR 坐标涂黑，**交法院前必须人工翻页**
- 自然语言姓名依赖 `entities.json`
- DOCX 段落重写可能简化 run 级格式
- 不是法律意见，不能替代律所保密流程

## 安全

不要在公开 Issue 里贴真实卷宗或 ledger。见 [SECURITY.md](SECURITY.md)。

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
