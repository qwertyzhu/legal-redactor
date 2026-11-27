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
from docx import Document
from lxml import etree

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


@pytest.mark.parametrize(
    ("encoding", "note"),
    [("gbk", ""), ("gb18030", "𠮷"), ("utf-8", "𠮷")],
    ids=["gbk", "gb18030", "utf8"],
)
def test_cli_redacts_encoded_text(tmp_path: Path, encoding: str, note: str) -> None:
    src = tmp_path / "input.txt"
    text = f"联系电话：{MOBILE}\r\n卷宗备注：保留原文{note}\n结束。\r"
    src.write_bytes(text.encode(encoding))
    out = tmp_path / "redacted.txt"

    proc = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert out.read_bytes().decode("utf-8") == text.replace(MOBILE, "[手机号]")


@pytest.mark.parametrize(
    ("original", "redacted"),
    [("甲方签字：郝测一", "甲方签字：某甲"), ("作品名称：清月传", "作品名称：某作品")],
    ids=["signatory", "work-title"],
)
def test_cli_redacts_cr_separated_text(tmp_path: Path, original: str, redacted: str) -> None:
    src = tmp_path / "input.txt"
    src.write_bytes(f"说明\r{original}\r附注无关\r".encode("utf-8"))
    out = tmp_path / "redacted.txt"

    proc = _run_cli("redact", str(src), "--profile", "ai", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert out.read_bytes().decode("utf-8") == f"说明\r{redacted}\r附注无关\r"


@pytest.mark.parametrize("invoice", ["042", "042-"])
def test_cli_landline_does_not_span_txt_lines(tmp_path: Path, invoice: str) -> None:
    src = tmp_path / "schedule.txt"
    text = f"发票编号：{invoice}\n2025-03-14 开庭。\n"
    src.write_text(text, encoding="utf-8")
    out = tmp_path / "redacted.txt"

    proc = _run_cli("redact", str(src), "--profile", "production", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert out.read_text(encoding="utf-8") == text
    residual = json.loads(out.with_suffix(".residual.json").read_text(encoding="utf-8"))
    assert residual["status"] == "PASS"
    assert residual["hits"] == []


@pytest.mark.parametrize("number", ["010-\n12345678", "010-1234- \t\r\n\t5678"])
def test_cli_redacts_landline_wrapped_after_hyphen(tmp_path: Path, number: str) -> None:
    src = tmp_path / "phone.txt"
    src.write_bytes(f"电话：{number}\r\n备注：保留。\r\n".encode("utf-8"))
    out = tmp_path / "redacted.txt"

    proc = _run_cli("redact", str(src), "--profile", "production", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert out.read_bytes().decode("utf-8") == "电话：[电话]\r\n备注：保留。\r\n"
    verified = _run_cli("verify", str(out), "--profile", "production", "--json", cwd=tmp_path)
    assert verified.returncode == 0, verified.stderr + verified.stdout
    report = json.loads(verified.stdout)
    assert report["status"] == "PASS"
    assert report["hits"] == []


@pytest.mark.parametrize("suffix", [".txt", ".docx"])
def test_cli_verify_fails_on_unredacted_wrapped_landline(tmp_path: Path, suffix: str) -> None:
    src = tmp_path / f"phone{suffix}"
    number = "010-\n12345678"
    if suffix == ".txt":
        src.write_bytes("电话：010-\r\n12345678".encode("utf-8"))
    else:
        doc = Document()
        doc.add_paragraph("电话：010-")
        doc.add_paragraph("12345678")
        doc.save(src)

    verified = _run_cli("verify", str(src), "--profile", "production", "--json", cwd=tmp_path)

    assert verified.returncode == 2, verified.stderr + verified.stdout
    report = json.loads(verified.stdout)
    assert report["status"] == "FAIL"
    assert [(hit["category"], hit["text"]) for hit in report["hits"]] == [("landline", number)]


def test_cli_rejects_undecodable_text(tmp_path: Path) -> None:
    src = tmp_path / "input.txt"
    src.write_bytes(b"\x81")
    out = tmp_path / "redacted.txt"

    proc = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 1, proc.stderr + proc.stdout
    assert not out.exists()


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


@pytest.mark.parametrize("output", ["./{}", "missing/../{}"])
@pytest.mark.parametrize("suffix", [".docx", ".pdf", ".txt"])
def test_cli_refuses_to_overwrite_input(tmp_path: Path, suffix: str, output: str) -> None:
    src = tmp_path / f"same{suffix}"
    if suffix == ".docx":
        docx_io.create_sample_docx(src, SAMPLE)
    elif suffix == ".pdf":
        with fitz.open() as pdf:
            pdf.new_page().insert_text((36, 50), "Contact: test@example.com")
            pdf.save(src)
    else:
        src.write_text(SAMPLE, encoding="utf-8")
    original = src.read_bytes()

    proc = _run_cli("redact", src.name, "--mode", "ai", "-o", output.format(src.name), cwd=tmp_path)

    assert proc.returncode == 1, proc.stderr + proc.stdout
    assert "输出路径不能与输入文件相同" in proc.stderr
    assert src.read_bytes() == original


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


@pytest.mark.parametrize("invoice", ["042", "042-"])
def test_cli_landline_does_not_span_docx_table_rows(tmp_path: Path, invoice: str) -> None:
    src = tmp_path / "schedule.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    rows = [("发票编号", invoice), ("2025-03-14", "开庭")]
    for row, values in zip(table.rows, rows):
        for cell, value in zip(row.cells, values):
            cell.text = value
    doc.save(src)
    out = tmp_path / "redacted.docx"

    proc = _run_cli("redact", str(src), "--profile", "production", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert [tuple(cell.text for cell in row.cells) for row in Document(out).tables[0].rows] == rows
    residual = json.loads(out.with_suffix(".residual.json").read_text(encoding="utf-8"))
    assert residual["status"] == "PASS"
    assert residual["hits"] == []


def test_cli_redacts_landline_wrapped_within_docx_paragraph(tmp_path: Path) -> None:
    src = tmp_path / "phone.docx"
    doc = Document()
    doc.add_paragraph("电话：010-\n12345678")
    doc.save(src)
    out = tmp_path / "redacted.docx"

    proc = _run_cli("redact", str(src), "--profile", "production", "-o", str(out), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert Document(out).paragraphs[0].text == "电话：[电话]\n"
    residual = json.loads(out.with_suffix(".residual.json").read_text(encoding="utf-8"))
    assert residual["status"] == "PASS"
    assert residual["hits"] == []


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
            "word/charts/chart3.xml",
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<c:chart><c:title><c:tx><c:rich><a:bodyPr/>"
            "<a:p><a:r><a:t>QQ号：</a:t></a:r></a:p>"
            "<a:p><a:r><a:t>246801357</a:t></a:r></a:p>"
            "</c:rich></c:tx></c:title></c:chart></c:chartSpace>",
            "246801357",
            "[社交账号]",
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
    ids=["chart-cache", "chart-rich-text", "chart-multiline-title", "smartart-split-runs"],
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


_NATURAL_PARTIES = ("顾南枝", "沈测青禾")
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


def test_cli_reports_unredacted_witness_within_planned_party_name(tmp_path: Path) -> None:
    src = tmp_path / "party-contract.txt"
    out = tmp_path / "redacted.txt"
    src.write_text("甲方签字：沈测青禾\n证人沈测青出庭作证。", encoding="utf-8")
    result = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8") == "甲方签字：某甲\n证人沈测青出庭作证。"
    assert "pending=1" in result.stdout
    assert "handoff:    person\t沈测青" in result.stdout
    assert "只把下面几条补进 entities.json" in result.stdout
    verified = _run_cli("verify", str(out), "--mode", "ai", "--strict", cwd=tmp_path)
    assert verified.returncode == 1, verified.stdout + verified.stderr
    assert "person: 沈测青" in verified.stdout

    entities = tmp_path / "entities.json"
    entities.write_text(
        json.dumps([{"original": "沈测青", "category": "person", "replacement": "某乙"}]),
        encoding="utf-8",
    )
    rerun = _run_cli(
        "redact", str(src), "--mode", "ai", "--entities", str(entities), "-o", str(out), cwd=tmp_path
    )
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert out.read_text(encoding="utf-8") == "甲方签字：某甲\n证人某乙出庭作证。"


def test_cli_defers_witness_name_that_is_also_clause_wording(tmp_path: Path) -> None:
    src = tmp_path / "party-contract.txt"
    out = tmp_path / "redacted.txt"
    body = "\n证人方可出庭作证。\n款到后乙方方可开票。"
    src.write_text(f"甲方签字：汤方可明{body}", encoding="utf-8")
    result = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8") == f"甲方签字：某甲{body}"
    assert "pending=1" in result.stdout
    assert "须人工核对，不要直接补入 entities.json" in result.stdout
    assert "handoff:    person\t方可" in result.stdout
    assert "只把下面几条补进" not in result.stdout


def test_cli_defers_witness_name_that_is_also_wrapped_pdf_wording(tmp_path: Path) -> None:
    src = tmp_path / "party-contract.pdf"
    out = tmp_path / "redacted.pdf"
    text = "甲方签字：慕容花开·阿青\n证人慕容花开，出庭作证。\n片名为慕容\n花开。"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), text, fontsize=12, fontname="china-s")
        doc.save(src)
    result = _run_cli("redact", str(src), "--mode", "ai", "-o", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    redacted_text = pdf_io.extract_text(out)
    assert "证人慕容花开，出庭作证。" in redacted_text
    assert "片名为慕容\n花开。" in redacted_text
    assert "handoff:    person\t慕容花开" in result.stdout
    assert "须人工核对，不要直接补入 entities.json" in result.stdout
    assert "只把下面几条补进" not in result.stdout
