"""Drive the shipped CLI entry on the in-repo fictional contract.

These tests start at `python -m legal_redactor` (the packaged module
entry / console-script equivalent). They do not call `redact_file` or
reimplement replacement.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from legal_redactor import __version__
from legal_redactor.formats import docx_io, pdf_io

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fictional"

_spec = importlib.util.spec_from_file_location(
    "sample_contract_text", FIXTURES / "sample_contract_text.py"
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(_mod)
SAMPLE = _mod.FICTIONAL_CONTRACT

PARTY = "郝测一"
MOBILE = "13900001111"
CASE_NO = "（2024）京0491民初1234号"
ID_NO = "110101199001011234"


def _run_cli(
    *args: str,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "legal_redactor", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or ROOT,
        env=env,
        check=False,
    )


def test_cli_version_matches_package() -> None:
    proc = _run_cli("--version")
    assert proc.returncode == 0, proc.stderr
    blob = proc.stdout + proc.stderr
    assert f"legal-redactor {__version__}" in blob
    assert "deprecated" not in blob.lower()


def test_cli_help_lists_redact_scan_verify() -> None:
    proc = _run_cli("--help")
    assert proc.returncode == 0, proc.stderr
    help_text = proc.stdout
    assert "redact" in help_text
    assert "scan" in help_text
    assert "verify" in help_text
    assert "脱敏" in help_text or "证件号" in help_text

    scan_help = _run_cli("redact-scan", "--help")
    assert scan_help.returncode == 0, scan_help.stderr
    assert "--redact-party" in scan_help.stdout
    assert "--party-spec" in scan_help.stdout
    assert "甲方" in scan_help.stdout and "乙方" in scan_help.stdout


def test_shipped_cli_redacts_fictional_contract_ai_vs_production(tmp_path: Path) -> None:
    src = tmp_path / "sample_contract.md"
    src.write_text(SAMPLE, encoding="utf-8")
    entities_ai = tmp_path / "entities_ai.json"
    entities_prod = tmp_path / "entities_production.json"
    shutil.copy(FIXTURES / "entities_ai.json", entities_ai)
    shutil.copy(FIXTURES / "entities_production.json", entities_prod)

    ai_out = tmp_path / "sample_contract.redacted-ai.md"
    prod_out = tmp_path / "sample_contract.redacted-production.md"
    ai_work = tmp_path / "work-ai"
    prod_work = tmp_path / "work-production"

    ai = _run_cli(
        "redact",
        str(src),
        "--mode",
        "ai",
        "--entities",
        str(entities_ai),
        "-o",
        str(ai_out),
        "--work-dir",
        str(ai_work),
        cwd=tmp_path,
    )
    assert ai.returncode == 0, ai.stderr + "\n" + ai.stdout
    assert ai_out.suffix == src.suffix == ".md"
    ai_text = ai_out.read_text(encoding="utf-8")
    assert PARTY not in ai_text
    assert MOBILE not in ai_text
    assert CASE_NO not in ai_text
    assert ID_NO not in ai_text

    prod = _run_cli(
        "redact",
        str(src),
        "--mode",
        "production",
        "--entities",
        str(entities_prod),
        "-o",
        str(prod_out),
        "--work-dir",
        str(prod_work),
        cwd=tmp_path,
    )
    assert prod.returncode == 0, prod.stderr + "\n" + prod.stdout
    assert prod_out.suffix == src.suffix == ".md"
    prod_text = prod_out.read_text(encoding="utf-8")
    assert PARTY in prod_text
    assert CASE_NO in prod_text
    assert MOBILE not in prod_text
    assert ID_NO not in prod_text

    for mode, out in (("ai", ai_out), ("production", prod_out)):
        verified = _run_cli("verify", str(out), "--mode", mode, cwd=tmp_path)
        assert verified.returncode == 0, verified.stderr + "\n" + verified.stdout
        residual_path = (tmp_path / f"work-{mode}") / f"{out.stem}.residual.json"
        residual = json.loads(residual_path.read_text(encoding="utf-8"))
        assert residual["ok"] is True, residual
        assert residual["mode"] == mode


def test_shipped_cli_production_redact_survives_cp1252_stdio(tmp_path: Path) -> None:
    """Windows consoles (and PYTHONIOENCODING=cp1252) must not turn a
    successful production redact into exit 1 when printing CJK suspects.
    """
    src = tmp_path / "sample_contract.md"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "sample_contract.redacted-production.md"
    proc = _run_cli(
        "redact",
        str(src),
        "--mode",
        "production",
        "--entities",
        str(FIXTURES / "entities_production.json"),
        "-o",
        str(out),
        "--work-dir",
        str(tmp_path / "work"),
        cwd=tmp_path,
        extra_env={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"},
    )
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    assert "charmap" not in proc.stderr
    text = out.read_text(encoding="utf-8")
    assert PARTY in text
    assert CASE_NO in text
    assert MOBILE not in text
    assert ID_NO not in text
    residual = json.loads(
        (tmp_path / "work" / f"{out.stem}.residual.json").read_text(encoding="utf-8")
    )
    assert residual["ok"] is True, residual


def test_shipped_cli_docx_keeps_suffix_and_ai_content(tmp_path: Path) -> None:
    src = tmp_path / "sample_contract.docx"
    docx_io.create_sample_docx(src, SAMPLE)
    out = tmp_path / "sample_contract.redacted-ai.docx"
    proc = _run_cli(
        "redact",
        str(src),
        "--mode",
        "ai",
        "--entities",
        str(FIXTURES / "entities_ai.json"),
        "-o",
        str(out),
        "--work-dir",
        str(tmp_path / "work"),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    assert out.is_file()
    assert out.suffix == src.suffix == ".docx"
    text = docx_io.extract_text(out)
    assert PARTY not in text
    assert MOBILE not in text
    assert CASE_NO not in text
    residual = json.loads(
        (tmp_path / "work" / f"{out.stem}.residual.json").read_text(encoding="utf-8")
    )
    assert residual["ok"] is True, residual


_NATURAL_PARTIES = ("顾南枝", "沈测禾")
_PARTY_CLAUSE = "乙方应当在交付时签收。"
_PARTY_CONTRACT = f"乙方：{_NATURAL_PARTIES[0]}\n乙方签字: {_NATURAL_PARTIES[1]}\n{_PARTY_CLAUSE}"


@pytest.fixture(params=["txt", "docx", "pdf"])
def party_contract_file(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    src = tmp_path / f"party-contract.{request.param}"
    if src.suffix == ".docx":
        docx_io.create_sample_docx(src, _PARTY_CONTRACT)
    elif src.suffix == ".pdf":
        pdf_io.create_sample_pdf(src, _PARTY_CONTRACT)
    else:
        src.write_text(_PARTY_CONTRACT, encoding="utf-8")
    return src


@pytest.mark.parametrize(
    "profile_args", [("--mode", "ai"), ("--profile", "publish")], ids=["ai", "publish"]
)
def test_cli_redacts_natural_person_parties(
    party_contract_file: Path, profile_args: tuple[str, str], tmp_path: Path
) -> None:
    out = tmp_path / f"redacted{party_contract_file.suffix}"
    result = _run_cli("redact", str(party_contract_file), *profile_args, "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    if out.suffix == ".docx":
        text = docx_io.extract_text(out)
    elif out.suffix == ".pdf":
        text = pdf_io.extract_text(out)
    else:
        text = out.read_text(encoding="utf-8")
    assert not {name for name in _NATURAL_PARTIES if name in text}, text
    assert "乙方签字" in text and _PARTY_CLAUSE in text

    verified = _run_cli("verify", str(out), *profile_args, "--strict", cwd=tmp_path)
    assert verified.returncode == 0, verified.stdout + verified.stderr


@pytest.mark.parametrize(
    "profile_args", [("--mode", "ai"), ("--profile", "publish")], ids=["ai", "publish"]
)
def test_cli_strict_verify_warns_on_natural_person_parties(
    party_contract_file: Path, profile_args: tuple[str, str], tmp_path: Path
) -> None:
    result = _run_cli("verify", str(party_contract_file), *profile_args, "--strict", cwd=tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "status: WARN" in result.stdout
    assert all(f"person: {name}" in result.stdout for name in _NATURAL_PARTIES)


@pytest.mark.parametrize(
    "profile_args", [("--mode", "production"), ("--profile", "judgment-online")],
    ids=["production", "judgment-online"],
)
def test_cli_preserves_natural_person_parties_in_filing_modes(
    profile_args: tuple[str, str], tmp_path: Path
) -> None:
    src = tmp_path / "party-contract.txt"
    src.write_text(_PARTY_CONTRACT, encoding="utf-8")
    out = tmp_path / "redacted.txt"
    result = _run_cli("redact", str(src), *profile_args, "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8") == _PARTY_CONTRACT
    verified = _run_cli("verify", str(out), *profile_args, "--strict", cwd=tmp_path)
    assert verified.returncode == 0, verified.stdout + verified.stderr


def test_cli_defers_party_name_that_also_appears_in_a_clause(tmp_path: Path) -> None:
    text = "乙方：方可\n款到后乙方方可开票。"
    src = tmp_path / "party-contract.txt"
    src.write_text(text, encoding="utf-8")
    out = tmp_path / "redacted.txt"
    result = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8") == text
    assert "pending=1" in result.stdout
    assert "须人工核对，不要直接补入 entities.json" in result.stdout
    verified = _run_cli("verify", str(out), "--mode", "ai", "--strict", cwd=tmp_path)
    assert verified.returncode == 1, verified.stdout + verified.stderr
