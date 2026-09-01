"""Tests for OCR helpers and scanned-PDF path (no real client files)."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.ocr_engine import collapse_cjk_spaces, find_tesseract, pdf_has_text_layer
from legal_redactor.scan_pdf import (
    _collect_identifier_boxes,
    _collect_region_boxes,
    _party_entries,
)


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


def test_party_selection_matches_name_across_words_and_keeps_other_party():
    words = [
        {
            "left": 10,
            "top": 20,
            "width": 45,
            "height": 15,
            "text": "北测",
            "block_num": 1,
            "par_num": 1,
            "line_num": 1,
            "word_num": 1,
        },
        {
            "left": 60,
            "top": 20,
            "width": 90,
            "height": 15,
            "text": "文化有限公司",
            "block_num": 1,
            "par_num": 1,
            "line_num": 1,
            "word_num": 2,
        },
        {
            "left": 10,
            "top": 60,
            "width": 140,
            "height": 15,
            "text": "南测发行有限公司",
            "block_num": 1,
            "par_num": 2,
            "line_num": 1,
            "word_num": 1,
        },
    ]
    spec = {
        "parties": {
            "a": {"identifiers": ["北测文化有限公司"], "regions": []},
            "b": {"identifiers": ["南测发行有限公司"], "regions": []},
        }
    }
    identifiers, _ = _party_entries(spec, "a")
    boxes = _collect_identifier_boxes(words, identifiers)
    assert len(boxes) == 1
    assert boxes[0][4] == "party_a_identifier"
    assert boxes[0][5] == "北测文化有限公司"
    assert boxes[0][1] < 20 < boxes[0][3]
    assert all("南测" not in box[5] for box in boxes)


def test_party_selection_supports_both_and_reviewed_complete_seal_regions():
    spec = {
        "parties": {
            "a": {
                "identifiers": [{"text": "北测文化有限公司", "category": "organization"}],
                "regions": [
                    {
                        "page": 2,
                        "rect": [0.10, 0.20, 0.40, 0.55],
                        "category": "seal",
                        "note": "甲方整枚公章",
                    }
                ],
            },
            "b": {
                "identifiers": ["南测发行有限公司"],
                "regions": [
                    {
                        "page": 2,
                        "rect": [0.60, 0.20, 0.90, 0.55],
                        "category": "signature_block",
                    }
                ],
            },
        }
    }
    identifiers, regions = _party_entries(spec, "both")
    assert {item["party"] for item in identifiers} == {"a", "b"}
    boxes = _collect_region_boxes(regions, page_number=2, width=1000, height=2000)
    assert len(boxes) == 2
    assert boxes[0][:4] == (100.0, 400.0, 400.0, 1100.0)
    assert boxes[0][4] == "party_a_seal"
    assert boxes[1][4] == "party_b_signature_block"


def test_party_spec_rejects_non_normalized_or_missing_selected_party():
    with pytest.raises(ValueError, match="normalized"):
        _party_entries(
            {
                "parties": {
                    "a": {
                        "identifiers": [],
                        "regions": [{"page": 1, "rect": [0, 0, 2, 1]}],
                    }
                }
            },
            "a",
        )
    with pytest.raises(ValueError, match="missing selected party"):
        _party_entries({"parties": {"a": {"identifiers": ["北测"], "regions": []}}}, "b")


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


def test_redact_scan_selected_party_does_not_blank_other_party_structural_data(tmp_path: Path):
    try:
        find_tesseract()
    except FileNotFoundError:
        pytest.skip("tesseract not installed")

    src = fitz.open()
    page = src.new_page(width=600, height=300)
    page.insert_text((40, 80), "Party A Alpha Media 13900001111", fontsize=18)
    page.insert_text((40, 150), "Party B Beta Media 13800002222", fontsize=18)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    src.close()

    image_pdf = fitz.open()
    image_page = image_pdf.new_page(width=pix.width, height=pix.height)
    image_page.insert_image(image_page.rect, pixmap=pix)
    input_path = tmp_path / "parties.pdf"
    image_pdf.save(input_path)
    image_pdf.close()

    spec_path = tmp_path / "party-spec.json"
    spec_path.write_text(
        """{
  "parties": {
    "a": {
      "identifiers": [
        {"text": "Alpha Media", "category": "organization"},
        {"text": "13900001111", "category": "mobile"}
      ],
      "regions": [
        {"page": 1, "rect": [0.70, 0.05, 0.90, 0.25], "category": "seal"}
      ]
    },
    "b": {
      "identifiers": [
        {"text": "Beta Media", "category": "organization"},
        {"text": "13800002222", "category": "mobile"}
      ],
      "regions": []
    }
  }
}
""",
        encoding="utf-8",
    )

    from legal_redactor.scan_pdf import redact_scanned_pdf

    output = tmp_path / "party-a-redacted.pdf"
    result = redact_scanned_pdf(
        input_path,
        output,
        mode="production",
        dpi=150,
        lang="eng",
        work_dir=tmp_path,
        redact_party="a",
        party_spec_path=spec_path,
    )
    categories = {hit["category"] for hit in result.hits}
    assert result.party_scope == "a"
    assert "party_a_mobile" in categories
    assert "party_a_seal" in categories
    assert not any(category.startswith("party_b_") for category in categories)
    assert not categories & {"mobile", "bank_account", "landline", "email"}
