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

import pymupdf as fitz
import pytest

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


def _write_pdf_with_scan(path: Path, *, text_cover: bool) -> int:
    with fitz.open() as source, fitz.open() as pdf:
        page = source.new_page()
        page.insert_text((36, 50), "Synthetic attachment: test@example.com")
        if text_cover:
            pdf.new_page().insert_text((36, 50), "Case cover with extractable text")
        pdf.new_page().insert_image(page.rect, pixmap=page.get_pixmap())
        pdf.save(path)
        return pdf.page_count


@pytest.mark.parametrize("mode", ["ai", "production"])
@pytest.mark.parametrize("text_cover", [False, True], ids=["scan-only", "mixed"])
def test_cli_redact_rejects_pdf_with_uncovered_scan_pages(
    tmp_path: Path, mode: str, text_cover: bool
) -> None:
    src = tmp_path / "scan.pdf"
    scan_page = _write_pdf_with_scan(src, text_cover=text_cover)
    out = tmp_path / "redacted.pdf"

    proc = _run_cli("redact", str(src), "--mode", mode, "-o", str(out))

    assert proc.returncode == 1, proc.stderr + proc.stdout
    assert f"第 {scan_page} 页" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize("text_cover", [False, True], ids=["scan-only", "mixed"])
@pytest.mark.parametrize("batch", [False, True], ids=["file", "directory"])
def test_cli_verify_strict_fails_with_uncovered_scan_pages(
    tmp_path: Path, text_cover: bool, batch: bool
) -> None:
    src = tmp_path / "scan.pdf"
    scan_page = _write_pdf_with_scan(src, text_cover=text_cover)

    proc = _run_cli("verify", str(tmp_path if batch else src), "--mode", "ai", "--strict", "--json")

    assert proc.returncode == 2, proc.stderr + proc.stdout
    report = json.loads(proc.stdout)
    assert report["status"] == "FAIL"
    row = report["results"][0] if batch else report
    assert row["hits"][0]["category"] == "unverified_scan"
    assert f"第 {scan_page} 页" in row["hits"][0]["text"]


def test_cli_redacts_and_verifies_text_pdf_with_blank_page(tmp_path: Path) -> None:
    src = tmp_path / "text.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((36, 50), "Contact: test@example.com")
        pdf.new_page()
        pdf.save(src)
    out = tmp_path / "redacted.pdf"

    redact = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out))
    verify = _run_cli("verify", str(out), "--mode", "ai", "--strict")

    assert redact.returncode == 0, redact.stderr + redact.stdout
    assert verify.returncode == 0, verify.stderr + verify.stdout
    assert "status: PASS" in verify.stdout


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("allow_residual", [False, True])
def test_cli_batch_reports_skipped_scan(
    tmp_path: Path, json_output: bool, allow_residual: bool
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "plain.txt").write_text("Fictional sample\n", encoding="utf-8")
    scan = inputs / "scan.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), 0)
        image.clear_with(255)
        page.insert_image(page.rect, pixmap=image)
        doc.save(scan)

    output = tmp_path / "output"
    flags = (["--json"] if json_output else []) + (["--allow-residual"] if allow_residual else [])
    proc = _run_cli("redact", str(inputs), "--mode", "ai", "-o", str(output), *flags)

    assert (output / "plain.redacted-ai.txt").is_file()
    assert not (output / "scan.redacted-ai.pdf").exists()
    if json_output:
        batch = json.loads(proc.stdout)
        assert [entry["status"] for entry in batch["files"]] == ["PASS"]
        assert [entry["input"] for entry in batch["skipped"]] == [str(scan)]
        assert "没有文字层" in batch["skipped"][0]["reason"]
        assert (batch["ok"], proc.returncode) == (False, 1)
    else:
        assert "[SKIP] scan.pdf:" in proc.stderr
        assert proc.returncode == 1
        assert "无待补实体。到此停止" not in proc.stdout


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
