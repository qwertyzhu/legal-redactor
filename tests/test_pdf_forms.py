from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.formats.pdf_io import extract_text_with_scan_pages, redact_pdf
from legal_redactor.pipeline import redact_file, verify_file


def _form(value: str = "Earlier evidence value") -> tuple[fitz.Document, fitz.Page, int]:
    doc = fitz.open()
    page = doc.new_page(width=400, height=280)
    page.insert_text((24, 40), "Fictional evidence")
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "evidence"
    widget.field_value = value
    widget.rect = fitz.Rect(24, 60, 300, 90)
    widget.text_fontsize = 12
    widget.text_font = "Helv"
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
        image_rect = fitz.Rect(26, 120, 126, 220)
        image_xref = page.insert_image(image_rect, pixmap=image)
        parent_xref = int(doc.xref_get_key(page.xref, "Parent")[1].split()[0])
        resources = doc.xref_get_key(page.xref, "Resources")[1]
        comment_page = doc.new_page(width=400, height=280)
        comment_page.insert_image(image_rect, xref=image_xref)
        comment_page.add_text_annot((24, 40), "Fictional comment")
        doc.xref_set_key(parent_xref, "Resources", resources)
        for page in doc:
            doc.xref_set_key(page.xref, "Resources", "null")
        doc.save(src)
    with fitz.open(src) as original:
        assert not list(original[1].widgets())
        assert len(list(original[1].annots())) == 1
        for page in original:
            assert original.xref_get_key(page.xref, "Resources")[0] == "null"
            assert page.get_pixmap().pixel(50, 150) == (90, 90, 90)

    redact_pdf(src, out, [], ocr_images="off", mode="production")

    with fitz.open(out) as output:
        for page in output:
            assert page.get_pixmap().pixel(50, 150) == (90, 90, 90), page.number


def test_production_drops_widgets_when_appearances_are_untrusted(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    value = "Current evidence value"
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
        assert output[0].get_text().splitlines() == ["Fictional evidence"]
        assert not list(output[0].widgets())
    assert "pdf: 1 form field(s) removed (NeedAppearances set, appearances not trusted)" in result.warnings


def test_production_redacts_email_wrapped_in_widget_appearance(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    first, second = "fictional@", "example.test"
    doc, _, widget_xref = _form(first + second)
    with doc:
        _, appearance = doc.xref_get_key(widget_xref, "AP/N")
        appearance_xref = int(appearance.split()[0])
        doc.xref_set_key(widget_xref, "Ff", str(fitz.PDF_TX_FIELD_IS_MULTILINE))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "false")
        doc.update_stream(appearance_xref, f"BT /Helv 12 Tf 2 18 Td ({first}) Tj 0 -12 Td ({second}) Tj ET".encode())
        doc.save(src)

    assert extract_text_with_scan_pages(src)[0].splitlines()[-2:] == [first, second]
    result = redact_file(src, "production", out, work_dir=tmp_path)

    assert result.ok
    with fitz.open(out) as output:
        text = output[0].get_text()
        assert first not in text and second not in text
        assert "[邮箱]" in "".join(text.split())
        assert not list(output[0].widgets())
    assert verify_file(out, "production").status == "PASS"


def test_production_removes_hidden_widget_parent_value(tmp_path: Path) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    phone = "13900001111"
    doc, _, widget_xref = _form()
    with doc:
        parent_xref = doc.get_new_xref()
        doc.update_object(parent_xref, f"<</FT/Tx /T (Parent field) /V ({phone}) /Kids [{widget_xref} 0 R]>>")
        doc.xref_set_key(widget_xref, "Parent", f"{parent_xref} 0 R")
        doc.xref_set_key(widget_xref, "V", "null")
        doc.xref_set_key(widget_xref, "F", str(fitz.PDF_ANNOT_IS_HIDDEN))
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", f"[{parent_xref} 0 R]")
        doc.save(src)

    warnings: list[str] = []
    text = redact_pdf(src, out, [], mode="production", warnings=warnings)

    assert "pdf: 1 hidden form field(s) removed" in warnings
    assert phone not in text
    assert verify_file(out, "production").status == "PASS"
    with fitz.open(out) as output:
        objects = {xref: output.xref_object(xref) for xref in range(1, output.xref_length())}
        assert not {xref: obj for xref, obj in objects.items() if phone in obj}
        assert output.xref_get_key(output.pdf_catalog(), "AcroForm")[0] == "null"
    assert phone.encode() not in out.read_bytes()


@pytest.mark.parametrize(("key", "value"), [("Rect", "[24 220 24 220]"), ("AP", "null")])
def test_production_drops_unusable_widget_without_aborting_export(tmp_path: Path, key: str, value: str) -> None:
    src, out = tmp_path / "input.pdf", tmp_path / "redacted.pdf"
    doc, _, widget_xref = _form()
    with doc:
        doc.xref_set_key(widget_xref, key, value)
        doc.save(src)

    warnings: list[str] = []
    text = redact_pdf(src, out, [], warnings=warnings, mode="production")

    assert "Fictional evidence" in text
    assert "Earlier evidence value" not in text
    assert "pdf: 1 form field(s) removed (no usable stored appearance)" in warnings
