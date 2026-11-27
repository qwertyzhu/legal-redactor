from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.formats.pdf_io import redact_pdf
from legal_redactor.pipeline import redact_file


def _form(value: str = "Earlier evidence value", font: str = "Helv") -> tuple[fitz.Document, fitz.Page, int]:
    doc = fitz.open()
    page = doc.new_page(width=400, height=280)
    page.insert_text((24, 40), "Fictional evidence")
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "evidence"
    widget.field_value = value
    widget.rect = fitz.Rect(24, 60, 300, 90)
    widget.text_fontsize = 12
    widget.text_font = font
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


@pytest.mark.parametrize("need_appearances", [False, True])
def test_production_does_not_flatten_password_widgets(tmp_path: Path, need_appearances: bool) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    doc, _, widget_xref = _form()
    with doc:
        doc.xref_set_key(widget_xref, "Ff", str(fitz.PDF_TX_FIELD_IS_PASSWORD))
        doc.xref_set_key(widget_xref, "V", fitz.get_pdf_str("Fictional secret"))
        if need_appearances:
            doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "true")
        doc.save(src)

    warnings: list[str] = []
    text = redact_pdf(src, out, [], warnings=warnings, mode="production")

    assert "Fictional secret" not in text
    assert "Earlier evidence value" not in text
    assert "pdf: 1 password form field(s) removed" in warnings


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


def test_production_redacts_current_widget_value_when_appearance_is_stale(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    phone = "13900001111"
    value = f"Current evidence value: {phone}"
    doc, _, widget_xref = _form()
    with doc:
        doc.xref_set_key(widget_xref, "V", fitz.get_pdf_str(value))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "true")
        doc.save(src)
    with fitz.open(src) as original:
        assert original.xref_get_key(widget_xref, "V") == ("string", value)
        kind, appearance = original.xref_get_key(widget_xref, "AP/N")
        assert kind == "xref"
        assert b"Earlier evidence value" in original.xref_stream(int(appearance.split()[0]))

    result = redact_file(src, "production", out, work_dir=tmp_path)

    assert result.ok
    with fitz.open(out) as output:
        text = output[0].get_text()
        assert text.count("Current evidence value:") == 1
        assert "[手机号]" in "".join(text.split())
        assert phone not in text
        assert "Earlier evidence value" not in text


@pytest.mark.parametrize("stale_appearance", [False, True])
def test_production_preserves_widget_font_and_fits_value(tmp_path: Path, stale_appearance: bool) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    value = "Full fictional evidence payment reference ABCDEFGHIJK" if stale_appearance else "Current evidence value"
    doc, page, widget_xref = _form("Earlier evidence value" if stale_appearance else value, "Cour")
    with doc:
        font_xref = page.insert_font(fontname="cour")
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/DR", f"<</Font<</EvidenceFont {font_xref} 0 R>>>>")
        doc.xref_set_key(widget_xref, "DA", fitz.get_pdf_str("/EvidenceFont 24 Tf 0 g"))
        doc.xref_set_key(widget_xref, "V", fitz.get_pdf_str(value))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "true")
        doc.save(src)

    redact_pdf(src, out, [], mode="production")

    with fitz.open(out) as result:
        spans = [
            span for block in result[0].get_text("dict", clip=fitz.Rect(24, 60, 300, 90))["blocks"]
            for line in block["lines"] for span in line["spans"]
        ]
        assert [(span["text"], span["font"]) for span in spans] == [(value, "Courier")]
        if not stale_appearance:
            assert spans[0]["size"] == pytest.approx(12)


def test_production_keeps_wrapped_cjk_widget_appearance(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    lines = ["证据说明确认", "原件已经核验"]
    doc, page, widget_xref = _form()
    with doc:
        font_xref = page.insert_font(fontname="china-s")
        _, appearance = doc.xref_get_key(widget_xref, "AP/N")
        appearance_xref = int(appearance.split()[0])
        doc.xref_set_key(appearance_xref, "Resources", f"<</Font<</CJK {font_xref} 0 R>>>>")
        first, second = (line.encode("utf-16-be").hex() for line in lines)
        doc.update_stream(appearance_xref, f"BT /CJK 12 Tf 2 18 Td <{first}> Tj 0 -12 Td <{second}> Tj ET".encode())
        doc.xref_set_key(widget_xref, "Ff", str(fitz.PDF_TX_FIELD_IS_MULTILINE))
        doc.xref_set_key(widget_xref, "V", fitz.get_pdf_str("".join(lines)))
        doc.xref_set_key(widget_xref, "DA", fitz.get_pdf_str("/CJK 12 Tf 0 g"))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "true")
        doc.save(src)

    text = redact_pdf(src, out, [], mode="production")

    assert text.splitlines()[-2:] == lines


@pytest.mark.parametrize(("key", "value"), [("T", "null"), ("Rect", "[24 220 24 220]")])
def test_production_drops_malformed_widgets_without_aborting_export(tmp_path: Path, key: str, value: str) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    doc, _, widget_xref = _form()
    with doc:
        doc.xref_set_key(widget_xref, key, value)
        doc.xref_set_key(widget_xref, "V", fitz.get_pdf_str("Current evidence value"))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "true")
        doc.save(src)

    warnings: list[str] = []
    text = redact_pdf(src, out, [], warnings=warnings, mode="production")

    assert "Fictional evidence" in text
    assert "Earlier evidence value" not in text
    assert "Current evidence value" not in text
    assert "pdf: 1 form field(s) removed (appearance could not be regenerated)" in warnings
