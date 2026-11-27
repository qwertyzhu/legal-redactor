"""Regression tests for v0.19.0 security, mode-boundary, and legal-entity fixes."""

from __future__ import annotations

import io
import json
import re
import subprocess
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pymupdf as fitz
import pytest

from legal_redactor.cli import main
from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.formats import docx_io, pdf_io
from legal_redactor.patterns import detect_structural
from legal_redactor.pipeline import redact_file, redact_tree
from legal_redactor.suspects import detect_suspects
from legal_redactor.web import WebRequestHandler, _find_available_port

ROOT = Path(__file__).resolve().parents[1]


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[str, bytes]:
    boundary = "----LRTestBoundary019"
    chunks: list[bytes] = []
    for k, v in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    for k, (fname, content, ctype) in files.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


# --- P0-1: Standalone HTML safety & sync -----------------------------------


def test_standalone_html_files_are_synced_and_rasterize_pdf():
    docs_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    root_html = (ROOT / "legal-redactor.html").read_text(encoding="utf-8")
    assert docs_html == root_html, "docs/index.html and legal-redactor.html must stay identical"

    # Must NOT load original PDF bytes into pdf-lib and merely draw white rectangles over live text streams
    assert "PDFLib.PDFDocument.load(arrayBuffer)" not in docs_html
    assert "PDFLib.PDFDocument.create()" in docs_html
    assert "embedJpg" in docs_html or "embedPng" in docs_html

    # DOCX in browser must parse XML DOM across runs rather than naive docXml.split(escapedOrig)
    assert "DOMParser" in docs_html
    assert "docXml.split(escapedOrig)" not in docs_html


def test_standalone_audit_download_discloses_unredacted_original_text():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    button = re.search(r'<button\b[^>]*\bid="btnDownloadAudit"[^>]*>([^<]+)</button>', html)
    assert button is not None
    assert "未脱敏原文" in button[1]
    hint_id = re.search(r'\baria-describedby="([^"]+)"', button[0])
    assert hint_id is not None
    hint = re.search(rf'<p\b[^>]*\bid="{re.escape(hint_id[1])}"[^>]*>(.*?)</p>', html, re.DOTALL)
    assert hint is not None
    assert "未脱敏原文" in hint[1]


# --- P0-2: Batch --unify mode boundaries -----------------------------------


def test_batch_unify_production_preserves_case_judge_url_and_order_no(tmp_path: Path):
    src = tmp_path / "case"
    out = tmp_path / "out"
    src.mkdir()
    (src / "entities.json").write_text('{"entities": []}\n', encoding="utf-8")
    (src / "a.txt").write_text(
        "案号：（2024）京0491民初1234号。原告：北京星澜影业有限公司。\n"
        "审判长：王测国。证据链接：https://example-fictional.test/v/123\n"
        "订单编号：12345678901234567。联系电话：13900001111。\n",
        encoding="utf-8",
    )
    batch = redact_tree(src, mode="production", output_dir=out, unify_first=True, auto_confident=True)
    assert batch.ok
    redacted = (out / "a.redacted-production.txt").read_text(encoding="utf-8")
    assert "（2024）京0491民初1234号" in redacted
    assert "北京星澜影业有限公司" in redacted
    assert "王测国" in redacted
    assert "https://example-fictional.test/v/123" in redacted
    assert "12345678901234567" in redacted
    assert "13900001111" not in redacted


def test_batch_unify_ai_links_abbreviations_across_files_and_keeps_evidence_url(tmp_path: Path):
    src = tmp_path / "case"
    out = tmp_path / "out"
    src.mkdir()
    (src / "a.md").write_text(
        "原告：北京星澜影业有限公司（以下简称“星澜影业”），证据：https://example-fictional.test/item/9。\n",
        encoding="utf-8",
    )
    (src / "b.md").write_text(
        "经查，星澜影业享有独占信息网络传播权。\n",
        encoding="utf-8",
    )
    batch = redact_tree(src, mode="ai", output_dir=out, unify_first=True, auto_confident=True)
    assert batch.ok
    a = (out / "a.redacted-ai.md").read_text(encoding="utf-8")
    b = (out / "b.redacted-ai.md").read_text(encoding="utf-8")
    assert "星澜影业" not in a and "星澜影业" not in b
    assert "https://example-fictional.test/item/9" in a
    entities = json.loads((src / ".redactor" / "entities.consistent.json").read_text(encoding="utf-8"))["entities"]
    by_orig = {e["original"]: e["replacement"] for e in entities if e.get("replacement")}
    assert by_orig["北京星澜影业有限公司"] == by_orig["星澜影业"]


