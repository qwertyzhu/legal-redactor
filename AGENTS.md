# AGENTS.md

本仓库是一个 Agent Skill。所有 Agent（Codex、Claude Code、Cursor 等）的**唯一操作说明**在：

- [`skills/legal-document-redactor/SKILL.md`](skills/legal-document-redactor/SKILL.md)

先读它，再按需读 `skills/legal-document-redactor/references/`。不要在这里重复规则。

开发者须知：

- 安装：`pip install -e '.[dev]'`；可选 `[crypto]`（加密 ledger）、`[ner]`（LTP 人名疑似）、`[vision]`（OpenCV 公章草稿）；图片 OCR 需系统 Tesseract + `chi_sim`。`legal-redactor doctor` 查看可选组件。
- 测试：`pytest -q`（可选组件缺失时相关用例自动跳过）。
- 版本号需在 `pyproject.toml`、`src/legal_redactor/__init__.py`、`.codex-plugin/plugin.json` 与 `CHANGELOG.md` 同步。
- 绝不提交真实当事人信息；测试样例只用虚构数据。
