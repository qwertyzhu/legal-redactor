"""P2: rule fixes, optional NER, embedded-image OCR, seal drafts, docx revisions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor import ner
from legal_redactor.cli import main
from legal_redactor.formats import docx_io
from legal_redactor.image_scan import (
    detect_seals,
    ocr_available,
    ocr_image_hits,
    seal_detection_available,
)
from legal_redactor.pipeline import redact_file
from legal_redactor.suspects import detect_suspects

needs_ocr = pytest.mark.skipif(not ocr_available(), reason="tesseract not installed")
needs_cv = pytest.mark.skipif(not seal_detection_available(), reason="opencv not installed")
PHONE = "13912345678"


# ---------- 13. rules + NER ----------

def test_judge_and_entrusted_rules():
    got = {s.text: s for s in detect_suspects("审判长 赵测审\n书记员：周测记\n本所受孙测五委托，函告如下")}
    assert got["赵测审"].role_hint == "court_staff"
    assert got["周测记"].role_hint == "court_staff"
    assert got["孙测五"].reason == "entrusted_by"
    org = detect_suspects("本所受北京某某有限公司委托")
    assert [(s.text, s.category) for s in org] == [("北京某某有限公司", "organization")]


def test_ner_absent_degrades(monkeypatch):
    monkeypatch.setenv("LEGAL_REDACTOR_NER", "off")
    ner.set_backend(None)
    assert ner.detect_ner_suspects("小明说") == []
    assert not ner.ner_available()


def test_ner_results_are_suspects_only(tmp_path: Path):
    def fake(text):
        i = text.find("小明")
        return [("小明", i, i + 2)] if i >= 0 else []

    ner.set_backend(fake)
    try:
        src = tmp_path / "n.txt"
        src.write_text("小明在现场看到了事故经过。\n", encoding="utf-8")
        res = redact_file(src, mode="ai", output_path=tmp_path / "n.out.txt", auto_confident=True)
        assert "小明" in res.output_text  # never auto-replaced
        assert any(s.text == "小明" and s.reason == "ner" for s in res.suspects)
    finally:
        ner.set_backend(None)


# ---------- 14. images ----------

def _phone_png(text=f"电话 {PHONE}") -> bytes:
    d = fitz.open()
    page = d.new_page(width=420, height=80)
    page.insert_text((10, 50), text, fontsize=28, fontname="helv")
    png = page.get_pixmap(dpi=150).tobytes("png")
    d.close()
    return png


@needs_ocr
def test_ocr_image_hits_finds_phone():
    hits, w, h, text = ocr_image_hits(_phone_png(f"Tel {PHONE}"), categories={"mobile"})
    assert w and h
    assert any(hit.category == "mobile" for hit in hits), text


@needs_ocr
def test_pdf_embedded_image_redacted(tmp_path: Path):
    src = tmp_path / "img.pdf"
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 72), "Contract text layer", fontsize=12)
    page.insert_image(fitz.Rect(72, 100, 492, 180), stream=_phone_png(f"Tel {PHONE}"))
    d.save(str(src))
    d.close()
    res = redact_file(src, mode="ai", output_path=tmp_path / "img.out.pdf", ocr_images="on")
    assert any("IMAGE-PII" in w for w in res.warnings), res.warnings
    out = fitz.open(str(res.output_path))
    xref = out[0].get_images(full=True)[0][0]
    raw = out.extract_image(xref)["image"]
    out.close()
    hits, *_ = ocr_image_hits(raw, categories={"mobile"})
    assert not hits


def test_pdf_image_warns_without_ocr(tmp_path: Path, monkeypatch):
    import legal_redactor.image_scan as im

    monkeypatch.setattr(im, "ocr_available", lambda: False)
    src = tmp_path / "img2.pdf"
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 72), "Text layer here", fontsize=12)
    page.insert_image(fitz.Rect(72, 100, 492, 180), stream=_phone_png())
    d.save(str(src))
    d.close()
    res = redact_file(src, mode="ai", output_path=tmp_path / "img2.out.pdf")
    assert any("not OCR-scanned" in w for w in res.warnings)


def _docx_with_image(path: Path, png: bytes):
    docx_io.create_sample_docx(path, "原告：张测伟")
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for it in zin.infolist():
            zout.writestr(it, zin.read(it.filename))
        zout.writestr("word/media/image1.png", png)
    tmp.replace(path)


@needs_ocr
def test_docx_image_pii_listed(tmp_path: Path):
    src = tmp_path / "d.docx"
    _docx_with_image(src, _phone_png(f"Tel {PHONE}"))
    res = redact_file(src, mode="ai", output_path=tmp_path / "d.out.docx", auto_confident=True)
    assert any("IMAGE-PII word/media/image1.png" in w for w in res.warnings), res.warnings


def test_docx_image_off(tmp_path: Path):
    src = tmp_path / "d2.docx"
    _docx_with_image(src, _phone_png())
    res = redact_file(src, mode="ai", output_path=tmp_path / "d2.out.docx", ocr_images="off")
    assert any("word/media/image1.png" in w and "not OCR-scanned" in w for w in res.warnings)


@needs_cv
def test_seal_detection_draft(tmp_path: Path):
    import cv2
    import numpy as np

    img = np.full((600, 800, 3), 255, np.uint8)
    cv2.circle(img, (550, 350), 90, (0, 0, 220), 8)
    cv2.putText(img, "SEAL", (500, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 220), 3)
    ok, buf = cv2.imencode(".png", img)
    seals = detect_seals(buf.tobytes())
    assert seals, "no seal found"
    x0, y0, x1, y1 = seals[0]["rect"]
    assert x0 < 550 / 800 < x1 and y0 < 350 / 600 < y1
    # plain black text -> nothing
    blank = np.full((300, 300, 3), 255, np.uint8)
    cv2.circle(blank, (150, 150), 60, (0, 0, 0), 6)
    assert detect_seals(cv2.imencode(".png", blank)[1].tobytes()) == []

    pdf = tmp_path / "s.pdf"
    d = fitz.open()
    d.new_page().insert_image(fitz.Rect(0, 0, 595, 446), stream=buf.tobytes())
    d.save(str(pdf))
    d.close()
    out = tmp_path / "s.json"
    assert main(["seal-draft", str(pdf), "-o", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["draft"] and payload["regions"][0]["category"] == "seal"


# ---------- 15. revisions ----------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _revision_docx(path: Path):
    docx_io.create_sample_docx(path, "placeholder")
    body = (
        f'<w:document xmlns:w="{W}"><w:body><w:p>'
        '<w:r><w:t xml:space="preserve">联系电话 </w:t></w:r>'
        '<w:del w:id="1" w:author="律师甲"><w:r><w:delText>13912345678</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="律师甲"><w:r><w:t>13800138000</w:t></w:r></w:ins>'
        "</w:p></w:body></w:document>"
    )
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for it in zin.infolist():
            data = body.encode() if it.filename == "word/document.xml" else zin.read(it.filename)
            zout.writestr(it, data)
    tmp.replace(path)


def _doc_xml(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        return z.read("word/document.xml").decode("utf-8")


@pytest.mark.parametrize(
    "mode,present,absent",
    [
        ("accept", [], ["13912345678", "13800138000", "w:ins", "w:del "]),
        ("reject", [], ["13912345678", "13800138000", "w:ins", "delText"]),
        ("keep-and-redact", ["w:ins", "w:delText"], ["13912345678", "13800138000", "律师甲"]),
    ],
)
def test_docx_revisions_modes(tmp_path: Path, mode, present, absent):
    src = tmp_path / "r.docx"
    _revision_docx(src)
    res = redact_file(src, mode="ai", output_path=tmp_path / f"r.{mode}.docx", revisions=mode)
    xml = _doc_xml(res.output_path)
    for s in present:
        assert s in xml, (mode, s)
    for s in absent:
        assert s not in xml, (mode, s)


def test_revisions_extract_views(tmp_path: Path):
    src = tmp_path / "v.docx"
    _revision_docx(src)
    assert "13800138000" in docx_io.extract_text(src)
    assert "13912345678" not in docx_io.extract_text(src)
    assert "13912345678" in docx_io.extract_text(src, revisions="reject")
    both = docx_io.extract_text(src, revisions="keep-and-redact")
    assert "13912345678" in both and "13800138000" in both


def test_doctor_cli(capsys):
    assert main(["doctor", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert set(info) == {"tesseract", "opencv", "ner"}


# ---------- 16. agent entry ----------

ROOT = Path(__file__).resolve().parents[1]


def test_agents_md_points_to_skill():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "skills/legal-document-redactor/SKILL.md" in text


def test_claude_code_frontmatter():
    import re

    skill = (ROOT / "skills/legal-document-redactor/SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    front = m.group(1)
    name = re.search(r"(?m)^name:\s*(\S+)\s*$", front).group(1)
    desc = re.search(r'(?m)^description:\s*"(.*)"\s*$', front).group(1)
    assert name == "legal-document-redactor"
    assert re.fullmatch(r"[a-z0-9-]{1,64}", name)
    assert 0 < len(desc) <= 1024 and "<" not in desc and ">" not in desc


# ---------- court staff preserved by default ----------

_JUDGMENT = "原告：张测伟\n审判长 赵测审\n人民陪审员 钱测陪\n书记员：周测记\n本所受孙测五委托致函。\n"


@pytest.mark.parametrize("profile", ["ai", "production", "minimal", "publish"])
def test_court_staff_kept_by_default(tmp_path: Path, profile):
    src = tmp_path / "j.txt"
    src.write_text(_JUDGMENT, encoding="utf-8")
    res = redact_file(src, mode="ai", profile=profile, output_path=tmp_path / "j.out.txt", auto_confident=True)
    for name in ("赵测审", "钱测陪", "周测记"):
        assert name in res.output_text, (profile, name)


def test_court_staff_opt_in(tmp_path: Path):
    src = tmp_path / "j.txt"
    src.write_text(_JUDGMENT, encoding="utf-8")
    res = redact_file(
        src, mode="ai", output_path=tmp_path / "j.out.txt", auto_confident=True, redact_court_staff=True
    )
    for name in ("赵测审", "钱测陪", "周测记", "孙测五", "张测伟"):
        assert name not in res.output_text, name
    out = tmp_path / "c.out.txt"
    assert main(["redact", str(src), "--mode", "ai", "--auto-confident", "--redact-court-staff", "-o", str(out)]) in (0, 1)
    assert "赵测审" not in out.read_text(encoding="utf-8")


def test_entrusted_party_still_redacted_in_ai(tmp_path: Path):
    src = tmp_path / "j.txt"
    src.write_text(_JUDGMENT, encoding="utf-8")
    res = redact_file(src, mode="ai", output_path=tmp_path / "j.out.txt", auto_confident=True)
    assert "孙测五" not in res.output_text and "张测伟" not in res.output_text


def test_profile_key_redact_court_staff():
    from legal_redactor.profiles_loader import _profile_from_dict, load_profile

    for name in ("ai", "production", "minimal", "publish"):
        assert load_profile(name).redact_court_staff is False
    assert _profile_from_dict({"redact_court_staff": True}, fallback_name="x").redact_court_staff is True
