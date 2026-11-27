"""Text-layer PDF redaction via PyMuPDF search + redaction annotations."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pymupdf as fitz

from ..entities import apply_mapping_to_text
from . import FIELD_BOUNDARY, scrub_external_url, scrub_structural_text

_CLEAR_PDF_META_KEYS = frozenset({"author", "creator", "producer", "title", "subject", "keywords"})
_SKIP_PDF_META_KEYS = frozenset({"format", "creationDate", "modDate", "encryption", "trapped"})


def extract_text(path: Path) -> str:
    doc = fitz.open(str(path))
    try:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text("text"))
        return "\n".join(parts)
    finally:
        doc.close()


def extract_metadata_and_links(path: Path) -> str:
    """Extract PDF metadata values and external link targets for residual verification."""
    doc = fitz.open(str(path))
    try:
        parts: list[str] = []
        for key, val in (doc.metadata or {}).items():
            if key in _SKIP_PDF_META_KEYS:
                continue
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        for page in doc:
            for link in page.get_links() or []:
                uri = (link.get("uri") or "").strip()
                if uri:
                    parts.append(uri)
                    decoded = unquote(uri)
                    if decoded != uri:
                        parts.append(decoded)
                file_target = (link.get("file") or "").strip()
                if file_target:
                    parts.append(file_target)
        return FIELD_BOUNDARY.join(parts)
    finally:
        doc.close()


def extract_text_with_scan_pages(path: Path) -> tuple[str, list[int]]:
    """Return text and 1-based pages with raster content but no extractable text."""
    parts: list[str] = []
    scan_pages: list[int] = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            text = page.get_text("text")
            parts.append(text)
            if not text.strip() and page.get_image_info():
                scan_pages.append(page.number + 1)
    return "\n".join(parts), scan_pages
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


def _clear_pdf_extras(doc: "fitz.Document", warnings: list[str] | None, *, preserve_bookmarks: bool = False) -> None:
    """Drop XMP, bookmarks, form fields, comment annotations and attachments."""
    warn = warnings if warnings is not None else []
    try:
        doc.del_xml_metadata()
    except Exception as exc:  # noqa: BLE001
        warn.append(f"pdf: could not delete XMP metadata ({exc})")
    if not preserve_bookmarks:
        try:
            if doc.get_toc():
                doc.set_toc([])
                warn.append("pdf: bookmarks removed (titles may carry names)")
        except Exception as exc:  # noqa: BLE001
            warn.append(f"pdf: could not clear bookmarks ({exc})")
    removed_annots = removed_widgets = 0
    for page in doc:
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
            removed_widgets += 1
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
            removed_annots += 1
    if removed_widgets:
        warn.append(f"pdf: {removed_widgets} form field(s) removed")
    if removed_annots:
        warn.append(f"pdf: {removed_annots} annotation(s)/comment(s) removed")
    try:
        names = list(doc.embfile_names())
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        doc.embfile_del(name)
    if names:
        warn.append(f"pdf: {len(names)} embedded attachment(s) removed")


def _scrub_pdf_links(
    doc: "fitz.Document",
    mapping: list[tuple[str, str]],
    warnings: list[str] | None,
    *,
    drop_http: bool = True,
) -> None:
    """Remove or sanitize external PDF hyperlink targets (page.get_links())."""
    warn = warnings if warnings is not None else []
    removed = scrubbed = 0
    for page in doc:
        for link in list(page.get_links() or []):
            uri = link.get("uri") or ""
            file_target = link.get("file") or ""
            if uri:
                if drop_http or uri.lower().startswith(("mailto:", "tel:")):
                    page.delete_link(link)
                    removed += 1
                else:
                    redacted = scrub_external_url(uri, mapping)
                    if redacted != uri:
                        link["uri"] = redacted
                        page.update_link(link)
                        scrubbed += 1
            elif file_target:
                page.delete_link(link)
                removed += 1
    if removed:
        warn.append(f"pdf: {removed} external link(s) removed")
    if scrubbed:
        warn.append(f"pdf: {scrubbed} external link target(s) scrubbed")


def _line_chars(page: "fitz.Page") -> list[list[tuple[str, "fitz.Rect"]]]:
    """Per-line non-space characters with their rects, in reading order."""
    lines: list[list[tuple[str, fitz.Rect]]] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            chars: list[tuple[str, fitz.Rect]] = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    char = ch.get("c", "")
                    if not char or char.isspace():
                        continue
                    chars.append((char, fitz.Rect(ch["bbox"])))
            if chars:
                lines.append(chars)
    return lines


def _page_char_index(
    page: "fitz.Page",
) -> tuple[str, list["fitz.Rect"], list[int]]:
    """Whitespace-free text of a page plus each character's rect and line number.

    Court PDFs wrap a long party name across a line break, and `Page.search_for`
    only matches inside one line, so that occurrence survives the replacement.
    Indexing characters lets us find the wrapped ones and redact per line.
    """
    parts: list[str] = []
    rects: list[fitz.Rect] = []
    rows: list[int] = []
    for row, line in enumerate(_line_chars(page)):
        for char, rect in line:
            parts.append(char)
            rects.append(rect)
            rows.append(row)
    return "".join(parts), rects, rows


def _wrapped_rects(
    index: tuple[str, list["fitz.Rect"], list[int]],
    original: str,
    *,
    allow_single_line: bool = False,
) -> list[tuple["fitz.Rect", bool]]:
    """Rects for occurrences of `original` that span a line break.

    Returns ``[(rect, carries_replacement_text), ...]`` so the caller paints the
    alias once, on the first line of the wrapped run.
    """
    text, rects, rows = index
    target = "".join(original.split())
    if len(target) < 4:
        return []
    out: list[tuple[fitz.Rect, bool]] = []
    start = text.find(target)
    while start != -1:
        span_rows = rows[start : start + len(target)]
        if len(span_rows) == len(target):
            unique_rows = sorted(set(span_rows))
            if len(unique_rows) > 1 or allow_single_line:
                for n, row in enumerate(unique_rows):
                    line_rects = [
                        rects[i] for i in range(start, start + len(target)) if rows[i] == row
                    ]
                    union = fitz.Rect(line_rects[0])
                    for rect in line_rects[1:]:
                        union |= rect
                    out.append((union, n == 0))
        start = text.find(target, start + 1)
    return out


def _overlaps_claimed(rect: "fitz.Rect", claimed: list["fitz.Rect"]) -> bool:
    area = max(rect.width * rect.height, 0.01)
    for prev in claimed:
        inter = rect & prev
        if inter.width > 0 and inter.height > 0:
            if (inter.width * inter.height) / area > 0.35:
                return True
    return False


def _restoration_obstacles(page: fitz.Page) -> list[fitz.Rect]:
    occupied = [fitz.Rect(word[:4]) for word in page.get_text("words")]
    occupied.extend(fitz.Rect(image["bbox"]) for image in page.get_image_info())
    white = (1.0, 1.0, 1.0)
    for path in page.get_drawings():
        if any(path.get(key) not in (None, white) for key in ("fill", "color")):
            pad = (path.get("width") or 0) / 2
            occupied.append(path["rect"] + (-pad, -pad, pad, pad))
    return occupied


def _horizontal_text_chars(page: fitz.Page) -> list[list[tuple[str, fitz.Rect, float]]]:
    flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
    return [
        [
            (char["c"], fitz.Rect(char["bbox"]), char["origin"][1])
            for span in line["spans"]
            for char in span["chars"]
            if not char["c"].isspace()
        ]
        for block in page.get_text("rawdict", flags=flags)["blocks"]
        for line in block.get("lines", ())
        if line["dir"] == (1, 0)
    ]


def _placeholder_baseline(
    lines: list[list[tuple[str, fitz.Rect, float]]], rect: fitz.Rect, placeholder: str,
) -> float | None:
    target = re.compile(re.escape("".join(placeholder.split())), re.IGNORECASE)
    for line in lines:
        chars = [
            (char, y) for char, bbox, y in line
            if rect.contains((bbox.tl + bbox.br) / 2)
        ]
        match = target.search("".join(char for char, _ in chars))
        if match is not None and chars:
            return chars[match.start()][1]
    return None


def _insert_fitted_text(
    page: fitz.Page, rect: fitz.Rect, text: str, occupied: list[fitz.Rect],
    baseline: float | None,
) -> None:
    fontname = _redaction_fontname(text)
    font = fitz.Font(fontname)
    lines = text.splitlines()
    line_height = font.ascender - font.descender
    fontsize = min(9, rect.height / (len(lines) * line_height))
    width = max(fitz.get_text_length(line, fontname, fontsize) for line in lines)
    word_gap = max(1, fitz.get_text_length(" ", fontname, fontsize))
    right = min(rect.x0 + width, (page.rect * page.derotation_matrix).x1)
    for other in occupied:
        if other.x1 > rect.x1 and other.y0 < rect.y1 and other.y1 > rect.y0:
            right = min(right, other.x0 - word_gap)
    right = max(rect.x1, right)
    fitted_width = min(width, right - rect.x0)
    scale_x = fitted_width / width
    min_scale_x = 0.6
    shrinks_fontsize = scale_x < min_scale_x
    if shrinks_fontsize:
        fontsize *= scale_x / min_scale_x
        scale_x = min_scale_x
    y = rect.y0 + (rect.height - len(lines) * line_height * fontsize) / 2 + font.ascender * fontsize
    if shrinks_fontsize and len(lines) == 1 and baseline is not None:
        y = baseline
    for line in lines:
        point = fitz.Point(rect.x0, y)
        page.insert_text(point, line, fontname=fontname, fontsize=fontsize, morph=(point, fitz.Matrix(scale_x, 1)))
        y += line_height * fontsize
    occupied.append(fitz.Rect(rect.x0, rect.y0, rect.x0 + fitted_width, rect.y1))


def redact_pdf(
    input_path: Path,
    output_path: Path,
    mapping: list[tuple[str, str]],
    warnings: list[str] | None = None,
    *,
    preserve_hyperlinks: bool = False,
    preserve_bookmarks: bool = False,
    ocr_images: str = "auto",
    image_categories: set[str] | None = None,
    mode: str = "ai",
    fit_replacements: bool = False,
) -> str:
    warn = warnings if warnings is not None else []
    ordered_mapping = sorted(mapping, key=lambda p: len(p[0]), reverse=True)
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
            claimed_rects: list[fitz.Rect] = []
            fitted_text: list[tuple[fitz.Rect, str, str]] = []
            index: tuple[str, list[fitz.Rect], list[int]] | None = None
            for original, replacement in ordered_mapping:
                if not original:
                    continue
                rects = page.search_for(original)
                # Names and addresses wrapped across a line break: search_for
                # cannot see them, so redact those occurrences per line.
                if index is None:
                    index = _page_char_index(page)
                matches = [(rect, True) for rect in rects]
                matches.extend(_wrapped_rects(index, original, allow_single_line=not rects))
                for rect, carries_text in matches:
                    if _overlaps_claimed(rect, claimed_rects):
                        continue
                    claimed_rects.append(rect)
                    text = replacement if carries_text else ""
                    if fit_replacements and text:
                        fitted_text.append((rect, original, text))
                        text = ""
                    page.add_redact_annot(
                        rect,
                        text=text,
                        fill=(1, 1, 1),
                        text_color=(0, 0, 0),
                        fontsize=9,
                        fontname=_redaction_fontname(replacement),
                    )
            if claimed_rects:
                lines = _horizontal_text_chars(page) if fitted_text else []
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            if fitted_text:
                occupied = claimed_rects + _restoration_obstacles(page)
                for rect, original, text in fitted_text:
                    baseline = _placeholder_baseline(lines, rect, original)
                    _insert_fitted_text(page, rect, text, occupied, baseline)

        _redact_embedded_images(doc, mapping, warn, ocr_images, image_categories)

        meta = dict(doc.metadata or {})
        for key in list(meta.keys()):
            if key in _CLEAR_PDF_META_KEYS:
                meta[key] = ""
                continue
            if key in _SKIP_PDF_META_KEYS:
                continue
            val = meta.get(key)
            if isinstance(val, str) and val:
                meta[key] = scrub_structural_text(apply_mapping_to_text(val, mapping))
        for key in _CLEAR_PDF_META_KEYS:
            meta[key] = ""
        doc.set_metadata(meta)
        _clear_pdf_extras(doc, warnings, preserve_bookmarks=preserve_bookmarks)
        strip_urls = bool(image_categories and "url" in image_categories)
        drop_http = (not preserve_hyperlinks) and (
            mode.lower().strip() != "production" or strip_urls
        )
        _scrub_pdf_links(doc, mapping, warnings, drop_http=drop_http)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), deflate=True, garbage=3)
    finally:
        doc.close()

    return extract_text(output_path)


def _redact_embedded_images(
    doc: "fitz.Document",
    mapping: list[tuple[str, str]],
    warnings: list[str],
    ocr_images: str,
    categories: set[str] | None,
) -> None:
    """OCR raster images on text-layer pages and black-box PII pixels (scan path reuse)."""
    from ..image_scan import ocr_available, ocr_image_hits

    pages_with_images = [p for p in doc if p.get_images(full=True)]
    if not pages_with_images:
        return
    if ocr_images == "off":
        warnings.append(
            f"PDF has embedded images on {len(pages_with_images)} page(s); --ocr-images off — review manually"
        )
        return
    if not ocr_available():
        if ocr_images == "on":
            raise RuntimeError("--ocr-images on requires Tesseract (chi_sim)")
        warnings.append(
            f"PDF has embedded images on {len(pages_with_images)} page(s); Tesseract not installed — "
            "images not OCR-scanned, review manually"
        )
        return
    from ..patterns import categories_for_mode

    cats = categories if categories is not None else categories_for_mode("ai")
    terms = [o for o, _ in mapping]
    total = 0
    for page in pages_with_images:
        page_hits = 0
        for info in page.get_images(full=True):
            xref = info[0]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            try:
                raw = doc.extract_image(xref)["image"]
                hits, w, h, _ = ocr_image_hits(raw, categories=cats, extra_terms=terms)
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"page {page.number + 1} image xref {xref}: OCR failed ({exc})")
                continue
            if not hits or not w or not h:
                continue
            for rect in rects:
                sx, sy = rect.width / w, rect.height / h
                for hit in hits:
                    x0, y0, x1, y1 = hit.box
                    page.add_redact_annot(
                        fitz.Rect(
                            rect.x0 + x0 * sx, rect.y0 + y0 * sy, rect.x0 + x1 * sx, rect.y0 + y1 * sy
                        ),
                        fill=(0, 0, 0),
                    )
                    page_hits += 1
        if page_hits:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
            total += page_hits
    if total:
        warnings.append(
            f"IMAGE-PII: blacked out {total} OCR hit box(es) inside embedded images — spot-check the output"
        )


def create_sample_pdf(path: Path, text: str) -> None:
    """Create a simple text-layer PDF. Prefer a Chinese-capable font when present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()

    fontname = "china-s"
    # Try common Windows / Noto fonts for CJK; fall back to PyMuPDF built-in china-s.
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
                fontname = "china-s"

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
