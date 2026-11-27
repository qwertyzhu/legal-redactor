from pathlib import Path

import pymupdf as fitz

from legal_redactor.formats.pdf_io import redact_pdf


def _form() -> tuple[fitz.Document, fitz.Page, int]:
    doc = fitz.open()
    page = doc.new_page(width=400, height=280)
    page.insert_text((24, 40), "Fictional evidence")
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "evidence"
    widget.field_value = "Earlier evidence value"
    widget.rect = fitz.Rect(24, 60, 300, 90)
    widget.text_fontsize = 12
    return doc, page, page.add_widget(widget).xref


def test_production_does_not_flatten_indirectly_hidden_widget(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    doc, _, widget_xref = _form()
    with doc:
        flags_xref = doc.get_new_xref()
        doc.update_object(flags_xref, str(fitz.PDF_ANNOT_IS_NO_VIEW))
        doc.xref_set_key(widget_xref, "F", f"{flags_xref} 0 R")
        doc.save(src)
    with fitz.open(src) as original:
        assert original.xref_get_key(widget_xref, "F")[0] == "xref"
        assert "Earlier evidence value" not in original[0].get_text()

    text = redact_pdf(src, out, [], mode="production")

    assert "Earlier evidence value" not in text


def test_production_keeps_evidence_image_with_inherited_page_resources(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    doc, page, _ = _form()
    with doc:
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), 0)
        image.clear_with(90)
        page.insert_image(fitz.Rect(26, 120, 126, 220), pixmap=image)
        parent_xref = int(doc.xref_get_key(page.xref, "Parent")[1].split()[0])
        resources = doc.xref_get_key(page.xref, "Resources")[1]
        doc.xref_set_key(parent_xref, "Resources", resources)
        doc.xref_set_key(page.xref, "Resources", "null")
        doc.save(src)
    with fitz.open(src) as original:
        assert original.xref_get_key(original[0].xref, "Resources")[0] == "null"
        assert original[0].get_pixmap().pixel(50, 150) == (90, 90, 90)

    redact_pdf(src, out, [], ocr_images="off", mode="production")

    with fitz.open(out) as output:
        assert output[0].get_pixmap().pixel(50, 150) == (90, 90, 90)
