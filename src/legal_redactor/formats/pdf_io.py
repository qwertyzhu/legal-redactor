"""Text-layer PDF redaction via PyMuPDF search + redaction annotations."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from urllib.parse import unquote

import pymupdf as fitz

from ..entities import apply_mapping_to_text
from ..patterns import DEFAULT_PLACEHOLDERS, detect_structural
from . import FIELD_BOUNDARY, scrub_external_url, scrub_structural_text

_CLEAR_PDF_META_KEYS = frozenset({"author", "creator", "producer", "title", "subject", "keywords"})
_SKIP_PDF_META_KEYS = frozenset({"format", "creationDate", "modDate", "encryption", "trapped"})
_STRUCT_TEXT_KEYS = ("Alt", "ActualText", "E", "T")
_PHONE_CATEGORIES = frozenset({"mobile", "landline"})
_HIDDEN_PHONE_TOKEN = re.compile(r"\[PDF(?:手机号|电话)[0-9]+\]")
_INDIRECT_REF = re.compile(r"(\d+) \d+ R")

_SHORT_CJK_NAME = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]{2,3}")
_PDF_SEARCH_FLAGS = (
    fitz.TEXT_DEHYPHENATE | fitz.TEXT_PRESERVE_WHITESPACE
    | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP
)
_TextStyle = tuple[float, float, tuple[float, float]]
_CharIndex = tuple[str, list[fitz.Rect], list[int], list[tuple[float, float, int]], list[_TextStyle]]
_TEXT_ROTATIONS = {(0.0, -1.0): 90, (-1.0, 0.0): 180, (0.0, 1.0): 270}


@dataclass
class _NameRect:
    rect: fitz.Rect
    baseline: float
    font_size: float
    direction: tuple[float, float]


@dataclass
class PDFHiddenPhones:
    structure: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    literal_tokens: list[str] = field(default_factory=list)

    @property
    def requires_digest(self) -> bool:
        return bool(self.structure or self.metadata or self.literal_tokens)


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


def _struct_text_fields(doc: fitz.Document) -> list[tuple[int, str, str]]:
    """(xref, key, value) for alt text, actual text, expansions and titles in the structure tree."""
    kind, root = doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")
    if kind != "xref":
        return []
    fields: list[tuple[int, str, str]] = []
    pending, seen = [int(root.split()[0])], set()
    while pending:
        xref = pending.pop()
        if xref in seen:
            continue
        seen.add(xref)
        kids = doc.xref_object(xref, compressed=True)
        if not kids.startswith("["):
            for key in _STRUCT_TEXT_KEYS:
                kind, value = doc.xref_get_key(xref, key)
                if kind == "string" and value.strip():
                    fields.append((xref, key, value))
            kind, kids = doc.xref_get_key(xref, "K")
            if kind not in ("xref", "array"):
                continue
        # Inline marked-content / object references point at pages and annotations, not children.
        pending.extend(int(num) for num in _INDIRECT_REF.findall(re.sub(r"<<.*?>>", "", kids)))
    return fields


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
        parts.extend(value.strip() for _, _, value in _struct_text_fields(doc))
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


def _next_hidden_phone_token(category: str, taken: set[str], counter: count) -> str:
    label = DEFAULT_PLACEHOLDERS[category][1:-1]
    token = f"[PDF{label}{next(counter)}]"
    while token in taken:
        token = f"[PDF{label}{next(counter)}]"
    return token


def _scrub_struct_text(
    doc: "fitz.Document",
    mapping: list[tuple[str, str]],
    warnings: list[str] | None,
    categories: set[str] | None,
    *,
    capture_hidden_phones: PDFHiddenPhones | None,
    restore_hidden_phones: PDFHiddenPhones | None,
    restoring: bool,
) -> None:
    """Apply `mapping` to tagged-PDF structure text (/Alt, /ActualText, /E, /T).

    Structural PII of the active `categories` is replaced too; restore passes none.
    """
    fields = _struct_text_fields(doc)
    if restoring:
        known_tokens = (
            set(restore_hidden_phones.structure).union(restore_hidden_phones.literal_tokens)
            if restore_hidden_phones is not None else set()
        )
        unknown_tokens = any(
            token not in known_tokens
            for _, _, value in fields
            for token in _HIDDEN_PHONE_TOKEN.findall(value)
        )
        legacy_placeholder = restore_hidden_phones is None and any(
            DEFAULT_PLACEHOLDERS[category] in value
            for _, _, value in fields
            for category in _PHONE_CATEGORIES
        )
        if unknown_tokens or legacy_placeholder:
            raise ValueError("账本缺少 PDF 结构树电话号码的对应记录，请使用正确账本或从原始 PDF 重新脱敏")
    struct_mapping = mapping
    if restore_hidden_phones is not None:
        struct_mapping = [*restore_hidden_phones.structure.items(), *mapping]
    mapped_fields = [
        (xref, key, value, apply_mapping_to_text(value, struct_mapping))
        for xref, key, value in fields
    ]
    scrub_categories = categories or set()
    hidden_phone_categories = scrub_categories & _PHONE_CATEGORIES
    captured: dict[str, str] = {}
    token_counter = count(1)
    taken: set[str] = set()
    if capture_hidden_phones is not None:
        scrub_categories = scrub_categories - _PHONE_CATEGORIES
        literal_tokens = {
            token for _, _, _, mapped in mapped_fields for token in _HIDDEN_PHONE_TOKEN.findall(mapped)
        }
        capture_hidden_phones.literal_tokens = sorted(literal_tokens)
        if hidden_phone_categories:
            taken.update(literal_tokens)
            for _, _, original, _ in mapped_fields:
                taken.update(_HIDDEN_PHONE_TOKEN.findall(original))
    scrubbed = 0
    for xref, key, value, redacted in mapped_fields:
        if scrub_categories:
            redacted = scrub_structural_text(redacted, categories=scrub_categories)
        if capture_hidden_phones is not None and hidden_phone_categories:
            parts: list[str] = []
            last = 0
            for hit in detect_structural(redacted):
                if hit.category not in hidden_phone_categories or not hit.auto_replace:
                    continue
                if hit.text not in captured:
                    token = _next_hidden_phone_token(hit.category, taken, token_counter)
                    captured[hit.text] = token
                    capture_hidden_phones.structure[token] = hit.text
                parts.extend((redacted[last:hit.start], captured[hit.text]))
                last = hit.end
            if parts:
                parts.append(redacted[last:])
                redacted = "".join(parts)
        if redacted != value:
            doc.xref_set_key(xref, key, fitz.get_pdf_str(redacted))
            scrubbed += 1
    if scrubbed and warnings is not None:
        warnings.append(f"pdf: {scrubbed} structure tree text field(s) scrubbed")


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


def _line_chars(page: "fitz.Page") -> list[tuple[int, list[tuple[str, _NameRect]]]]:
    """Per-line non-space characters with their layout, in reading order."""
    lines: list[tuple[int, list[tuple[str, _NameRect]]]] = []
    raw = page.get_text("rawdict")
    for block_id, block in enumerate(raw.get("blocks", [])):
        for line in block.get("lines", []):
            chars: list[tuple[str, _NameRect]] = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    char = ch.get("c", "")
                    if not char or char.isspace():
                        continue
                    chars.append((char, _NameRect(
                        fitz.Rect(ch["bbox"]), ch["origin"][1], span["size"], line["dir"],
                    )))
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
    text_style: list[_TextStyle] = []
    for row, (block_id, line) in enumerate(_line_chars(page)):
        line_info.append((line[0][1].rect.x0, line[-1][1].rect.x1, block_id))
        for char, glyph in line:
            parts.append(char)
            rects.append(glyph.rect)
            rows.append(row)
            text_style.append((glyph.baseline, glyph.font_size, glyph.direction))
    return "".join(parts), rects, rows, line_info, text_style


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
) -> list[list[_NameRect]]:
    """Rects for occurrences of `original` split by whitespace or line breaks.

    Each match has one rect per line.
    """
    text, rects, rows, line_info, text_style = index
    target = "".join(original.split())
    short_name = is_short_cjk_name(target)
    if len(target) < 4 and not short_name:
        return []
    out: list[list[_NameRect]] = []
    start = text.find(target)
    while start != -1:
        span_rows = rows[start : start + len(target)]
        if not short_name or _short_name_is_local(rects, rows, line_info, start, start + len(target)):
            unique_rows = sorted(set(span_rows))
            if len(unique_rows) > 1 or allow_single_line or short_name:
                match: list[_NameRect] = []
                for row in unique_rows:
                    line_indices = [
                        i for i in range(start, start + len(target)) if rows[i] == row
                    ]
                    first = line_indices[0]
                    union = fitz.Rect(rects[first])
                    for i in line_indices[1:]:
                        union |= rects[i]
                    match.append(_NameRect(union, *text_style[first]))
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


def _paint_split_alias(page: "fitz.Page", rects: list[_NameRect], alias: str) -> None:
    lines = alias.splitlines()
    fontname = _redaction_fontname(alias)
    width = max((fitz.get_text_length(line, fontname=fontname, fontsize=1) for line in lines), default=0)
    if not width:
        return
    font = fitz.Font(fontname=fontname)
    lineheight = font.ascender - font.descender
    height = lineheight * len(lines)

    def layout(slot: _NameRect) -> tuple[float, tuple[float, float], int]:
        rect = slot.rect
        rotation = _TEXT_ROTATIONS.get(slot.direction, 0)
        if slot.direction == (1.0, 0.0) and len(lines) == 1:
            return min(slot.font_size, rect.width / width), (rect.x0, slot.baseline), rotation
        advance, cross = (rect.height, rect.width) if rotation in (90, 270) else (rect.width, rect.height)
        size = min(slot.font_size, 0.99 * advance / width, cross / height)
        offset = (cross - size * height) / 2 + size * font.ascender
        origin = {
            0: (rect.x0, rect.y0 + offset),
            90: (rect.x0 + offset, rect.y1),
            180: (rect.x1, rect.y1 - offset),
            270: (rect.x1 - offset, rect.y0),
        }[rotation]
        return size, origin, rotation

    font_size, origin, rotation = max(map(layout, rects), key=lambda placement: placement[0])
    if font_size > 0:
        page.insert_text(origin, lines, fontsize=font_size, lineheight=lineheight, fontname=fontname, rotate=rotation)


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


def _searchable_digits(text_page: fitz.TextPage) -> str | None:
    text = text_page.extractText()
    if not text.isascii() and any(unicodedata.category(char) == "Cn" for char in text):
        return None
    # A superset of native digit matches, allowing compatibility digits and
    # matches spanning any separators without having to reproduce MuPDF search.
    return re.sub(r"[^0-9]", "", unicodedata.normalize("NFKD", text))


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
    capture_hidden_phones: PDFHiddenPhones | None = None,
    restore_hidden_phones: PDFHiddenPhones | None = None,
    restoring: bool = False,
) -> str:
    warn = warnings if warnings is not None else []
    ordered_mapping = sorted((pair for pair in mapping if pair[0]), key=lambda p: len(p[0]), reverse=True)
    digit_targets = {original for original, _ in ordered_mapping if original.isascii() and original.isdecimal()}
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
            split_aliases: list[tuple[list[_NameRect], str]] = []
            fitted_text: list[tuple[fitz.Rect, str, str]] = []
            index: _CharIndex | None = None
            search_page = page.get_textpage(flags=_PDF_SEARCH_FLAGS) if ordered_mapping else None
            digits = _searchable_digits(search_page) if search_page is not None and digit_targets else None

            def add_redaction(rect: fitz.Rect, original: str, text: str) -> bool:
                if _overlaps_claimed(rect, claimed_rects):
                    return False
                claimed_rects.append(rect)
                if fit_replacements and text:
                    fitted_text.append((rect, original, text))
                    text = ""
                page.add_redact_annot(
                    rect,
                    text=text,
                    fill=(1, 1, 1),
                    text_color=(0, 0, 0),
                    fontsize=9,
                    fontname=_redaction_fontname(text),
                )
                return True

            for original, replacement in ordered_mapping:
                rects = (
                    page.search_for(original, textpage=search_page)
                    if digits is None or original not in digit_targets or original in digits
                    else []
                )
                for rect in rects:
                    add_redaction(rect, original, replacement)
                # search_for misses names split by spaces or line breaks.
                if index is None:
                    index = _page_char_index(page)
                paint_after = not fit_replacements and is_short_cjk_name(original)
                for match in _wrapped_rects(index, original, allow_single_line=not rects):
                    applied: list[_NameRect] = []
                    for i, slot in enumerate(match):
                        text = replacement if i == 0 and not paint_after else ""
                        if add_redaction(slot.rect, original, text):
                            applied.append(slot)
                    if paint_after and applied:
                        split_aliases.append((applied, replacement))
            if claimed_rects:
                lines = _horizontal_text_chars(page) if fitted_text else []
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                for slots, alias in split_aliases:
                    _paint_split_alias(page, slots, alias)
            if fitted_text:
                occupied = claimed_rects + _restoration_obstacles(page)
                for rect, original, text in fitted_text:
                    baseline = _placeholder_baseline(lines, rect, original)
                    _insert_fitted_text(page, rect, text, occupied, baseline)

        _redact_embedded_images(doc, mapping, warn, ocr_images, image_categories)

        meta = dict(doc.metadata or {})
        hidden_phone_categories = (image_categories or set()) & _PHONE_CATEGORIES
        for key in list(meta.keys()):
            if key in _CLEAR_PDF_META_KEYS:
                if capture_hidden_phones is not None and hidden_phone_categories and any(
                    hit.category in hidden_phone_categories and hit.auto_replace
                    for hit in detect_structural(meta[key] or "")
                ):
                    capture_hidden_phones.metadata[key] = meta[key]
                meta[key] = ""
                continue
            if key in _SKIP_PDF_META_KEYS:
                continue
            val = meta.get(key)
            if isinstance(val, str) and val:
                meta[key] = scrub_structural_text(apply_mapping_to_text(val, mapping))
        for key in _CLEAR_PDF_META_KEYS:
            meta[key] = ""
        if restore_hidden_phones is not None:
            meta.update(restore_hidden_phones.metadata)
        doc.set_metadata(meta)
        _scrub_struct_text(
            doc, mapping, warnings, image_categories,
            capture_hidden_phones=capture_hidden_phones,
            restore_hidden_phones=restore_hidden_phones,
            restoring=restoring,
        )
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
