#!/usr/bin/env python3
"""Build a reproducible `.skill` archive and SHA256SUMS for legal-redactor releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRECTORIES = ("scripts", "references", "schemas", "agents")
SKIP_DIRECTORY_NAMES = {"__pycache__", "evals", ".git"}
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
UNIX_FILE_ATTR = 0o644 << 16
SAFETY_BOUNDARIES = (
    "- 本地处理；不要求把文书上传到托管服务。",
    "- ledger 文件（*.ledger.json）是去标识密钥，仅限本地保存。",
    "- 结构性残留扫描 PASS 不等于自然语言标识已全部清除。",
    "- 公开样例均为虚构。产出须经律师复核。",
)


class PackError(ValueError):
    """Raised when a skill archive cannot be built safely."""


def project_version(root: Path) -> str:
    in_project = False
    for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project and stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise PackError("pyproject.toml is missing project.version")


def plugin_version(root: Path) -> str | None:
    path = root / ".codex-plugin" / "plugin.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise PackError(".codex-plugin/plugin.json is missing version")
    return version


def package_version(root: Path) -> str | None:
    init = root / "src" / "legal_redactor" / "__init__.py"
    if not init.is_file():
        return None
    for line in init.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def skill_frontmatter_name(text: str) -> str:
    if not text.startswith("---"):
        raise PackError("SKILL.md is missing YAML frontmatter")
    closing = text.find("\n---", 3)
    if closing < 0:
        raise PackError("SKILL.md frontmatter is not closed")
    for line in text[4:closing].splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    raise PackError("SKILL.md frontmatter is missing name")


def excluded(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES or path.suffix in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIRECTORY_NAMES for part in path.parts)


def iter_skill_files(skill_dir: Path) -> list[Path]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise PackError(f"{skill_dir.name} is missing SKILL.md")
    name = skill_frontmatter_name(skill_md.read_text(encoding="utf-8"))
    if name != skill_dir.name:
        raise PackError(
            f"{skill_dir.name}/SKILL.md name {name!r} does not match the directory"
        )

    files = [skill_md]
    for directory_name in RUNTIME_DIRECTORIES:
        runtime_dir = skill_dir / directory_name
        if not runtime_dir.is_dir():
            continue
        for path in sorted(runtime_dir.rglob("*")):
            if path.is_file() and not excluded(path):
                files.append(path)

    if not any(path.relative_to(skill_dir).parts[0] == "scripts" for path in files):
        raise PackError(f"{skill_dir.name} is missing scripts/")
    return files


def discover_skills(skills_dir: Path) -> list[Path]:
    found = [
        path
        for path in sorted(skills_dir.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    ]
    if not found:
        raise PackError(f"no SKILL.md directories found in {skills_dir}")
    return found


def archive_name(path: Path, skill_dir: Path) -> str:
    relative = path.relative_to(skill_dir.parent).as_posix()
    if relative.startswith("/") or ".." in relative.split("/"):
        raise PackError(f"refusing unsafe archive member: {relative}")
    return relative


def write_zip(skill_dir: Path, destination: Path) -> None:
    files = iter_skill_files(skill_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        for path in files:
            info = zipfile.ZipInfo(archive_name(path, skill_dir), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = UNIX_FILE_ATTR
            archive.writestr(info, path.read_bytes())


def write_sha256sums(output_dir: Path, filenames: Iterable[str]) -> Path:
    lines = []
    for filename in filenames:
        digest = hashlib.sha256((output_dir / filename).read_bytes()).hexdigest().upper()
        lines.append(f"{digest}  {filename}")
    checksums = output_dir / "SHA256SUMS.txt"
    checksums.write_text("\n".join(lines) + "\n", encoding="ascii")
    return checksums


def changelog_section(changelog: str, version: str) -> str:
    lines = changelog.splitlines()
    start = None
    heading = f"## {version}"
    for index, line in enumerate(lines):
        if line == heading or line.startswith(heading + " "):
            start = index
            break
    if start is None:
        raise PackError(f"CHANGELOG.md is missing a {version} section")
    stop = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            stop = index
            break
    body = "\n".join(lines[start:stop]).strip()
    if not body:
        raise PackError(f"CHANGELOG.md section {version} is empty")
    return body


def write_release_notes(root: Path, output_dir: Path, version: str) -> Path:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = (
        "legal-redactor {version}\n\n"
        "{body}\n\n"
        "## 安全边界\n\n"
        "{safety}\n"
    ).format(
        version=version,
        body=changelog_section(changelog, version),
        safety="\n".join(SAFETY_BOUNDARIES),
    )
    path = output_dir / "RELEASE_NOTES.md"
    path.write_text(notes, encoding="utf-8")
    return path


def pack_skills(root: Path, output_dir: Path, expect_version: str | None = None) -> list[Path]:
    version = project_version(root)
    plugged = plugin_version(root)
    if plugged is not None and version != plugged:
        raise PackError(
            f"version mismatch: pyproject.toml has {version!r}, "
            f"plugin.json has {plugged!r}"
        )
    pkg = package_version(root)
    if pkg is not None and version != pkg:
        raise PackError(
            f"version mismatch: pyproject.toml has {version!r}, "
            f"__init__.__version__ has {pkg!r}"
        )
    if expect_version is not None and expect_version != version:
        raise PackError(
            f"version mismatch: expected {expect_version!r}, project is {version!r}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    archives: list[Path] = []
    for skill_dir in discover_skills(root / "skills"):
        archive = output_dir / f"{skill_dir.name}.skill"
        write_zip(skill_dir, archive)
        archives.append(archive)

    write_sha256sums(output_dir, [path.name for path in archives])
    write_release_notes(root, output_dir, version)
    return archives


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory for .skill archives (default: dist/)",
    )
    parser.add_argument(
        "--expect-version",
        help="Fail unless project version matches (no v prefix)",
    )
    args = parser.parse_args()
    try:
        archives = pack_skills(ROOT, args.output_dir, args.expect_version)
    except PackError as exc:
        parser.error(str(exc))
    for archive in archives:
        print(archive)
    print(args.output_dir / "SHA256SUMS.txt")
    print(args.output_dir / "RELEASE_NOTES.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
