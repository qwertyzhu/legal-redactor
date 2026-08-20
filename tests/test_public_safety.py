from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Public tree must not ship live ledgers or client dumps.
FORBIDDEN_NAME_PARTS = (
    "clients.md",
    "credentials",
    ".env",
    "ledger.json",
)


def test_no_forbidden_filenames():
    bad = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or "demo-output" in path.parts:
            continue
        name = path.name.lower()
        for part in FORBIDDEN_NAME_PARTS:
            if part in name:
                bad.append(path)
    assert not bad, f"forbidden paths: {bad}"


def test_skill_md_exists():
    skill = ROOT / "skills" / "legal-document-redactor" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert "name: legal-document-redactor" in text
    assert "ai" in text and "production" in text