def test_batch_unify_links_company_short_name_first_detected_as_person(tmp_path: Path):
    src = tmp_path / "case"
    out = tmp_path / "out"
    src.mkdir()
    (src / "a.txt").write_text("原告：王仿乙。\n", encoding="utf-8")
    (src / "b.txt").write_text(
        "原告：北京星澜影业有限公司（以下简称“王仿乙”）。\n", encoding="utf-8"
    )

    batch = redact_tree(src, mode="ai", output_dir=out, unify_first=True)
    assert batch.ok
    assert (out / "a.redacted-ai.txt").read_text(encoding="utf-8") == "原告：某单位A。\n"
    assert (out / "b.redacted-ai.txt").read_text(encoding="utf-8") == (
        "原告：某单位A（以下简称“某单位A”）。\n"
    )


# --- P0-3: PDF overlap & Sandwich PDF protection ---------------------------


def test_pdf_substring_mapping_does_not_double_paint(tmp_path: Path):
    p_in = tmp_path / "in.pdf"
    p_out = tmp_path / "out.pdf"
    pdf_io.create_sample_pdf(p_in, "原告：北京星澜网络科技有限公司，关联方：星澜网络科技有限公司。")
    txt = pdf_io.redact_pdf(
        p_in,
        p_out,
        [("北京星澜网络科技有限公司", "某单位A"), ("星澜网络科技有限公司", "某单位B")],
    )
    assert txt.count("某单位A") == 1
    assert txt.count("某单位B") == 1


def test_pdf_skips_off_page_phone_searches_but_redacts_wrapped_phone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    src = tmp_path / "phones.pdf"
    out = tmp_path / "redacted.pdf"
    first, second = "13800001234", "13900001235"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), first)
        doc.new_page().insert_text((72, 72), f"{second[:5]}\n{second[5:]}")
        doc.save(src)

    calls = []
    search_for = fitz.Page.search_for

    def trace_search(page, original, *args, **kwargs):
        calls.append((page.number, original))
        return search_for(page, original, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "search_for", trace_search)
    pdf_io.redact_pdf(src, out, [(first, "A"), (second, "B")])

    assert calls == [(0, first), (1, second)]
    with fitz.open(out) as doc:
        assert doc[0].get_text().strip() == "A"
        assert doc[1].get_text().strip() == "B"


def test_sandwich_pdf_refused_in_ai_mode_and_warned_in_production(tmp_path: Path):
    # Create a sandwich PDF: full-page raster image + hidden/overlay text layer
    raw_doc = fitz.open()
    rp = raw_doc.new_page(width=400, height=400)
    rp.insert_text((40, 100), "Party Secret Name 13900001111", fontsize=14)
    pix = rp.get_pixmap(alpha=False)
    raw_doc.close()

    sandwich = tmp_path / "sandwich.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text((40, 100), "Party Secret Name 13900001111", fontsize=14, render_mode=3)
    doc.save(sandwich)
    doc.close()

    with pytest.raises(ValueError, match="双层|扫描"):
        redact_file(sandwich, mode="ai", output_path=tmp_path / "ai.pdf", auto_confident=True)

    prod_res = redact_file(
        sandwich,
        mode="production",
        output_path=tmp_path / "prod.pdf",
        ocr_images="off",
    )
    assert any("扫描" in w or "embedded images" in w for w in prod_res.warnings)


# --- P0-4: Local Web Server fixes ------------------------------------------


