"""Text-layer PDF redaction via PyMuPDF search + redaction annotations."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from ..entities import apply_mapping_to_text


def extract_text(path: Path) -> str:
    doc = fitz.open(str(path))
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text("text"))
        return "\n".join(parts)
    finally:
        doc.close()


def _has_text_layer(doc: fitz.Document) -> bool:
    for page in doc:
        if page.get_text("text").strip():
            return True
    return False


def _redaction_fontname(text: str) -> str:
    """Helvetica cannot paint CJK placeholders like [手机号] / 某甲."""
    if any(ord(ch) > 127 for ch in text):
        return "china-s"
    return "helv"


def redact_pdf(
    input_path: Path,
    output_path: Path,
    mapping: list[tuple[str, str]],
) -> str:
    doc = fitz.open(str(input_path))
    try:
        if not _has_text_layer(doc):
            raise ValueError(
                "PDF has no extractable text layer (likely a scan). "
                "Use: legal-redactor ocr INPUT.pdf -o workdir/ "
                "then redact the normalized markdown; "
                "or for court visual cover-up: legal-redactor redact-scan INPUT.pdf -o OUT.pdf"
            )

        for page in doc:
            found_any = False
            for original, replacement in mapping:
                if not original:
                    continue
                rects = page.search_for(original)
                for rect in rects:
                    page.add_redact_annot(
                        rect,
                        text=replacement,
                        fill=(1, 1, 1),
                        text_color=(0, 0, 0),
                        fontsize=9,
                        fontname=_redaction_fontname(replacement),
                    )
                    found_any = True
            if found_any:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        meta = doc.metadata or {}
        for key in list(meta.keys()):
            val = meta.get(key)
            if isinstance(val, str) and val:
                meta[key] = apply_mapping_to_text(val, mapping)
        for key in ("author", "title", "subject", "keywords", "producer", "creator"):
            if key in meta and isinstance(meta[key], str):
                meta[key] = apply_mapping_to_text(meta[key], mapping)
        doc.set_metadata(meta)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), deflate=True, garbage=3)
    finally:
        doc.close()

    return extract_text(output_path)


def create_sample_pdf(path: Path, text: str) -> None:
    """Create a simple text-layer PDF. Prefer a Chinese-capable font when present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()

    fontname = "helv"
    # Try common Windows / Noto fonts for CJK; fall back to built-in (latin only).
    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ):
        fp = Path(candidate)
        if fp.exists():
            try:
                fontname = "cjk"
                page.insert_font(fontname=fontname, fontfile=str(fp))
                break
            except Exception:
                fontname = "helv"

    y = 72.0
    for line in text.split("\n"):
        page.insert_text((72, y), line, fontsize=11, fontname=fontname)
        y += 16
        if y > page.rect.height - 72:
            page = doc.new_page()
            if fontname == "cjk":
                # re-insert font on new page
                for candidate in (
                    "C:/Windows/Fonts/msyh.ttc",
                    "C:/Windows/Fonts/simsun.ttc",
                    "C:/Windows/Fonts/simhei.ttf",
                ):
                    if Path(candidate).exists():
                        page.insert_font(fontname="cjk", fontfile=candidate)
                        break
            y = 72.0
    doc.save(str(path))
    doc.close()
