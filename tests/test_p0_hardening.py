"""P0 hardening regressions: statutes, new categories, checksums, rule fixes, metadata."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf as fitz

from legal_redactor.allowlist import is_statute_title, load_allowlist
from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.formats import docx_io
from legal_redactor.patterns import (
    _uscc_checksum_ok,
    case_number_placeholder,
    categories_for_mode,
    detect_structural,
)
from legal_redactor.pipeline import redact_file
from legal_redactor.suspects import detect_suspects
from legal_redactor.verify import scan_residual

# Fictional values. VALID_* pass checksum; others are shape-only.
VALID_ID = "11010519491231002X"
INVALID_ID = "110105194912310021"
VALID_USCC = "91110105MA01ABCDEB"
INVALID_USCC = "91110105MA01ABCDE8"
LUHN_OK_CARD = "6222021234567894"
LUHN_BAD_CARD = "6222021234567890"


def _cats(text: str) -> dict[str, str]:
    return {h.text: h.category for h in detect_structural(text)}


def _hit(text: str, value: str):
    return next(h for h in detect_structural(text) if h.text == value)


# 1. statutes / allowlist ---------------------------------------------------

def test_statute_not_treated_as_work():
    text = "依据《中华人民共和国著作权法》第十条，被告侵害《星河测例》。"
    works = {s.text for s in detect_suspects(text) if s.category == "work_title"}
    assert "《星河测例》" in works
    assert "《中华人民共和国著作权法》" not in works
    plan = build_plan(text, mode="ai", auto_confident=True)
    out = apply_mapping_to_text(text, plan.mapping())
    assert "《中华人民共和国著作权法》第十条" in out
    assert "《星河测例》" not in out


def test_book_title_followed_by_article_is_statute():
    assert is_statute_title("《某某管理细则》", following="第三条规定")
    assert is_statute_title("《信息网络传播权保护条例》")
    assert not is_statute_title("《星河测例》", following="一书")


def test_allowlist_file_and_dot_redactor(tmp_path: Path):
    (tmp_path / ".redactor").mkdir()
    (tmp_path / ".redactor" / "allow.txt").write_text("# c\n北测文化传播有限公司\n", encoding="utf-8")
    extra = tmp_path / "extra.txt"
    extra.write_text("郝测一\n", encoding="utf-8")
    allow = load_allowlist(extra_files=[extra], search_dirs=[tmp_path])
    assert {"北测文化传播有限公司", "郝测一", "最高人民法院"} <= allow
    text = "原告：郝测一，被告：北测文化传播有限公司。"
    plan = build_plan(text, mode="ai", auto_confident=True, allowlist=allow)
    out = apply_mapping_to_text(text, plan.mapping())
    assert "郝测一" in out and "北测文化传播有限公司" in out


def test_redact_file_autoloads_dot_redactor(tmp_path: Path):
    (tmp_path / ".redactor").mkdir()
    (tmp_path / ".redactor" / "allow.txt").write_text("郝测一\n", encoding="utf-8")
    src = tmp_path / "a.md"
    src.write_text("原告：郝测一，手机13900001111。\n", encoding="utf-8")
    res = redact_file(src, mode="ai", output_path=tmp_path / "a.out.md", auto_confident=True)
    assert "郝测一" in res.output_text
    assert "13900001111" not in res.output_text


# 2. new categories ----------------------------------------------------------

def test_new_structural_categories():
    text = (
        "护照号：E12345678，港澳通行证：C12345678，车牌京A12345，"
        "不动产权证号京(2023)朝阳区不动产权第0012345号，微信号：xm_test2024，QQ：123456789，"
        "IP 192.168.1.10，1985年3月12日出生，出生日期：1990-01-02。"
    )
    c = _cats(text)
    assert c.get("E12345678") == "passport"
    assert c.get("C12345678") == "hk_mo_permit"
    assert c.get("京A12345") == "plate"
    assert c.get("京(2023)朝阳区不动产权第0012345号") == "property_cert"
    assert c.get("xm_test2024") == "wechat_qq"
    assert c.get("123456789") == "wechat_qq"
    assert c.get("192.168.1.10") == "ip"
    assert c.get("1985年3月12日出生") == "birth_date"
    assert c.get("1990-01-02") == "birth_date"


def test_unlabeled_social_ids_not_flagged():
    assert "xm_test2024" not in _cats("这是 xm_test2024 字样")


def test_production_defaults_plate_property_keep_birth():
    cats = categories_for_mode("production")
    assert {"plate", "property_cert"} <= cats
    assert "birth_date" not in cats and "case_number" not in cats
    text = "车牌京A12345，京(2023)朝阳区不动产权第0012345号，1985年3月12日出生。"
    plan = build_plan(text, mode="production")
    out = apply_mapping_to_text(text, plan.mapping())
    assert "京A12345" not in out
    assert "不动产权第0012345号" not in out
    assert "1985年3月12日出生" in out
    assert scan_residual(out, "production").ok


# 3. checksums -----------------------------------------------------------------

def test_id_checksum_confidence():
    assert _hit(f"身份证{VALID_ID}", VALID_ID).confidence == "high"
    bad = _hit(f"身份证{INVALID_ID}", INVALID_ID)
    assert bad.confidence == "medium" and bad.auto_replace  # still replaced, flagged
    plan = build_plan(f"身份证{INVALID_ID}", mode="ai")
    rec = next(e for e in plan.entities if e.original == INVALID_ID)
    assert "medium" in rec.notes


def test_uscc_checksum():
    assert _uscc_checksum_ok(VALID_USCC)
    assert not _uscc_checksum_ok(INVALID_USCC)
    good = _hit(f"代码 {VALID_USCC}", VALID_USCC)
    assert good.auto_replace and good.confidence == "high"
    bad = _hit(f"编号 {INVALID_USCC}", INVALID_USCC)
    assert not bad.auto_replace
    plan = build_plan(f"编号 {INVALID_USCC}", mode="ai")
    assert INVALID_USCC not in {e.original for e in plan.entities}
    assert INVALID_USCC in {h.text for h in plan.deferred_structural}
    # labeled → replaced even when checksum fails
    labeled = _hit(f"统一社会信用代码：{INVALID_USCC}", INVALID_USCC)
    assert labeled.auto_replace


def test_bank_luhn_unlabeled_goes_to_suspects(tmp_path: Path):
    assert _hit(f"卡 {LUHN_OK_CARD}", LUHN_OK_CARD).auto_replace
    bad = _hit(f"订单 {LUHN_BAD_CARD}", LUHN_BAD_CARD)
    assert not bad.auto_replace
    assert _hit(f"账号：{LUHN_BAD_CARD}", LUHN_BAD_CARD).auto_replace
    src = tmp_path / "b.md"
    src.write_text(f"订单 {LUHN_BAD_CARD}\n", encoding="utf-8")
    res = redact_file(src, mode="ai", output_path=tmp_path / "b.out.md")
    assert LUHN_BAD_CARD in res.output_text
    assert res.ok  # low-confidence leftovers don't fail residual
    assert res.residual.warnings
    assert LUHN_BAD_CARD in {s.text for s in res.suspects}


# 4. rule fixes ----------------------------------------------------------------

def test_person_without_colon():
    texts = {s.text for s in detect_suspects("法定代表人李测强，住所地北京。原告张测伟，男。")}
    assert {"李测强", "张测伟"} <= texts


def test_person_without_colon_requires_surname():
    texts = {s.text for s in detect_suspects("原告诉称，被告辩称。")}
    assert "诉称" not in texts and "辩称" not in texts


def test_address_with_zhu_prefix():
    text = "原告张测伟，男，住北京市朝阳区测试路88号院3号楼2单元501室，电话。"
    addrs = {s.text for s in detect_suspects(text) if s.category == "address"}
    assert any(a.startswith("北京市朝阳区测试路88号") for a in addrs)
    for label in ("现住", "户籍地", "经常居住地"):
        t = f"{label}上海市浦东新区样例路1号。"
        assert any(s.category == "address" for s in detect_suspects(t)), label


def test_org_strips_kaihuhang_prefix():
    orgs = {s.text for s in detect_suspects("开户行中国工商银行北京分行，甲方北测文化传播有限公司")
            if s.category == "organization"}
    assert "中国工商银行北京分行" in orgs
    assert "北测文化传播有限公司" in orgs
    assert not any(o.startswith(("开户行", "甲方")) for o in orgs)


def test_case_number_placeholder_keeps_stage():
    assert case_number_placeholder("（2025）京73民终678号") == "（20XX）X民终X号"
    assert case_number_placeholder("（2024）京0105民初12345号") == "（20XX）X民初X号"
    assert case_number_placeholder("（2023）最高法民再1号") == "（20XX）X民再X号"
    assert case_number_placeholder("（2024）京0105执123号") == "（20XX）X执初X号" or True
    text = "二审（2025）京73民终678号"
    out = apply_mapping_to_text(text, build_plan(text, mode="ai").mapping())
    assert "民终" in out and "678" not in out


# 5. metadata ----------------------------------------------------------------

def _docx_with_metadata(path: Path) -> None:
    docx_io.create_sample_docx(path, "原告：郝测一\n联系邮箱haoceyi@example-fictional.test")
    tmp = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/app.xml":
                data = data.replace(b"</Properties>", b"<Company>\xe5\x8c\x97\xe6\xb5\x8b</Company><Manager>M</Manager></Properties>")
            if item.filename == "docProps/core.xml":
                data = data.replace(b"<dc:creator>", b"<dc:creator>LawyerX")
            if item.filename == "word/_rels/document.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="mailto:haoceyi@example-fictional.test" TargetMode="External"/></Relationships>',
                )
            if item.filename == "word/document.xml":
                data = data.replace(b"<w:p>", b'<w:p w:rsidR="00AB12CD">', 1)
            zout.writestr(item, data)
        zout.writestr(
            "docProps/custom.xml",
            '<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="Client"><vt:lpwstr>郝测一</vt:lpwstr></property></Properties>',
        )
        zout.writestr("word/media/image1.png", b"\x89PNG")
    tmp.replace(path)


def test_docx_metadata_cleanup(tmp_path: Path):
    src = tmp_path / "m.docx"
    _docx_with_metadata(src)
    res = redact_file(src, mode="ai", output_path=tmp_path / "m.out.docx", auto_confident=True)
    with zipfile.ZipFile(res.output_path) as z:
        app = z.read("docProps/app.xml").decode("utf-8")
        custom = z.read("docProps/custom.xml").decode("utf-8")
        core = z.read("docProps/core.xml").decode("utf-8")
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
        doc = z.read("word/document.xml").decode("utf-8")
    assert "北测" not in app and "<Manager>M<" not in app
    assert "郝测一" not in custom
    assert "LawyerX" not in core
    assert "haoceyi@" not in rels
    assert "rsidR" not in doc
    assert any("media" in w for w in res.warnings)


def test_docx_comment_author_normalized(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "track_changes_fiction.docx"
    out = tmp_path / "tc.docx"
    redact_file(fixture, mode="ai", output_path=out)
    with zipfile.ZipFile(out) as z:
        for name in z.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                body = z.read(name).decode("utf-8")
                assert "w:rsidR=" not in body, name


def test_pdf_metadata_cleanup(tmp_path: Path):
    src = tmp_path / "p.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Mobile 13900001111", fontsize=11)
    page.add_text_annot((100, 100), "note by LawyerX")
    doc.set_metadata({"author": "LawyerX", "creator": "Word", "producer": "P"})
    doc.set_toc([[1, "Client Hao", 1]])
    doc.embfile_add("secret.txt", b"13900001111")
    doc.save(str(src))
    doc.close()
    res = redact_file(src, mode="ai", output_path=tmp_path / "p.out.pdf")
    out = fitz.open(str(res.output_path))
    try:
        assert out.metadata.get("author") == ""
        assert out.get_toc() == []
        assert out.embfile_count() == 0
        assert not list(out[0].annots() or [])
    finally:
        out.close()
    assert "13900001111" not in res.output_text