@pytest.fixture(scope="module")
def live_web():
    port = _find_available_port(8910, max_attempts=20)
    server = ThreadingHTTPServer(("127.0.0.1", port), WebRequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("prof_name", ["publish", "judgment-online", "minimal"])
def test_web_supports_all_named_profiles_via_mode_field(live_web: str, prof_name: str):
    payload = json.dumps({
        "text": "原告张测三，电话13900001111，住北京市朝阳区测试路88号。",
        "mode": prof_name,
        "auto_confident": True,
    }).encode("utf-8")
    req = Request(f"{live_web}/api/redact-text", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert "13900001111" not in data["redacted_text"]


def test_web_html_checks_redact_court_staff_by_default(live_web: str):
    with urlopen(Request(f"{live_web}/")) as resp:
        html = resp.read().decode("utf-8")
    assert 'id="fileRedactCourtStaff" checked' in html


def test_web_rejects_cross_origin_and_dns_rebinding(live_web: str):
    payload = json.dumps({"text": "手机13900001111", "mode": "ai"}).encode("utf-8")
    req_csrf = Request(
        f"{live_web}/api/redact-text",
        data=payload,
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    with pytest.raises(HTTPError) as exc_info:
        urlopen(req_csrf)
    assert exc_info.value.code == 403

    req_rebind = Request(
        f"{live_web}/api/doctor",
        headers={"Host": "rebind.evil.example:8910"},
    )
    with pytest.raises(HTTPError) as exc_info2:
        urlopen(req_rebind)
    assert exc_info2.value.code == 403


def test_web_refuses_scanned_visual_coverup_for_ai_mode(live_web: str):
    doc = fitz.open()
    p = doc.new_page(width=200, height=100)
    p.draw_rect(p.rect, color=(0, 0, 0))
    scan_bytes = doc.tobytes()
    doc.close()

    ctype, body = _multipart(
        {"mode": "ai", "is_scanned_mode": "true"},
        {"file": ("scan.pdf", scan_bytes, "application/pdf")},
    )
    req = Request(f"{live_web}/api/redact-file", data=body, headers={"Content-Type": ctype})
    with pytest.raises(HTTPError) as exc_info:
        urlopen(req)
    assert exc_info.value.code == 400


# --- P1: Surnames, Orgs, Plates, Case/Notary numbers, Abbreviations --------


def test_modern_simplified_surnames_and_ethnic_dot_names_redacted():
    text = (
        "原告：肖测国，法定代表人：付测强，联系人：闫测磊，被告：代测军，"
        "证人岳测鹏出庭作证，姓名：覃测华，委托诉讼代理人：买买提·阿不都测。"
    )
    plan = build_plan(text, mode="ai", auto_confident=True)
    out = apply_mapping_to_text(text, plan.mapping())
    for name in ("肖测国", "付测强", "闫测磊", "代测军", "岳测鹏", "覃测华", "买买提·阿不都测"):
        assert name not in out, f"missed person name: {name}"


def test_partnerships_studios_and_individual_businesses_redacted():
    text = (
        "原告：霍尔果斯星澜影视文化工作室（有限合伙），"
        "第三人：杭州朗岳文化传媒合伙企业（有限合伙），"
        "被告：义乌市云帆日用品商行、广州市白云区北测服饰经营部。"
    )
    plan = build_plan(text, mode="ai", auto_confident=True)
    out = apply_mapping_to_text(text, plan.mapping())
    for org in (
        "霍尔果斯星澜影视文化工作室（有限合伙）",
        "杭州朗岳文化传媒合伙企业（有限合伙）",
        "义乌市云帆日用品商行",
        "广州市白云区北测服饰经营部",
    ):
        assert org not in out, f"missed organization: {org}"


def test_new_energy_8_char_license_plates_detected():
    text = "涉案车辆车牌为京AD12345、沪AF88888、粤B·D12345及传统蓝牌浙A12345。"
    hits = {h.text for h in detect_structural(text) if h.category == "plate"}
    assert {"京AD12345", "沪AF88888", "粤B·D12345", "浙A12345"} <= hits


def test_notary_and_extended_case_numbers_and_distinct_placeholders():
    text = (
        "公证书：（2024）京方圆内经证字第12345号、（2024）京东方内民证字第05678号。\n"
        "前案：（2015）朝民（知）初字第12345号、〔2023〕京0105民初999号。\n"
        "本案：（2024）京0491民初1234号。仲裁：（2024）京仲案字第0888号。\n"
    )
    ai_out = apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())
    for secret in ("12345", "05678", "999", "1234", "0888", "方圆", "东方"):
        assert secret not in ai_out, f"leaked in ai mode: {secret} -> {ai_out}"
    # Distinct case numbers get distinct placeholders so AI can tell prior case from current case
    assert "（20XX）X民初X号" in ai_out
    assert "（20XX）X民初X2号" in ai_out

    # In production mode, court case numbers and notary certificate numbers stay intact as evidence
    prod_out = apply_mapping_to_text(text, build_plan(text, mode="production", auto_confident=True).mapping())
    assert "（2024）京方圆内经证字第12345号" in prod_out
    assert "（2024）京东方内民证字第05678号" in prod_out
    assert "（2024）京0491民初1234号" in prod_out


def test_wrapped_and_compound_abbreviations_link_to_full_org():
    text = (
        "原告：西安云海数字娱乐发行股份有限公司（以下\n简称“云海公司”），"
        "与被告：北京星澜影业有限公司（以下简称“乙方”或“星澜影业”）签订合同。"
        "后云海公司向星澜影业支付许可费。"
    )
    plan = build_plan(text, mode="ai", auto_confident=True)
    by_orig = {e.original: e.replacement for e in plan.entities}
    assert by_orig.get("云海公司") == by_orig.get("西安云海数字娱乐发行股份有限公司")
    assert by_orig.get("星澜影业") == by_orig.get("北京星澜影业有限公司")
    out = apply_mapping_to_text(text, plan.mapping())
    assert "云海公司" not in out and "星澜影业" not in out


# --- P2: DOCX production links, single-file verify revisions, pre-commit ---


def test_docx_production_keeps_http_links_but_scrubs_mailto_and_url_secrets(tmp_path: Path):
    src = tmp_path / "links.docx"
    docx_io.create_sample_docx(src, "证据链接")
    patched = tmp_path / "links2.docx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(patched, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="https://example-fictional.test/v?token=secret9999" TargetMode="External"/>'
                    b'<Relationship Id="rIdM" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="mailto:haoceyi@example-fictional.test" TargetMode="External"/></Relationships>',
                )
            zout.writestr(item, data)
    res = redact_file(patched, mode="production", output_path=tmp_path / "prod.docx")
    with zipfile.ZipFile(res.output_path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "https://example-fictional.test/v" in rels
    assert "secret9999" not in rels
    assert "mailto:" not in rels


def test_single_file_verify_scans_deleted_track_changes(tmp_path: Path):
    # Craft a docx containing an unredacted phone inside <w:del><w:delText>
    base = tmp_path / "base.docx"
    docx_io.create_sample_docx(base, "正文干净无敏感词。")
    dirty = tmp_path / "dirty_del.docx"
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(dirty, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = data.replace(
                    b"</w:p>",
                    b'<w:del w:id="1" w:author="A"><w:r><w:delText>13900001111</w:delText></w:r></w:del></w:p>',
                    1,
                )
            zout.writestr(item, data)
    rc = main(["verify", str(dirty), "--mode", "ai"])
    assert rc == 2


def test_pre_commit_hook_has_no_pipe_subshell_bug():
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "| while " not in hook, "piped while runs in POSIX sh subshell and loses fail=1"


def test_rapidocr_backend_on_chinese_scanned_pdf(tmp_path: Path):
    pytest.importorskip("rapidocr_onnxruntime")
    font = None
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"):
        if Path(candidate).exists():
            font = candidate
            break
    if font is None:
        pytest.skip("no CJK font")

    src = fitz.open()
    p = src.new_page(width=450, height=160)
    p.insert_font(fontname="cjk", fontfile=font)
    p.insert_text((30, 70), "原告：肖测国 手机：13900001111", fontsize=16, fontname="cjk")
    pix = p.get_pixmap(dpi=200, alpha=False)
    src.close()

    scan = tmp_path / "scan_cn.pdf"
    img_doc = fitz.open()
    ip = img_doc.new_page(width=pix.width, height=pix.height)
    ip.insert_image(ip.rect, pixmap=pix)
    img_doc.save(scan)
    img_doc.close()

    from legal_redactor.ocr_engine import ocr_pdf
    from legal_redactor.scan_pdf import redact_scanned_pdf

    ocr_res = ocr_pdf(scan, tmp_path / "ocr_out", engine="rapidocr")
    assert "肖测国" in ocr_res.text_normalized
    assert "13900001111" in ocr_res.text_normalized

    scan_res = redact_scanned_pdf(scan, tmp_path / "redacted_scan.pdf", mode="production", engine="rapidocr")
    assert scan_res.ok
    assert any(h["category"] == "mobile" for h in scan_res.hits)

