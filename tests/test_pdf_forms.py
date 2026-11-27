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
