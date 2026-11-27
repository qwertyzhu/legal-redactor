"""Regression tests for PII in DOCX/PDF metadata and hyperlink targets."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.cli import main
from legal_redactor.formats import docx_io, pdf_io
from legal_redactor.ledger_store import write_ledger_payload
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


def _make_tagged_pdf(
    path: Path, *, body: str, alt: str, actual_text: str, subject: str = "",
) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), body, fontsize=11)
    root, figure, span = (doc.get_new_xref() for _ in range(3))
    doc.update_object(root, f"<< /Type /StructTreeRoot /K [ {figure} 0 R {span} 0 R ] >>")
    doc.update_object(
        figure,
        f"<< /Type /StructElem /S /Figure /P {root} 0 R /Pg {page.xref} 0 R /K 0 "
        f"/Alt {fitz.get_pdf_str(alt)} /T {fitz.get_pdf_str(alt)} >>",
    )
    doc.update_object(
        span,
        f"<< /S /Span /P {root} 0 R /Pg {page.xref} 0 R /K 1 "
        f"/ActualText {fitz.get_pdf_str(actual_text)} /E {fitz.get_pdf_str(actual_text)} >>",
    )
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
    if subject:
        doc.set_metadata({"subject": subject})
    doc.save(str(path))
    doc.close()
    return path


def _structure_strings(path: Path) -> list[str]:
    with fitz.open(str(path)) as doc:
        return [
            value
            for xref in range(1, doc.xref_length())
            for key in ("Alt", "ActualText", "E", "T")
            for kind, value in [doc.xref_get_key(xref, key)]
            if kind == "string"
        ]


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


def test_percent_encoded_phone_in_preserved_links_scrubbed_and_verified(tmp_path: Path):
    encoded_url = "https://example-fictional.test/a%2Fb/doc?phone=136%200000%204444"
    src_docx = _make_docx_with_hidden_pii(tmp_path / "enc.docx", title="", subject="", keywords="", http_target=encoded_url, mailto_target="https://example-fictional.test/ok")
    src_pdf = _make_pdf_with_hidden_pii(tmp_path / "enc.pdf", title="", subject="", keywords="", http_uri=encoded_url, mailto_uri="https://example-fictional.test/ok")

    assert main(["verify", str(src_docx), "--mode", "production", "--strict"]) == 2
    assert main(["verify", str(src_pdf), "--mode", "production", "--strict"]) == 2

    out_docx = tmp_path / "enc.out.docx"
    out_pdf = tmp_path / "enc.out.pdf"
    assert redact_file(src_docx, mode="production", output_path=out_docx).residual.ok
    assert redact_file(src_pdf, mode="production", output_path=out_pdf).residual.ok

    with zipfile.ZipFile(out_docx) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "136%200000%204444" not in rels and "136 0000 4444" not in rels
    assert "https://example-fictional.test/a%2Fb/doc" in rels

    doc = fitz.open(str(out_pdf))
    try:
        uris = "\n".join(lk.get("uri", "") for p in doc for lk in p.get_links() if lk.get("uri"))
    finally:
        doc.close()
    assert "136%200000%204444" not in uris and "136 0000 4444" not in uris
    assert "https://example-fictional.test/a%2Fb/doc" in uris


def test_docx_hyperlink_field_instr_verified_and_scrubbed(tmp_path: Path):
    base = tmp_path / "field_base.docx"
    docx_io.create_sample_docx(base, "干净显示文本")
    src = tmp_path / "field_link.docx"
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(src, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                field_xml = (
                    b'<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                    b'<w:r><w:instrText xml:space="preserve"> HYPERLINK "https://example-fictional.test/f?phone=13900005555" </w:instrText></w:r>'
                    b'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
                )
                data = data.replace(b"</w:p>", field_xml, 1)
            zout.writestr(item, data)

    assert main(["verify", str(src), "--mode", "ai", "--strict"]) == 2

    out_ai = tmp_path / "field_ai.docx"
    assert redact_file(src, mode="ai", output_path=out_ai).residual.ok
    with zipfile.ZipFile(out_ai) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "13900005555" not in doc_xml
    assert "example-fictional.test" not in doc_xml

    out_prod = tmp_path / "field_prod.docx"
    assert redact_file(src, mode="production", output_path=out_prod).residual.ok
    with zipfile.ZipFile(out_prod) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "13900005555" not in doc_xml
    assert "https://example-fictional.test/f" in doc_xml


def test_restore_file_preserves_pdf_and_docx_hyperlinks(tmp_path: Path):
    from legal_redactor.ledger_store import restore_file

    clean_url = "https://example-fictional.test/evidence/1"
    src_pdf = tmp_path / "src.pdf"
    _make_pdf_with_hidden_pii(src_pdf, http_uri=clean_url)
    red_pdf = tmp_path / "red.pdf"
    res_pdf = redact_file(src_pdf, mode="production", output_path=red_pdf)
    restored_pdf = tmp_path / "restored.pdf"
    restore_file(red_pdf, res_pdf.ledger_path, restored_pdf)
    doc = fitz.open(restored_pdf)
    try:
        uris = [lnk.get("uri") or "" for page in doc for lnk in page.get_links()]
    finally:
        doc.close()
    assert any(clean_url in u for u in uris)

    src_docx = tmp_path / "src.docx"
    _make_docx_with_hidden_pii(src_docx, http_target=clean_url)
    red_docx = tmp_path / "red.docx"
    res_docx = redact_file(src_docx, mode="production", output_path=red_docx)
    restored_docx = tmp_path / "restored.docx"
    restore_file(red_docx, res_docx.ledger_path, restored_docx)
    with zipfile.ZipFile(restored_docx) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert clean_url in rels


def test_verify_does_not_bridge_digits_across_body_and_metadata(tmp_path: Path):
    src = tmp_path / "split_digits.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Item 138", fontsize=11)
    doc.set_metadata({"title": "00001111", "subject": "010", "keywords": "12345678"})
    doc.save(src)
    doc.close()

    assert main(["verify", str(src), "--mode", "ai", "--strict"]) == 0


def test_production_extra_url_category_strips_http_links(tmp_path: Path):
    clean_url = "https://example-fictional.test/evidence/2"
    src_docx = tmp_path / "url_optin.docx"
    _make_docx_with_hidden_pii(src_docx, http_target=clean_url)
    out_docx = tmp_path / "url_optin.redacted.docx"
    res_docx = redact_file(
        src_docx, mode="production", extra_categories={"url"}, output_path=out_docx
    )
    assert res_docx.residual.ok, res_docx.residual.hits
    with zipfile.ZipFile(out_docx) as z:
        assert clean_url not in z.read("word/_rels/document.xml.rels").decode("utf-8")

    src_pdf = tmp_path / "url_optin.pdf"
    _make_pdf_with_hidden_pii(src_pdf, http_uri=clean_url)
    out_pdf = tmp_path / "url_optin.redacted.pdf"
    res_pdf = redact_file(
        src_pdf, mode="production", extra_categories={"url"}, output_path=out_pdf
    )
    assert res_pdf.residual.ok, res_pdf.residual.hits
    doc = fitz.open(out_pdf)
    try:
        uris = [lnk.get("uri") or "" for page in doc for lnk in page.get_links()]
    finally:
        doc.close()
    assert not any(clean_url in u for u in uris)


def test_production_extra_url_preserves_external_linked_image_rel(tmp_path: Path):
    base = tmp_path / "img_base.docx"
    docx_io.create_sample_docx(base, "正文不含号码")
    src = tmp_path / "linked_img.docx"
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(src, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                rel_xml = (
                    b'<Relationship Id="rIdImg1" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                    b'Target="file:///local/exhibit.png" TargetMode="External"/>'
                    b'<Relationship Id="rIdLink1" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="https://example-fictional.test/evidence/3" TargetMode="External"/>'
                )
                data = data.replace(b"</Relationships>", rel_xml + b"</Relationships>")
            zout.writestr(item, data)

    out = tmp_path / "linked_img.redacted.docx"
    res = redact_file(src, mode="production", extra_categories={"url"}, output_path=out)
    assert res.residual.ok, res.residual.hits
    with zipfile.ZipFile(out) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "file:///local/exhibit.png" in rels
    assert "https://example-fictional.test/evidence/3" not in rels


def test_credential_bearing_url_with_escaped_path_does_not_crash(tmp_path: Path):
    cred_url = "https://user:13900001111@example-fictional.test/a%2Fb/doc"
    src_docx = _make_docx_with_hidden_pii(
        tmp_path / "cred.docx", title="", subject="", keywords="", http_target=cred_url
    )
    out_docx = tmp_path / "cred.out.docx"
    res = redact_file(src_docx, mode="production", output_path=out_docx)
    assert res.residual.ok, res.residual.hits
    with zipfile.ZipFile(out_docx) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "13900001111" not in rels
    assert "/a%2Fb/doc" in rels


def test_percent_encoded_query_name_token_scrubbed(tmp_path: Path):
    encoded_token_url = (
        "https://example-fictional.test/a%2Fb/doc"
        "?%70assword=sec%23rest&note=136%200000%204444%23tail&keep=x%2Fy"
    )
    src_docx = _make_docx_with_hidden_pii(
        tmp_path / "enctok.docx", title="", subject="", keywords="", http_target=encoded_token_url
    )
    out_docx = tmp_path / "enctok.out.docx"
    res = redact_file(src_docx, mode="production", output_path=out_docx)
    assert res.residual.ok, res.residual.hits
    with zipfile.ZipFile(out_docx) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "sec" not in rels and "rest" not in rels
    assert "136%200000%204444" not in rels and "136 0000 4444" not in rels
    assert "%23tail&amp;keep=x%2Fy" in rels
    assert "/a%2Fb/doc" in rels


def test_relative_link_first_segment_encoded_phone_scrubbed(tmp_path: Path):
    rel_url = "136%200000%204444/exhibit.pdf"
    src_docx = _make_docx_with_hidden_pii(
        tmp_path / "rel.docx", title="", subject="", keywords="", http_target=rel_url
    )
    out_docx = tmp_path / "rel.out.docx"
    res = redact_file(src_docx, mode="production", output_path=out_docx)
    assert res.residual.ok, res.residual.hits
    with zipfile.ZipFile(out_docx) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "136%200000%204444" not in rels and "136 0000 4444" not in rels
    assert "/exhibit.pdf" in rels


def test_standalone_html_scrubs_docx_rels_and_metadata():
    docs_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    root_html = (ROOT / "legal-redactor.html").read_text(encoding="utf-8")
    assert docs_html == root_html
    assert "word\\/_rels\\/" in docs_html
    assert "TargetMode" in docs_html
    assert "docProps/custom.xml" in docs_html


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_pdf_structure_tree_text_scrubbed_all_modes(tmp_path: Path, profile: str):
    src = _make_tagged_pdf(
        tmp_path / f"tagged-{profile}.pdf",
        body="Contact phone 13900001111",
        alt="Bank transfer receipt for caller 13900001111",
        actual_text="Caller 13800002222",
    )
    out = tmp_path / f"tagged-{profile}.out.pdf"
    res = redact_file(src, mode="ai", profile=profile, output_path=out)
    assert res.residual.ok
    strings = _structure_strings(out)
    assert len(strings) == 4
    joined = "\n".join(strings)
    assert "13900001111" not in joined and "13800002222" not in joined
    assert "Bank transfer receipt for caller [手机号]" in strings


def test_pdf_hidden_phone_does_not_leave_partially_scrubbed_email(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Transfer receipt",
        alt="Phone 13800002222; Email a13800002222@x.com", actual_text="Receipt",
    )
    out = tmp_path / "redacted.pdf"
    assert redact_file(src, mode="ai", output_path=out).residual.ok
    assert "Phone [PDF手机号1]; Email [邮箱]" in _structure_strings(out)


def test_pdf_hidden_landlines_with_shared_prefix_round_trip(tmp_path: Path):
    alt = "Phone 010-1234567; Phone 010-1234567 8"
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Transfer receipt", alt=alt, actual_text="Receipt",
    )
    redacted, restored = (tmp_path / name for name in ("redacted.pdf", "restored.pdf"))
    ledger = redact_file(src, mode="ai", output_path=redacted, ledger=tmp_path / "ledger.json").ledger_path
    assert "Phone [PDF电话1]; Phone [PDF电话2]" in _structure_strings(redacted)
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 0
    assert alt in _structure_strings(restored)


def test_pdf_hidden_phone_and_wechat_id_with_same_digits(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Contact phone 13900001111",
        alt="微信 13800002222 电话 13800002222", actual_text="Receipt",
    )
    redacted, restored = (tmp_path / name for name in ("redacted.pdf", "restored.pdf"))
    ledger = redact_file(src, mode="ai", output_path=redacted, ledger=tmp_path / "ledger.json").ledger_path
    assert "微信 [社交账号] 电话 [PDF手机号1]" in _structure_strings(redacted)
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 0
    assert "微信 [社交账号] 电话 13800002222" in _structure_strings(restored)


def test_pdf_hidden_mobile_masks_its_international_copy(tmp_path: Path):
    alt = "Phone 13800002222; International +8613800002222"
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Contact phone 13900001111",
        alt=alt, actual_text="Receipt",
    )
    redacted, restored = (tmp_path / name for name in ("redacted.pdf", "restored.pdf"))
    ledger = redact_file(src, mode="ai", output_path=redacted, ledger=tmp_path / "ledger.json").ledger_path
    assert "Phone [PDF手机号1]; International +86[PDF手机号1]" in _structure_strings(redacted)
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 0
    assert alt in _structure_strings(restored)


def test_pdf_structure_tree_masks_unlabeled_copy_of_share_code(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Transfer receipt",
        alt="链接 https://pan.baidu.com/s/xxx 提取码 ab12 ... ab12", actual_text="Receipt",
    )
    redacted = tmp_path / "redacted.pdf"
    assert redact_file(src, mode="ai", output_path=redacted).residual.ok
    assert "链接 https://pan.baidu.com/s/xxx 提取码 [提取码] ... [提取码]" in _structure_strings(redacted)


def test_verify_catches_pdf_structure_tree_pii(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "alt_only.pdf",
        body="Exhibit 1. Transfer receipt.",
        alt="Bank transfer receipt for caller 13900001111",
        actual_text="Transfer receipt",
    )
    assert main(["verify", str(src), "--mode", "ai", "--strict"]) == 2


def test_pdf_structure_tree_text_honors_keep_categories(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "keep.pdf",
        body="Contact phone 13900001111",
        alt="Bank transfer receipt for caller 13900001111",
        actual_text="Transfer receipt",
    )
    out = tmp_path / "keep.out.pdf"
    redact_file(src, mode="ai", output_path=out, keep_categories={"mobile"})
    assert "Bank transfer receipt for caller 13900001111" in _structure_strings(out)


def test_restore_file_restores_pdf_structure_tree_text(tmp_path: Path):
    from legal_redactor.ledger_store import restore_file

    alt = "Bank transfer receipt for caller 13900001111"
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Contact phone 13900001111", alt=alt, actual_text="Transfer receipt"
    )
    red = tmp_path / "red.pdf"
    res = redact_file(src, mode="ai", output_path=red)
    restored = tmp_path / "restored.pdf"
    restore_file(red, res.ledger_path, restored)
    assert alt in _structure_strings(restored)


def test_pdf_round_trip_restores_distinct_hidden_phones(tmp_path: Path):
    body, alt, actual, subject = (
        "Contact phone 13900001111", "Caller 13800002222",
        "Caller 13700003333", "Subject phone 13600004444",
    )
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body=body, alt=alt, actual_text=actual, subject=subject,
    )
    redacted, restored, ledger = (tmp_path / name for name in ("redacted.pdf", "restored.pdf", "ledger.json"))
    assert main(["redact", str(src), "--mode", "ai", "-o", str(redacted), "--ledger", str(ledger)]) == 0
    assert main(["verify", str(redacted), "--mode", "ai", "--strict"]) == 0
    with fitz.open(redacted) as doc:
        assert "[手机号]" in doc[0].get_text()
        assert doc.metadata["subject"] == ""
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 0
    with fitz.open(restored) as doc:
        assert body.split()[-1] in doc[0].get_text()
        assert {key: value for _, key, value in pdf_io._struct_text_fields(doc)} == {
            "Alt": alt, "T": alt, "ActualText": actual, "E": actual,
        }
        assert doc.metadata["subject"] == subject

    redacted.write_bytes(redacted.read_bytes() + b"\n")
    mismatch = tmp_path / "mismatch.pdf"
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(mismatch)]) == 1
    assert not mismatch.exists()


def test_pdf_restore_rejects_legacy_ambiguous_hidden_phone(tmp_path: Path, capsys):
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Contact phone 13900001111",
        alt="Caller 13800002222", actual_text="Transfer receipt",
    )
    redacted, restored, ledger = (tmp_path / name for name in ("redacted.pdf", "restored.pdf", "ledger.json"))
    pdf_io.redact_pdf(src, redacted, [("13900001111", "[手机号]")], image_categories={"mobile"})
    write_ledger_payload(ledger, {"entities": [
        {"original": "13900001111", "replacement": "[手机号]", "category": "mobile"},
    ]})
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 1
    assert "账本缺少 PDF 结构树电话号码的对应记录" in capsys.readouterr().err
    assert not restored.exists()


def test_pdf_restore_rejects_ledger_from_document_without_hidden_phones(tmp_path: Path, capsys):
    src = _make_tagged_pdf(
        tmp_path / "src.pdf", body="Contact phone 13900001111",
        alt="Caller 13800002222", actual_text="Example token [PDF手机号1]",
    )
    other = _make_tagged_pdf(
        tmp_path / "other.pdf", body="Contact phone 13700003333",
        alt="Other receipt", actual_text="Transfer receipt",
    )
    redacted, restored = (tmp_path / name for name in ("redacted.pdf", "restored.pdf"))
    ledger = redact_file(src, mode="ai", output_path=redacted, ledger=tmp_path / "src.ledger.json").ledger_path
    other_ledger = redact_file(
        other, mode="ai", output_path=tmp_path / "other.redacted.pdf", ledger=tmp_path / "other.ledger.json",
    ).ledger_path
    assert main(["restore", str(redacted), "--ledger", str(other_ledger), "-o", str(restored)]) == 1
    assert "账本缺少 PDF 结构树电话号码的对应记录" in capsys.readouterr().err
    assert not restored.exists()
    assert main(["restore", str(redacted), "--ledger", str(ledger), "-o", str(restored)]) == 0
    assert "Caller 13800002222" in _structure_strings(restored)
    assert "Example token [PDF手机号1]" in _structure_strings(restored)


def test_verify_reads_pdf_structure_tree_with_sparse_xref(tmp_path: Path):
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 4 0 R >>",
        2: "<< /Type /Pages /Kids [ 3 0 R ] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 200 200 ] >>",
        4: "<< /Type /StructTreeRoot /K 10 0 R >>",
        10: "<< /S /Figure /P 4 0 R /Alt (Receipt for caller 13900001111) >>",
    }
    body, offsets = b"%PDF-1.7\n", {}
    for num, obj in objects.items():
        offsets[num] = len(body)
        body += f"{num} 0 obj\n{obj}\nendobj\n".encode()
    xref = (
        "xref\n0 5\n0000000000 65535 f \n"
        + "".join(f"{offsets[num]:010d} 00000 n \n" for num in range(1, 5))
        + f"10 1\n{offsets[10]:010d} 00000 n \n"
    )
    trailer = f"trailer\n<< /Size 11 /Root 1 0 R >>\nstartxref\n{len(body)}\n%%EOF\n"
    src = tmp_path / "sparse.pdf"
    src.write_bytes(body + xref.encode() + trailer.encode())
    assert main(["verify", str(src), "--mode", "ai", "--strict"]) == 2


def test_pdf_structure_tree_text_under_indirect_kids_array(tmp_path: Path):
    src = _make_tagged_pdf(
        tmp_path / "indirect.pdf",
        body="Contact phone 13900001111",
        alt="Bank transfer receipt for caller 13900001111",
        actual_text="Transfer receipt",
    )
    with fitz.open(str(src)) as doc:
        root = int(doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[1].split()[0])
        kids = doc.get_new_xref()
        doc.update_object(kids, doc.xref_get_key(root, "K")[1])
        doc.xref_set_key(root, "K", f"{kids} 0 R")
        doc.save(str(tmp_path / "indirect-kids.pdf"))
    out = tmp_path / "indirect.out.pdf"
    redact_file(tmp_path / "indirect-kids.pdf", mode="ai", output_path=out)
    assert "Bank transfer receipt for caller [手机号]" in _structure_strings(out)
    assert main(["verify", str(tmp_path / "indirect-kids.pdf"), "--mode", "ai", "--strict"]) == 2
