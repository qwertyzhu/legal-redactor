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

_SHORT_CJK_NAME = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]{2,3}")
_CharIndex = tuple[str, list[fitz.Rect], list[int], list[tuple[float, float, int]]]


def is_short_cjk_name(text: str) -> bool:
    return _SHORT_CJK_NAME.fullmatch("".join(text.split())) is not None


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


def _line_chars(page: "fitz.Page") -> list[tuple[int, list[tuple[str, "fitz.Rect"]]]]:
    """Per-line non-space characters with their rects, in reading order."""
    lines: list[tuple[int, list[tuple[str, fitz.Rect]]]] = []
    raw = page.get_text("rawdict")
    for block_id, block in enumerate(raw.get("blocks", [])):
        for line in block.get("lines", []):
            chars: list[tuple[str, fitz.Rect]] = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    char = ch.get("c", "")
                    if not char or char.isspace():
                        continue
                    chars.append((char, fitz.Rect(ch["bbox"])))
            if chars:
                lines.append((block_id, chars))
    return lines


def _page_char_index(page: "fitz.Page") -> _CharIndex:
    """Whitespace-free page text, character rects and row numbers, and row bounds.

    Court PDFs wrap a long party name across a line break, and `Page.search_for`
    only matches inside one line, so that occurrence survives the replacement.
    Indexing characters lets us find the wrapped ones and redact per line.
    """
    parts: list[str] = []
    rects: list[fitz.Rect] = []
    rows: list[int] = []
    line_info: list[tuple[float, float, int]] = []
    for row, (block_id, line) in enumerate(_line_chars(page)):
        line_info.append((line[0][1].x0, line[-1][1].x1, block_id))
        for char, rect in line:
            parts.append(char)
            rects.append(rect)
            rows.append(row)
    return "".join(parts), rects, rows, line_info


def _short_name_is_local(
    rects: list["fitz.Rect"],
    rows: list[int],
    line_info: list[tuple[float, float, int]],
    start: int,
    end: int,
) -> bool:
    for i in range(start + 1, end):
        prev, curr = rects[i - 1], rects[i]
        width = max(prev.width, curr.width)
        if rows[i] == rows[i - 1]:
            if not 0 <= curr.x0 - prev.x0 <= prev.width + width:
                return False
        else:
            height = max(prev.height, curr.height)
            prev_left, prev_right, prev_block = line_info[rows[i - 1]]
            curr_left, curr_right, curr_block = line_info[rows[i]]
            gap = curr.y0 - prev.y0
            if rows[i] != rows[i - 1] + 1 or not 0 < gap <= 2 * height:
                return False
            if max(prev_left, curr_left) >= min(prev_right, curr_right):
                return False
            indent = abs(curr_left - prev_left)
            if prev_block != curr_block and not (
                width < indent <= 2 * width and gap <= 1.5 * height and curr_left <= prev.x0
            ):
                return False
    return True


def _wrapped_rects(
    index: _CharIndex,
    original: str,
    *,
    allow_single_line: bool = False,
) -> list[list["fitz.Rect"]]:
    """Rects for occurrences of `original` split by whitespace or line breaks.

    Each match has one rect per line.
    """
    text, rects, rows, line_info = index
    target = "".join(original.split())
    short_name = is_short_cjk_name(target)
    if len(target) < 4 and not short_name:
        return []
    out: list[list[fitz.Rect]] = []
    start = text.find(target)
    while start != -1:
        span_rows = rows[start : start + len(target)]
        if not short_name or _short_name_is_local(rects, rows, line_info, start, start + len(target)):
            unique_rows = sorted(set(span_rows))
            if len(unique_rows) > 1 or allow_single_line or short_name:
                match: list[fitz.Rect] = []
                for row in unique_rows:
                    line_rects = [
                        rects[i] for i in range(start, start + len(target)) if rows[i] == row
                    ]
                    union = fitz.Rect(line_rects[0])
                    for rect in line_rects[1:]:
                        union |= rect
                    match.append(union)
                out.append(match)
        start = text.find(target, start + 1)
    return out


def matched_short_names(path: Path, originals: set[str]) -> set[str]:
    pending = set(originals)
    matches: set[str] = set()
    with fitz.open(str(path)) as doc:
        for page in doc:
            index = _page_char_index(page)
            found = {name for name in pending if _wrapped_rects(index, name)}
            matches.update(found)
            pending.difference_update(found)
            if not pending:
                break
    return matches


def _overlaps_claimed(rect: "fitz.Rect", claimed: list["fitz.Rect"]) -> bool:
    area = max(rect.width * rect.height, 0.01)
    for prev in claimed:
        inter = rect & prev
        if inter.width > 0 and inter.height > 0:
            if (inter.width * inter.height) / area > 0.35:
                return True
    return False


def _paint_wrapped_alias(page: "fitz.Page", rects: list["fitz.Rect"], alias: str) -> None:
    lines = alias.splitlines()
    fontname = _redaction_fontname(alias)
    width = max((fitz.get_text_length(line, fontname=fontname, fontsize=1) for line in lines), default=0)
    if not width:
        return
    font = fitz.Font(fontname=fontname)
    lineheight = font.ascender - font.descender
    height = lineheight * len(lines)

    def size(rect: fitz.Rect) -> float:
        return min(9, rect.width / width, rect.height / height)

    rect = max(rects, key=size)
    fontsize = size(rect)
    baseline = rect.y0 + (rect.height - fontsize * height) / 2 + fontsize * font.ascender
    page.insert_text((rect.x0, baseline), lines, fontname=fontname, fontsize=fontsize, lineheight=lineheight)


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
            found_any = False
            claimed_rects: list[fitz.Rect] = []
            wrapped_aliases: list[tuple[list[fitz.Rect], str]] = []
            index: _CharIndex | None = None
            for original, replacement in ordered_mapping:
                if not original:
                    continue
                rects = page.search_for(original)
                for rect in rects:
                    if _overlaps_claimed(rect, claimed_rects):
                        continue
                    claimed_rects.append(rect)
                    page.add_redact_annot(
                        rect,
                        text=replacement,
                        fill=(1, 1, 1),
                        text_color=(0, 0, 0),
                        fontsize=9,
                        fontname=_redaction_fontname(replacement),
                    )
                    found_any = True
                # search_for misses names split by spaces or line breaks.
                if index is None:
                    index = _page_char_index(page)
                for match in _wrapped_rects(index, original, allow_single_line=not rects):
                    paint_after = len(match) > 1 and is_short_cjk_name(original)
                    applied = []
                    for i, rect in enumerate(match):
                        if _overlaps_claimed(rect, claimed_rects):
                            continue
                        claimed_rects.append(rect)
                        applied.append(rect)
                        page.add_redact_annot(
                            rect,
                            text=replacement if i == 0 and not paint_after else "",
                            fill=(1, 1, 1),
                            text_color=(0, 0, 0),
                            fontsize=9,
                            fontname=_redaction_fontname(replacement),
                        )
                        found_any = True
                    if paint_after and applied:
                        wrapped_aliases.append((applied, replacement))
            if found_any:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                for rects, alias in wrapped_aliases:
                    _paint_wrapped_alias(page, rects, alias)

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
