"""Drive the shipped CLI entry on the in-repo fictional contract.

These tests start at `python -m legal_redactor` (the packaged module
entry / console-script equivalent). They do not call `redact_file` or
reimplement replacement.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from legal_redactor import __version__
from legal_redactor.formats import docx_io

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


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "legal_redactor", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or ROOT,
        check=False,
    )


def test_cli_version_matches_package() -> None:
    proc = _run_cli("--version")
    assert proc.returncode == 0, proc.stderr
    assert f"legal-redactor {__version__}" in (proc.stdout + proc.stderr)


def test_cli_help_lists_redact_scan_verify() -> None:
    proc = _run_cli("--help")
    assert proc.returncode == 0, proc.stderr
    help_text = proc.stdout
    assert "redact" in help_text
    assert "scan" in help_text
    assert "verify" in help_text


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
