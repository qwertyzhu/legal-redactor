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
import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest
from lxml import etree

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


@pytest.mark.parametrize(
    "part, xml, secret, placeholder",
    [
        (
            "word/charts/chart1.xml",
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            '<c:strCache><c:pt idx="0"><c:v>13700003333</c:v></c:pt></c:strCache></c:chartSpace>',
            "13700003333",
            "[手机号]",
        ),
        (
            "word/charts/chart2.xml",
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<c:title><c:tx><c:rich><a:p><a:r><a:t>13700003333</a:t></a:r></a:p>"
            "</c:rich></c:tx></c:title></c:chartSpace>",
            "13700003333",
            "[手机号]",
        ),
        (
            "word/diagrams/data1.xml",
            '<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<dgm:ptLst><dgm:pt><dgm:t><a:p><a:r><a:t>1101051990</a:t></a:r>"
            "<a:r><a:t>0101123X</a:t></a:r></a:p></dgm:t></dgm:pt></dgm:ptLst></dgm:dataModel>",
            "11010519900101123X",
            "[身份证号]",
        ),
    ],
    ids=["chart-cache", "chart-rich-text", "smartart-split-runs"],
)
def test_shipped_cli_docx_charts_and_smartart(
    tmp_path: Path, part: str, xml: str, secret: str, placeholder: str
) -> None:
    src = tmp_path / "original.docx"
    docx_io.create_sample_docx(src, "示例文档。")
    with zipfile.ZipFile(src, "a") as archive:
        archive.writestr(part, xml)

    before = _run_cli("verify", str(src), "--mode", "ai", "--strict", cwd=tmp_path)
    assert before.returncode == 2, before.stderr + before.stdout
    assert secret in before.stdout

    out = tmp_path / "redacted.docx"
    work = tmp_path / "work"
    redacted = _run_cli(
        "redact", str(src), "--mode", "ai", "-o", str(out), "--work-dir", str(work), cwd=tmp_path
    )
    assert redacted.returncode == 0, redacted.stderr + redacted.stdout
    with zipfile.ZipFile(out) as archive:
        text = "".join(etree.fromstring(archive.read(part)).itertext())
    assert secret not in text
    assert placeholder in text

    after = _run_cli("verify", str(out), "--mode", "ai", "--strict", cwd=tmp_path)
    assert after.returncode == 0, after.stderr + after.stdout
    assert "status: PASS" in after.stdout

    restored = tmp_path / "restored.docx"
    restore = _run_cli(
        "restore", str(out), "--ledger", str(work / "redacted.ledger.json"), "-o", str(restored),
        cwd=tmp_path,
    )
    assert restore.returncode == 0, restore.stderr + restore.stdout
    with zipfile.ZipFile(restored) as archive:
        assert secret in "".join(etree.fromstring(archive.read(part)).itertext())


@pytest.mark.parametrize("container", ["numCache", "numLit"])
def test_shipped_cli_docx_chart_preserves_numeric_measurements(tmp_path: Path, container: str) -> None:
    src = tmp_path / "original.docx"
    docx_io.create_sample_docx(src, "示例文档。")
    part = "word/charts/chart1.xml"
    with zipfile.ZipFile(src, "a") as archive:
        archive.writestr(
            part,
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f"<c:{container}><c:pt idx=\"0\"><c:v>13700003333</c:v></c:pt></c:{container}></c:chartSpace>",
        )

    out = tmp_path / "redacted.docx"
    redacted = _run_cli(
        "redact", str(src), "--mode", "ai", "-o", str(out), "--work-dir", str(tmp_path / "work"),
        cwd=tmp_path,
    )
    assert redacted.returncode == 0, redacted.stderr + redacted.stdout
    with zipfile.ZipFile(out) as archive:
        assert b"<c:v>13700003333</c:v>" in archive.read(part)

    for docx in (src, out):
        verify = _run_cli("verify", str(docx), "--mode", "ai", "--strict", cwd=tmp_path)
        assert verify.returncode == 0, verify.stderr + verify.stdout


def test_shipped_cli_docx_chart_redacts_numeric_category_labels(tmp_path: Path) -> None:
    src = tmp_path / "original.docx"
    docx_io.create_sample_docx(src, "示例文档。")
    part = "word/charts/chart1.xml"
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
    with zipfile.ZipFile(src, "a") as archive:
        archive.writestr(
            part,
            f'<c:chartSpace xmlns:c="{ns["c"]}"><c:chart><c:plotArea><c:barChart><c:ser>'
            '<c:cat><c:numRef><c:f>Sheet1!$A$2</c:f><c:numCache><c:pt idx="0"><c:v>13700003333</c:v>'
            '</c:pt></c:numCache></c:numRef></c:cat>'
            '<c:val><c:numLit><c:pt idx="0"><c:v>13700003333</c:v></c:pt></c:numLit></c:val>'
            '</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>',
        )

    before = _run_cli("verify", str(src), "--mode", "ai", "--strict", cwd=tmp_path)
    assert before.returncode == 2, before.stderr + before.stdout
    assert "mobile: 13700003333" in before.stdout

    out = tmp_path / "redacted.docx"
    redacted = _run_cli(
        "redact", str(src), "--mode", "ai", "-o", str(out), "--work-dir", str(tmp_path / "work"),
        cwd=tmp_path,
    )
    assert redacted.returncode == 0, redacted.stderr + redacted.stdout
    with zipfile.ZipFile(out) as archive:
        chart = etree.fromstring(archive.read(part))
    assert chart.findtext(".//c:cat//c:v", namespaces=ns) == "[手机号]"
    assert chart.findtext(".//c:val//c:v", namespaces=ns) == "13700003333"

    after = _run_cli("verify", str(out), "--mode", "ai", "--strict", cwd=tmp_path)
    assert after.returncode == 0, after.stderr + after.stdout


@pytest.mark.parametrize("separate_parts", [False, True], ids=["same-chart", "separate-charts"])
def test_shipped_cli_docx_chart_keeps_labels_separate(tmp_path: Path, separate_parts: bool) -> None:
    src = tmp_path / "original.docx"
    docx_io.create_sample_docx(src, "示例文档。")
    labels = (("137000",), ("03333",)) if separate_parts else (("137000", "03333"),)
    charts = {}
    for index, values in enumerate(labels, 1):
        points = "".join(f'<c:pt idx="{i}"><c:v>{value}</c:v></c:pt>' for i, value in enumerate(values))
        charts[f"word/charts/chart{index}.xml"] = (
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f"<c:strLit>{points}</c:strLit></c:chartSpace>"
        )
    with zipfile.ZipFile(src, "a") as archive:
        for name, xml in charts.items():
            archive.writestr(name, xml)

    before = _run_cli("verify", str(src), "--mode", "ai", "--strict", cwd=tmp_path)
    assert before.returncode == 0, before.stderr + before.stdout

    out = tmp_path / "redacted.docx"
    redacted = _run_cli(
        "redact", str(src), "--mode", "ai", "-o", str(out), "--work-dir", str(tmp_path / "work"),
        cwd=tmp_path,
    )
    assert redacted.returncode == 0, redacted.stderr + redacted.stdout
    assert "replaced:    0" in redacted.stdout
    with zipfile.ZipFile(out) as archive:
        for name, xml in charts.items():
            assert etree.tostring(etree.fromstring(archive.read(name))) == xml.encode()

    after = _run_cli("verify", str(out), "--mode", "ai", "--strict", cwd=tmp_path)
    assert after.returncode == 0, after.stderr + after.stdout
