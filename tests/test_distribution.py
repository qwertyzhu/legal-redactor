from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACK_SCRIPT = ROOT / "scripts" / "pack_skill.py"

PACK_SPEC = importlib.util.spec_from_file_location("legal_redactor_pack_skill", PACK_SCRIPT)
assert PACK_SPEC is not None and PACK_SPEC.loader is not None
pack_skill = importlib.util.module_from_spec(PACK_SPEC)
sys.modules[PACK_SPEC.name] = pack_skill
PACK_SPEC.loader.exec_module(pack_skill)


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match is not None, "pyproject.toml missing project version"
    return match.group(1)


def test_pyproject_has_no_license_classifier() -> None:
    # PEP 639: license = "Apache-2.0" plus a License :: classifier breaks
    # recent setuptools (CI pip install -e ".[dev]").
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "License ::" not in text


def test_versions_aligned() -> None:
    version = _pyproject_version()
    assert pack_skill.project_version(ROOT) == version
    assert pack_skill.package_version(ROOT) == version
    assert pack_skill.plugin_version(ROOT) == version


def test_pack_skill_builds_reproducible_archive(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    archives = pack_skill.pack_skills(ROOT, first, expect_version=_pyproject_version())
    pack_skill.pack_skills(ROOT, second, expect_version=_pyproject_version())

    assert [path.name for path in archives] == ["legal-document-redactor.skill"]

    checksums = (first / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    assert checksums == (second / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()

    archive = archives[0]
    replica = second / archive.name
    assert archive.read_bytes() == replica.read_bytes()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest().upper()
    assert f"{digest}  {archive.name}" in checksums

    with zipfile.ZipFile(archive) as package:
        members = package.namelist()
    assert "legal-document-redactor/SKILL.md" in members
    assert all(member.startswith("legal-document-redactor/") for member in members)
    assert all("\\" not in member for member in members)
    assert not any("/evals/" in member or member.endswith(".pyc") for member in members)
    assert any(member.startswith("legal-document-redactor/scripts/") for member in members)
    assert any(member.startswith("legal-document-redactor/agents/") for member in members)
    assert any(member.startswith("legal-document-redactor/schemas/") for member in members)

    notes = (first / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert notes.startswith(f"legal-redactor {_pyproject_version()}")
    assert f"## {_pyproject_version()}" in notes
    assert "*.ledger.json" in notes
    assert "本地" in notes


def test_pack_skill_rejects_version_mismatch(tmp_path: Path) -> None:
    with pytest.raises(pack_skill.PackError, match="expected '0.0.0'"):
        pack_skill.pack_skills(ROOT, tmp_path, expect_version="0.0.0")


def test_changelog_section_matches_version() -> None:
    body = pack_skill.changelog_section(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        _pyproject_version(),
    )
    assert body.startswith(f"## {_pyproject_version()}")


def test_default_readme_is_chinese() -> None:
    default = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README.en.md").read_text(encoding="utf-8")
    stub = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "## 双模式" in default
    assert "证件号" in default
    assert "给 AI" in default
    assert "## Two modes" in english
    assert "## 双模式" not in english
    assert "## Two modes" not in default
    assert "README.md" in stub
    assert "README.en.md" in stub


def test_default_readme_does_not_repeat_sidecar_list() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`*.ledger.json`" in text
    assert text.count("`*.residual.json`") == 1
    assert text.count("`*.suspects.json`") == 1
    assert text.count("`*.summary.md`") == 1


def test_readmes_show_dual_mode_demo_and_before_after() -> None:
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "`ai`" in text
        assert "`production`" in text
        assert "郝测一" in text
        assert "13900001111" in text
        assert "scripts/run_demo.py" in text
        assert "legal-redactor" in text
        assert "docs/images/dual-mode-preview.png" in text
    zh = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "给 AI" in zh
    assert "证件号" in zh


def test_community_files_are_chinese_first() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "贡献" in contributing
    assert "虚构" in contributing
    assert "禁止" in security or "不要" in security
    assert "卷宗" in security or "合同" in security
    current = pack_skill.changelog_section(changelog, _pyproject_version())
    assert any("\u4e00" <= ch <= "\u9fff" for ch in current)


def test_skill_entry_is_chinese() -> None:
    skill = (ROOT / "skills" / "legal-document-redactor" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "name: legal-document-redactor" in skill
    assert "脱敏" in skill
    assert "证件号" in skill or "去标识" in skill
    assert "`ai`" in skill and "`production`" in skill
