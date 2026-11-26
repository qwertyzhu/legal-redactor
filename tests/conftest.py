from __future__ import annotations

import io

import docx
import pymupdf as fitz
import pytest

from legal_redactor import image_scan


@pytest.fixture
def docx_with_image_pii(tmp_path, monkeypatch):
    phone = "13900001111"
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=100)
        page.insert_text((20, 60), phone, fontsize=28)
        png = page.get_pixmap().tobytes("png")
    doc = docx.Document()
    doc.add_picture(io.BytesIO(png))
    path = tmp_path / "with_img.docx"
    doc.save(path)

    def ocr_image_hits(data, *, categories, **_):
        assert data == png
        hits = [image_scan.ImageHit("mobile", phone, (20, 30, 200, 60))] if "mobile" in categories else []
        return hits, 300, 100, phone

    monkeypatch.setattr(image_scan, "ocr_available", lambda: True)
    monkeypatch.setattr(image_scan, "ocr_image_hits", ocr_image_hits)
    return path


@pytest.fixture(autouse=True)
def _isolated_ledger_root(tmp_path, monkeypatch):
    """Never write test ledgers into the real ~/.legal-redactor."""
    monkeypatch.setenv("LEGAL_REDACTOR_LEDGER_ROOT", str(tmp_path / "_ledgers"))
    monkeypatch.delenv("LEGAL_REDACTOR_LEDGER_PASS", raising=False)


@pytest.fixture(autouse=True)
def _ner_off_by_default(monkeypatch):
    """Keep tests deterministic/fast even when the [ner] extra is installed."""
    from legal_redactor import ner

    monkeypatch.setenv("LEGAL_REDACTOR_NER", "off")
    ner.set_backend(None)
    yield
    ner.set_backend(None)
