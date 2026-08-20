"""DOCX load / text extract / mapped rewrite."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..entities import apply_mapping_to_text


def _iter_paragraphs(doc: DocumentObject):
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        yield from _iter_table_paragraphs(table)
    # headers / footers
    for section in doc.sections:
        for p in section.header.paragraphs:
            yield p
        for table in section.header.tables:
            yield from _iter_table_paragraphs(table)
        for p in section.footer.paragraphs:
            yield p
        for table in section.footer.tables:
            yield from _iter_table_paragraphs(table)


def _iter_table_paragraphs(table: Table):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                yield p
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested)


def extract_text(path: Path) -> str:
    doc = Document(str(path))
    parts = [p.text for p in _iter_paragraphs(doc) if p.text]
    return "\n".join(parts)


def _replace_paragraph_text(paragraph: Paragraph, new_text: str) -> None:
    """Replace paragraph text while trying to keep basic run styling of the first run."""
    if paragraph.text == new_text:
        return
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(new_text)
        return
    runs[0].text = new_text
    for run in runs[1:]:
        run.text = ""


def redact_docx(
    input_path: Path,
    output_path: Path,
    mapping: list[tuple[str, str]],
) -> str:
    doc = Document(str(input_path))
    for paragraph in _iter_paragraphs(doc):
        original = paragraph.text
        if not original:
            continue
        updated = apply_mapping_to_text(original, mapping)
        if updated != original:
            _replace_paragraph_text(paragraph, updated)

    # Core document properties may leak party names
    props = doc.core_properties
    for attr in ("author", "last_modified_by", "comments", "subject", "title"):
        try:
            val = getattr(props, attr, None)
            if isinstance(val, str) and val:
                setattr(props, attr, apply_mapping_to_text(val, mapping))
        except Exception:
            pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return extract_text(output_path)


def create_sample_docx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for block in text.split("\n"):
        doc.add_paragraph(block)
    doc.save(str(path))
