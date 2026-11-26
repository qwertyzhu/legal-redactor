"""Regression tests for PII in DOCX/PDF metadata and hyperlink targets."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.cli import main
from legal_redactor.formats import docx_io
from legal_redactor.pipeline import redact_file, verify_tree

ROOT = Path(__file__).resolve().parents[1]
ALL_PROFILES = ("ai", "production", "publish", "judgment-online")


def _make_docx_with_hidden_pii(
    path: Path,
    *,
    title: str = "虚构合同 13900001111",
    subject: str = "摘要 13800002222",
    keywords: str = "联系方式 13700003333",
    http_target: str = "https://example-fictional.test/doc?phone=13600004444&token=tok999999",
    mailto_target: str = "mailto:fictional-user@example-fictional.test",
) -> Path:
    docx_io.create_sample_docx(path, "合同正文：本协议由双方依法签署，不含任何号码。")
    patched = path.with_suffix(".patched.docx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(patched, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                if "<dc:title>" in text:
                    text = text.replace("<dc:title/>", f"<dc:title>{title}</dc:title>")
                else:
                    text = text.replace(
                        "</cp:coreProperties>",
                        f"<dc:title>{title}</dc:title>"
                        f"<dc:subject>{subject}</dc:subject>"
                        f"<cp:keywords>{keywords}</cp:keywords>"
                        "</cp:coreProperties>",
                    )
                data = text.encode("utf-8")
            elif item.filename == "word/_rels/document.xml.rels":
                rel_xml = (
                    '<Relationship Id="rIdH1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    f'Target="{http_target.replace("&", "&amp;")}" TargetMode="External"/>'
                    '<Relationship Id="rIdM1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    f'Target="{mailto_target}" TargetMode="External"/>'
                ).encode("utf-8")
                data = data.replace(b"</Relationships>", rel_xml + b"</Relationships>")
            zout.writestr(item, data)
    patched.replace(path)
    return path


def _make_pdf_with_hidden_pii(
    path: Path,
    *,
    title: str = "虚构合同 13900001111",
    subject: str = "摘要 13800002222",
    keywords: str = "联系方式 13700003333",
    http_uri: str = "https://example-fictional.test/doc?phone=13600004444&token=tok999999",
    mailto_uri: str = "mailto:fictional-user@example-fictional.test",
) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Contract body without numbers.", fontsize=11)
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(72, 60, 220, 80),
            "uri": http_uri,
        }
    )
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(72, 90, 220, 110),
            "uri": mailto_uri,
        }
    )
    doc.set_metadata(
        {
            "title": title,
            "subject": subject,
            "keywords": keywords,
            "author": "FictionalLawyer",
            "creator": "TestCreator",
            "producer": "TestProducer",
        }
    )
    doc.save(str(path))
    doc.close()
    return path


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_docx_metadata_only_phone_stripped_all_modes(tmp_path: Path, profile: str):
    src = _make_docx_with_hidden_pii(tmp_path / f"src-{profile}.docx")
    out = tmp_path / f"out-{profile}.docx"
    res = redact_file(src, mode="ai", profile=profile, output_path=out)
    assert res.residual.ok
    with zipfile.ZipFile(out) as z:
        core = z.read("docProps/core.xml").decode("utf-8")
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    for secret in ("13900001111", "13800002222", "13700003333"):
        assert secret not in core, f"leaked in core.xml ({profile}): {secret}"
    for secret in ("13600004444", "tok999999", "fictional-user@"):
        assert secret not in rels, f"leaked in rels ({profile}): {secret}"
    if profile in ("production", "judgment-online"):
        assert "https://example-fictional.test/doc" in rels
    else:
        assert "example-fictional.test" not in rels


def test_docx_preserve_hyperlinks_still_scrubs_target_secrets(tmp_path: Path):
    src = _make_docx_with_hidden_pii(tmp_path / "keep.docx")
    out = tmp_path / "keep.out.docx"
    redact_file(src, mode="ai", output_path=out, preserve_hyperlinks=True)
    with zipfile.ZipFile(out) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "https://example-fictional.test/doc" in rels
    assert "13600004444" not in rels
    assert "tok999999" not in rels
    assert "mailto:" not in rels


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_pdf_metadata_and_links_stripped_all_modes(tmp_path: Path, profile: str):
    src = _make_pdf_with_hidden_pii(tmp_path / f"src-{profile}.pdf")
    out = tmp_path / f"out-{profile}.pdf"
    res = redact_file(src, mode="ai", profile=profile, output_path=out)
    assert res.residual.ok
    doc = fitz.open(str(out))
    try:
        meta_blob = " ".join(str(v) for v in (doc.metadata or {}).values())
        link_uris = [lk.get("uri", "") for p in doc for lk in p.get_links() if lk.get("uri")]
    finally:
        doc.close()
    for secret in ("13900001111", "13800002222", "13700003333"):
        assert secret not in meta_blob, f"leaked in PDF metadata ({profile}): {secret}"
    joined_uris = "\n".join(link_uris)
    for secret in ("13600004444", "tok999999", "fictional-user@"):
        assert secret not in joined_uris, f"leaked in PDF links ({profile}): {secret}"
    if profile in ("production", "judgment-online"):
        assert any("https://example-fictional.test/doc" in u for u in link_uris)
    else:
        assert not link_uris


def test_pdf_preserve_hyperlinks_keeps_clean_url_but_scrubs_secrets(tmp_path: Path):
    src = _make_pdf_with_hidden_pii(
        tmp_path / "keep.pdf",
        http_uri="https://example-fictional.test/doc?token=tok999999&phone=13600004444&page=2",
    )
    out = tmp_path / "keep.out.pdf"
    redact_file(src, mode="ai", output_path=out, preserve_hyperlinks=True)
    doc = fitz.open(str(out))
    try:
        link_uris = [lk.get("uri", "") for p in doc for lk in p.get_links() if lk.get("uri")]
    finally:
        doc.close()
    assert any("https://example-fictional.test/doc?token=[参数]" in u and "&page=2" in u for u in link_uris)
    joined = "\n".join(link_uris)
    assert "13600004444" not in joined
    assert "tok999999" not in joined
    assert "mailto:" not in joined


def test_verify_catches_docx_metadata_and_link_pii(tmp_path: Path):
    meta_only = _make_docx_with_hidden_pii(
        tmp_path / "meta_only.docx",
        http_target="https://example-fictional.test/clean",
        mailto_target="https://example-fictional.test/clean2",
    )
    assert main(["verify", str(meta_only), "--mode", "ai", "--strict"]) == 2

    link_only = _make_docx_with_hidden_pii(
        tmp_path / "link_only.docx",
        title="干净标题",
        subject="",
        keywords="",
    )
    assert main(["verify", str(link_only), "--mode", "ai", "--strict"]) == 2

    tree_report = verify_tree(tmp_path, mode="ai")
    assert tree_report["status"] == "FAIL"
    assert tree_report["failed"] == 2


def test_verify_catches_pdf_metadata_and_link_pii(tmp_path: Path):
    meta_only = _make_pdf_with_hidden_pii(
        tmp_path / "meta_only.pdf",
        http_uri="https://example-fictional.test/clean",
        mailto_uri="https://example-fictional.test/clean2",
    )
    assert main(["verify", str(meta_only), "--mode", "ai", "--strict"]) == 2

    link_only = _make_pdf_with_hidden_pii(
        tmp_path / "link_only.pdf",
        title="Clean Title",
        subject="",
        keywords="",
    )
    assert main(["verify", str(link_only), "--mode", "ai", "--strict"]) == 2

    tree_report = verify_tree(tmp_path, mode="ai")
    assert tree_report["status"] == "FAIL"
    assert tree_report["failed"] == 2


def test_standalone_html_scrubs_docx_rels_and_metadata():
    docs_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    root_html = (ROOT / "legal-redactor.html").read_text(encoding="utf-8")
    assert docs_html == root_html
    assert "word\\/_rels\\/" in docs_html
    assert "TargetMode" in docs_html
    assert "docProps/custom.xml" in docs_html
