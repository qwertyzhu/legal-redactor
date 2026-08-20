"""Tests for OCR helpers and scanned-PDF path (no real client files)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from legal_redactor.ocr_engine import collapse_cjk_spaces, find_tesseract, pdf_has_text_layer


def test_collapse_cjk_spaces():
    raw = "北 京 奇 艺 世 纪 科 技 有 限 公 司"
    assert collapse_cjk_spaces(raw) == "北京奇艺世纪科技有限公司"
    assert collapse_cjk_spaces("手机 13900001111") == "手机 13900001111"


def test_pdf_has_text_layer_false_for_image_only(tmp_path: Path):
    # pure image page
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    # draw only graphics, no text
    page.draw_rect(page.rect, color=(0, 0, 0), width=2)
    path = tmp_path / "img_only.pdf"
    doc.save(path)
    doc.close()
    assert pdf_has_text_layer(path) is False


def test_pdf_has_text_layer_true(tmp_path: Path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello structural 13900001111", fontsize=12)
    path = tmp_path / "text.pdf"
    doc.save(path)
    doc.close()
    assert pdf_has_text_layer(path) is True


def test_redact_scan_on_image_pdf_with_phone(tmp_path: Path):
    try:
        tess = find_tesseract()
    except FileNotFoundError:
        pytest.skip("tesseract not installed")

    # Render a page that is an image containing a phone number
    # Create via pixmap of a text page, then embed as image-only PDF
    src = fitz.open()
    page = src.new_page(width=400, height=200)
    page.insert_text((40, 100), "Contact 13900001111 bank 6222021234567890123", fontsize=14)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    src.close()

    img_pdf = fitz.open()
    ip = img_pdf.new_page(width=pix.width, height=pix.height)
    ip.insert_image(ip.rect, pixmap=pix)
    in_path = tmp_path / "scan_in.pdf"
    img_pdf.save(in_path)
    img_pdf.close()

    assert pdf_has_text_layer(in_path) is False

    from legal_redactor.scan_pdf import redact_scanned_pdf

    out = tmp_path / "scan_out.pdf"
    # Point tessdata if needed via env; function uses find_tessdata
    result = redact_scanned_pdf(in_path, out, mode="production", dpi=150, work_dir=tmp_path)
    assert out.is_file()
    assert result.ok
    # Should have found at least mobile or bank
    cats = {h["category"] for h in result.hits}
    assert cats & {"mobile", "bank_account", "landline"}, result.hits
