# 贡献指南

欢迎改进这套本地优先的法律文书脱敏工具。英文见 [CONTRIBUTING.en.md](CONTRIBUTING.en.md)。

## 规则

1. 测试、样例、演示和截图里**只用虚构数据**。
2. 不要提交 `*.ledger.json`、客户路径或在办卷宗。
3. 保持 Agent / CLI 分工：判断写在文档和 skill 里；确定性替换与校验写在 Python 里。
4. 新增结构性检测器必须带测试，并写明误报边界。
5. PDF 路径不得在未支持扫描件时静默声称已支持。
6. 软件版本必须对齐：`pyproject.toml`、`src/legal_redactor/__init__.py`、`.codex-plugin/plugin.json`。

## 开发环境

```console
python -m pip install -e ".[dev]"
pytest
python scripts/run_demo.py --clean
python scripts/pack_skill.py --output-dir dist
```

## Pull request

- 改动小、可审
- 行为变化时更新 README / CHANGELOG
- CI 须在 Ubuntu、Windows、macOS 的 Python 3.10–3.12 上通过

## 发布

1. 三个版本号一起升，并在 CHANGELOG 增加对应章节。
2. 推送到 `main`，再打标签：

```console
git tag -a v0.8.0 -m "legal-redactor v0.8.0"
git push origin v0.8.0
```

3. Release 工作流会打包 `legal-document-redactor.skill`、写 `SHA256SUMS.txt`、构建 wheel/sdist，并发布 GitHub Release。
4. 不要改写已发布的标签。

### PyPI（可选）

包名 `legal-redactor` 尚未发布到 PyPI。准备发布时：

1. 创建 PyPI 项目，并把本 GitHub 仓库加为 **trusted publisher**
   （environment 名 `pypi`，工作流 `.github/workflows/pypi.yml`）。
2. Actions → PyPI → Run workflow，输入 `publish`。

在此之前，文档中的安装方式仍是 clone 后 `pip install -e ".[dev]"`，
或 `pip install git+https://github.com/qwertyzhu/legal-redactor.git`。

演示文案改动后刷新 README 预览图：

```console
python scripts/render_demo_preview.py
```
