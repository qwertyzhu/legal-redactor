"""DOCX load / text extract / mapped rewrite via OOXML.

Handles track-changes (w:ins / w:del) and run-split entity strings by:
1. accepting insertions and dropping deletions;
2. applying replacements at run granularity when possible, else paragraph level.
Also cleans core/app/custom props, custom XML data bound to content controls,
author/rsid attributes, and mailto/external rels.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote

from docx import Document
from lxml import etree

from ..entities import apply_mapping_to_text, find_mapping_spans
from ..patterns import categories_for_mode
from . import FIELD_BOUNDARY, scrub_external_url, scrub_structural_text

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W_NS}
W = "{%s}" % W_NS

# Package parts that may carry visible body text
_TEXT_PART_RE = re.compile(
    r"^word/(document|header\d+|footer\d+|footnotes|endnotes|comments|(?:charts|diagrams)/[^/]+)\.xml$"
)
# Custom XML data store parts; content controls bound to them re-display these values on open
_CUSTOM_XML_ITEM_RE = re.compile(r"^customXml/item\d+\.xml$")
_CHART_NAMESPACES = (
    "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "http://purl.oclc.org/ooxml/drawingml/chart",
)
_CHART_VALUE_TAGS = {f"{{{ns}}}v" for ns in _CHART_NAMESPACES}
_CHART_NUMERIC_TAGS = tuple(
    f"{{{ns}}}{tag}" for ns in _CHART_NAMESPACES for tag in ("numCache", "numLit")
)
_CHART_LABEL_TAGS = tuple(f"{{{ns}}}{tag}" for ns in _CHART_NAMESPACES for tag in ("cat", "tx"))
_RSID_ATTR_RE = re.compile(r"^rsid", re.IGNORECASE)
_HYPERLINK_FIELD_RE = re.compile(r'(?i)(\bHYPERLINK\s+")([^"]+)(")')


def _scrub_hyperlink_instr(
    instr: str, mapping: list[tuple[str, str]], *, drop_http: bool
) -> str:
    def _repl(match: re.Match[str]) -> str:
        prefix, target, suffix = match.group(1), match.group(2), match.group(3)
        if target.lower().startswith(("mailto:", "tel:")):
            return f"{prefix}#{suffix}"
        if target.lower().startswith(("http://", "https://")) or "://" in target:
            if drop_http:
                return f"{prefix}#{suffix}"
            return f"{prefix}{scrub_external_url(target, mapping)}{suffix}"
        return match.group(0)

    return _HYPERLINK_FIELD_RE.sub(_repl, instr)


def _local(tag: str) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


REVISION_MODES = ("accept", "reject", "keep-and-redact")


def _flatten_revisions(root: etree._Element, revisions: str = "accept") -> None:
    """Resolve tracked changes.

    accept: keep insertions, drop deletions (default, historic behaviour).
    reject: drop insertions, restore deletions (w:delText -> w:t).
    keep-and-redact: leave w:ins / w:del in place; text inside both is redacted.
    """
    if revisions == "keep-and-redact":
        return
    if revisions not in REVISION_MODES:
        raise ValueError(f"revisions must be one of {REVISION_MODES}")
    drop = {"del", "moveFrom"} if revisions == "accept" else {"ins", "moveTo"}
    keep = {"ins", "moveTo"} if revisions == "accept" else {"del", "moveFrom"}
    for el in list(root.iter()):
        if _local(el.tag) in drop:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    if revisions == "reject":
        for el in root.iter():
            if _local(el.tag) == "delText":
                el.tag = f"{W}t"
            elif _local(el.tag) == "delInstrText":
                el.tag = f"{W}instrText"
    for el in [node for node in root.iter() if _local(node.tag) in keep]:
        parent = el.getparent()
        if parent is not None:
            for child in list(el):
                el.addprevious(child)
            parent.remove(el)


def _scrub_authors_and_rsids(root: etree._Element) -> None:
    for el in root.iter():
        # Attribute keys may be Clark notation
        for attr in list(el.attrib):
            local = _local(attr)
            if local in ("author", "lastModifiedBy"):
                el.attrib[attr] = "作者"
            elif local == "initials":
                el.attrib[attr] = "作"
            elif _RSID_ATTR_RE.match(local):
                del el.attrib[attr]


def _revision_owner(node: etree._Element) -> etree._Element | None:
    for anc in node.iterancestors():
        if _local(anc.tag) in ("ins", "del", "moveFrom", "moveTo"):
            return anc
        if _local(anc.tag) == "p":
            return None
    return None


def _own_iter(p: etree._Element):
    """Descendants of p, skipping paragraphs nested in text boxes (visited on their own)."""
    for node in p.iter():
        if node is p:
            continue
        nearest = next((a for a in node.iterancestors() if _local(a.tag) == "p"), None)
        if nearest is p:
            yield node


def _paragraph_text(p: etree._Element) -> str:
    parts: list[str] = []
    owner: object = False
    for node in _own_iter(p):
        if _local(node.tag) in ("t", "delText"):
            cur = _revision_owner(node)
            # keep-and-redact: deleted & inserted text must not glue together
            if owner is not False and cur is not owner and parts:
                parts.append("\n")
            owner = cur
            parts.append(node.text or "")
        elif _local(node.tag) == "tab":
            parts.append("\t")
        elif _local(node.tag) == "br":
            parts.append("\n")
    return "".join(parts)


def _preserve_space(t: etree._Element, text: str) -> None:
    if text[:1].isspace() or text[-1:].isspace():
        t.set(f"{{{XML_NS}}}space", "preserve")


def _set_paragraph_text(p: etree._Element, new_text: str) -> None:
    """Collapse paragraph visible text into the first w:t, keeping its run props."""
    t_nodes = [n for n in _own_iter(p) if _local(n.tag) == "t"]
    if not t_nodes:
        r = etree.SubElement(p, f"{W}r")
        t = etree.SubElement(r, f"{W}t")
        t.text = new_text
        _preserve_space(t, new_text)
        return

    first = t_nodes[0]
    first.text = new_text
    _preserve_space(first, new_text)
    for t in t_nodes[1:]:
        t.text = ""


def _segments(p: etree._Element) -> list[list[etree._Element]]:
    """Text nodes grouped the way _paragraph_text joins them: a new group at every
    tab/br and every change of tracked-change owner."""
    groups: list[list[etree._Element]] = []
    cur: list[etree._Element] = []
    owner: object = False
    for node in _own_iter(p):
        tag = _local(node.tag)
        if tag in ("t", "delText"):
            o = _revision_owner(node)
            if owner is not False and o is not owner and cur:
                groups.append(cur)
                cur = []
            owner = o
            cur.append(node)
        elif tag in ("tab", "br") and cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _replace_across_runs(nodes: list[etree._Element], mapping: list[tuple[str, str]]) -> None:
    """Replace entities even when Word split them over runs (陈|建国, 138|12345678).

    The replacement goes into the run where the entity starts, so it keeps that
    run's formatting; the rest of the entity is cut from the following runs.
    Every other character keeps its own run and formatting.
    """
    texts = [n.text or "" for n in nodes]
    offsets: list[int] = []
    pos = 0
    for t in texts:
        offsets.append(pos)
        pos += len(t)
    spans = find_mapping_spans("".join(texts), mapping)
    if not spans:
        return

    def node_at(char: int) -> int:
        i = 0
        while i + 1 < len(offsets) and offsets[i + 1] <= char:
            i += 1
        return i

    for start, end, replacement in reversed(spans):
        i, j = node_at(start), node_at(end - 1)
        a, b = start - offsets[i], end - offsets[j]
        if i == j:
            texts[i] = texts[i][:a] + replacement + texts[i][b:]
            continue
        texts[i] = texts[i][:a] + replacement
        for k in range(i + 1, j):
            texts[k] = ""
        texts[j] = texts[j][b:]
    for node, new in zip(nodes, texts):
        if (node.text or "") != new:
            node.text = new
            _preserve_space(node, new)


def _redact_paragraph(p: etree._Element, mapping: list[tuple[str, str]]) -> tuple[str, bool]:
    """Redact runs in place; return text and whether a revision boundary blocked a replacement."""
    original = _paragraph_text(p)
    if not original:
        return original, False
    updated = apply_mapping_to_text(original, mapping)
    if updated == original:
        return original, False

    t_nodes = [n for n in _own_iter(p) if _local(n.tag) in ("t", "delText")]
    has_revision = any(_local(n.tag) in ("ins", "del") for n in _own_iter(p))
    if t_nodes:
        for group in _segments(p):
            _replace_across_runs(group, mapping)
        after = _paragraph_text(p)
        if after == updated:
            return updated, False
        if has_revision:
            # Collapsing would destroy tracked-change structure; report the
            # per-run result and let residual verify flag what is left.
            return after, True
        # Entity spanned runs (or per-run order differed) — safe collapse.
        _set_paragraph_text(p, updated)
        return updated, False

    _set_paragraph_text(p, updated)
    return updated, False


def _process_xml_bytes(
    name: str,
    data: bytes,
    mapping: list[tuple[str, str]] | None,
    revisions: str = "accept",
    *,
    drop_http: bool = True,
) -> tuple[bytes, str, int]:
    root = etree.fromstring(data)
    _flatten_revisions(root, revisions)
    if mapping is not None:
        _scrub_authors_and_rsids(root)
        for el in root.iter():
            local = _local(el.tag)
            if local in ("instrText", "delInstrText") and el.text and "HYPERLINK" in el.text.upper():
                el.text = _scrub_hyperlink_instr(el.text, mapping, drop_http=drop_http)
            elif local == "fldSimple":
                for attr in list(el.attrib):
                    if _local(attr) == "instr" and "HYPERLINK" in (el.attrib[attr] or "").upper():
                        el.attrib[attr] = _scrub_hyperlink_instr(
                            el.attrib[attr], mapping, drop_http=drop_http
                        )

    texts: list[str] = []
    chart_fields: list[str] = []
    revision_split_count = 0
    chart = name.startswith("word/charts/")
    for node in root.iter():
        if _local(node.tag) == "p":
            if mapping is None:
                text = _paragraph_text(node)
            else:
                text, revision_split = _redact_paragraph(node, mapping)
                if revision_split:
                    revision_split_count += 1
            if text:
                texts.append(text)
        elif chart and node.tag in _CHART_VALUE_TAGS and node.text:
            if (
                next(node.iterancestors(*_CHART_NUMERIC_TAGS), None) is not None
                and next(node.iterancestors(*_CHART_LABEL_TAGS), None) is None
            ):
                continue
            text = node.text
            if mapping is not None:
                updated = apply_mapping_to_text(text, mapping)
                if updated != text:
                    node.text = updated
                text = updated
            if texts:
                chart_fields.append("\n".join(texts))
                texts.clear()
            chart_fields.append(text)

    out = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    text = "\n".join(texts)
    if chart:
        if text.strip():
            chart_fields.append(text)
        if chart_fields:
            text = FIELD_BOUNDARY.join(("", *chart_fields, ""))
    return out, text, revision_split_count


def extract_text(path: Path, revisions: str = "accept") -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path, "r") as zin:
        names = [n for n in zin.namelist() if _TEXT_PART_RE.match(n)]
        names.sort(key=lambda n: (0 if n.endswith("document.xml") else 1, n))
        for name in names:
            _, text, _ = _process_xml_bytes(name, zin.read(name), mapping=None, revisions=revisions)
            if text.strip():
                chunks.append(text)
    return "\n".join(chunks)


_CLEAR_CORE_PROPS = frozenset(
    {"title", "subject", "description", "keywords", "category", "contentStatus"}
)
_SKIP_CORE_PROPS = frozenset({"created", "modified", "lastPrinted", "revision"})


def extract_metadata_and_links(path: Path) -> str:
    """Extract DOCX metadata values and external hyperlink targets for residual verification."""
    chunks: list[str] = []
    with zipfile.ZipFile(path, "r") as zin:
        names = zin.namelist()
        if "docProps/core.xml" in names:
            try:
                root = etree.fromstring(zin.read("docProps/core.xml"))
                for el in root.iter():
                    local = _local(el.tag)
                    if local in _SKIP_CORE_PROPS:
                        continue
                    if el.text and el.text.strip() and el.text.strip() != "作者":
                        chunks.append(el.text.strip())
            except etree.XMLSyntaxError:
                pass
        if "docProps/app.xml" in names:
            try:
                root = etree.fromstring(zin.read("docProps/app.xml"))
                for el in root.iter():
                    if _local(el.tag) in {"Company", "Manager", "CompanyDoc", "HyperlinkBase"}:
                        if el.text and el.text.strip():
                            chunks.append(el.text.strip())
            except etree.XMLSyntaxError:
                pass
        if "docProps/custom.xml" in names:
            try:
                root = etree.fromstring(zin.read("docProps/custom.xml"))
                for el in root.iter():
                    if el.text and el.text.strip():
                        chunks.append(el.text.strip())
            except etree.XMLSyntaxError:
                pass
        for name in sorted(n for n in names if _CUSTOM_XML_ITEM_RE.match(n)):
            try:
                root = etree.fromstring(zin.read(name))
            except etree.XMLSyntaxError:
                continue
            for el in root.iter(etree.Element):
                values = [el.text, *el.attrib.values()]
                chunks.extend(v.strip() for v in values if v and v.strip())
        for name in sorted(n for n in names if n.startswith("word/_rels/") and n.endswith(".rels")):
            try:
                root = etree.fromstring(zin.read(name))
                for el in root.iter():
                    if _local(el.tag) != "Relationship":
                        continue
                    target = (el.get("Target") or "").strip()
                    if not target or target == "#":
                        continue
                    if el.get("TargetMode") == "External" or target.lower().startswith(
                        ("mailto:", "tel:", "http://", "https://")
                    ):
                        chunks.append(target)
                        decoded = unquote(target)
                        if decoded != target:
                            chunks.append(decoded)
            except etree.XMLSyntaxError:
                pass
        for name in sorted(n for n in names if _TEXT_PART_RE.match(n)):
            try:
                root = etree.fromstring(zin.read(name))
                for el in root.iter():
                    local = _local(el.tag)
                    instr_val = ""
                    if local in ("instrText", "delInstrText") and el.text:
                        instr_val = el.text
                    elif local == "fldSimple":
                        for attr, val in el.attrib.items():
                            if _local(attr) == "instr" and val:
                                instr_val = val
                                break
                    if instr_val and "HYPERLINK" in instr_val.upper():
                        for m in _HYPERLINK_FIELD_RE.finditer(instr_val):
                            target = m.group(2).strip()
                            if target and target != "#":
                                chunks.append(target)
                                decoded = unquote(target)
                                if decoded != target:
                                    chunks.append(decoded)
            except etree.XMLSyntaxError:
                pass
    return FIELD_BOUNDARY.join(chunks)


def _redact_core_props(data: bytes, mapping: list[tuple[str, str]]) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for el in root.iter():
        local = _local(el.tag)
        if local in {"creator", "lastModifiedBy"}:
            el.text = "作者"
            continue
        if local in _CLEAR_CORE_PROPS:
            el.text = ""
            continue
        if local in _SKIP_CORE_PROPS:
            continue
        if el.text and isinstance(el.text, str) and el.text.strip():
            el.text = scrub_structural_text(apply_mapping_to_text(el.text, mapping))
        if el.tail and isinstance(el.tail, str) and el.tail.strip():
            el.tail = scrub_structural_text(apply_mapping_to_text(el.tail, mapping))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _redact_custom_xml_item(data: bytes, mapping: list[tuple[str, str]]) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    changed = False
    for el in root.iter(etree.Element):
        if el.text:
            updated = apply_mapping_to_text(el.text, mapping)
            if updated != el.text:
                el.text = updated
                changed = True
        for attr, value in el.attrib.items():
            updated = apply_mapping_to_text(value, mapping)
            if updated != value:
                el.set(attr, updated)
                changed = True
    if not changed:
        return data
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _clean_app_props(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for el in root.iter():
        local = _local(el.tag)
        if local in {"Company", "Manager", "CompanyDoc", "HyperlinkBase"}:
            el.text = ""
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _clean_custom_props(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    # Remove all property children under Properties
    for child in list(root):
        root.remove(child)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _scrub_misc_part(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for el in list(root.iter()):
        if _local(el.tag) in {"rsids", "presenceInfo"}:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    _scrub_authors_and_rsids(root)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _scrub_rels(
    data: bytes,
    mapping: list[tuple[str, str]] | None = None,
    *,
    drop_http: bool = True,
) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    changed = False
    for el in root.iter():
        if _local(el.tag) != "Relationship":
            continue
        target = el.get("Target") or ""
        if target.lower().startswith(("mailto:", "tel:")):
            el.set("Target", "#")
            changed = True
        elif el.get("TargetMode") == "External":
            rel_type = (el.get("Type") or "").lower()
            is_http = target.lower().startswith(("http://", "https://"))
            if drop_http and (is_http or rel_type.endswith("/hyperlink")):
                el.set("Target", "#")
                changed = True
            elif target:
                redacted_target = scrub_external_url(target, mapping)
                if redacted_target != target:
                    el.set("Target", redacted_target)
                    changed = True
    if not changed:
        return data
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def redact_docx(
    input_path: Path,
    output_path: Path,
    mapping: list[tuple[str, str]],
    warnings: list[str] | None = None,
    *,
    preserve_hyperlinks: bool = False,
    preserve_bookmarks: bool = False,  # unused for docx; accepted for API symmetry
    revisions: str = "accept",
    ocr_images: str = "auto",
    image_categories: set[str] | None = None,
    image_pii: list[str] | None = None,
    mode: str = "ai",
) -> str:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    collected: list[str] = []
    revision_split_count = 0
    warn = warnings if warnings is not None else []
    with zipfile.ZipFile(input_path, "r") as zin:
        names = zin.namelist()
        if any(n.startswith("word/media/") for n in names):
            media_warnings, findings = scan_media(
                input_path,
                ocr_images=ocr_images,
                categories=image_categories if image_categories is not None else categories_for_mode("ai"),
                extra_terms=[original for original, _ in mapping],
            )
            warn.extend(media_warnings)
            if image_pii is not None:
                image_pii.extend(findings)
        if any(n.startswith("word/embeddings/") for n in names):
            warn.append("docx contains word/embeddings/* (OLE) — review or remove before sharing")
        if _has_sensitive_field(zin, names):
            warn.append(
                "DOCX-FIELD: 域代码未替换（交叉引用、文件名域）。页码域忽略。导出前看一眼域结果里有没有人名。"
            )
        strip_urls = bool(image_categories and "url" in image_categories)
        drop_http = (not preserve_hyperlinks) and (
            mode.lower().strip() != "production" or strip_urls
        )
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                name = item.filename
                if _TEXT_PART_RE.match(name):
                    data, text, part_revision_splits = _process_xml_bytes(
                        name, data, mapping, revisions, drop_http=drop_http
                    )
                    revision_split_count += part_revision_splits
                    if text.strip():
                        collected.append(text)
                elif name == "docProps/core.xml":
                    data = _redact_core_props(data, mapping)
                elif _CUSTOM_XML_ITEM_RE.match(name):
                    data = _redact_custom_xml_item(data, mapping)
                elif name == "docProps/app.xml":
                    data = _clean_app_props(data)
                elif name == "docProps/custom.xml":
                    data = _clean_custom_props(data)
                elif name.startswith("word/_rels/") and name.endswith(".rels"):
                    data = _scrub_rels(data, mapping, drop_http=drop_http)
                elif name in ("word/settings.xml", "word/people.xml"):
                    data = _scrub_misc_part(data)
                zout.writestr(item, data)

    if revision_split_count:
        warn.append(
            "keep-and-redact: entity spans a tracked-change boundary in "
            f"{revision_split_count} paragraph(s); not collapsed — check residual report"
        )
    return "\n".join(collected)


_BENIGN_FIELD = re.compile(br"(?i)^[\s\\]*(PAGE|NUMPAGES|SECTIONPAGES|DATE|TIME|SEQ|PAGE\s*\\\*[^\s]*)\b")


def _has_sensitive_field(zin: zipfile.ZipFile, names: list[str]) -> bool:
    """True when a Word field is not a page/date/seq field. Those can hide a filename or a name."""
    for name in names:
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        data = zin.read(name)
        if b"instrText" not in data:
            continue
        for match in re.finditer(br"<w:instrText[^>]*>([^<]*)</w:instrText>", data):
            body = match.group(1).strip()
            if body and not _BENIGN_FIELD.match(body):
                return True
    return False


def scan_media(
    path: Path,
    *,
    categories: set[str],
    ocr_images: str = "auto",
    extra_terms: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    """Return embedded-image warnings and the subset reporting detected PII."""
    from ..image_scan import ocr_available, scan_docx_media

    if ocr_images == "off" or not ocr_available():
        why = "--ocr-images off" if ocr_images == "off" else "Tesseract not installed"
        if ocr_images == "on" and why != "--ocr-images off":
            raise RuntimeError("--ocr-images on requires Tesseract (chi_sim) — see skills/legal-document-redactor/references/scanned-pdf.md")
        with zipfile.ZipFile(path) as z:
            media = [n for n in z.namelist() if n.startswith("word/media/")]
        warnings = [
            f"docx contains {len(media)} embedded image(s) ({why}) — not OCR-scanned; "
            "review manually: " + ", ".join(media[:10])
        ] if media else []
        return warnings, []

    items = scan_docx_media(path, categories=categories, extra_terms=extra_terms)
    warnings: list[str] = []
    findings: list[str] = []
    for it in items:
        if it["hits"]:
            kinds = sorted({h["category"] for h in it["hits"]})
            detail = (
                f"{it['name']}: OCR found {len(it['hits'])} hit(s) ({', '.join(kinds)}) — "
                "image NOT modified; replace or crop it before sharing"
            )
            findings.append(detail)
            warnings.append(f"IMAGE-PII {detail}")
        elif not it["ocr"]:
            warnings.append(f"docx image {it['name']} not OCR-scanned (undecodable/tiny/unsupported); review manually")
    if items and not warnings:
        warnings.append(f"docx word/media images OCR-scanned ({len(items)}), no structural PII found — still glance at seals/signatures")
    return warnings, findings


def create_sample_docx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for block in text.split("\n"):
        doc.add_paragraph(block)
    doc.save(str(path))


def create_mixed_run_docx(path: Path, *, plain_prefix: str, bold_secret: str, plain_suffix: str) -> None:
    """Fixture helper: one paragraph with a bold middle run (formatting preservation tests)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    p = doc.add_paragraph()
    p.add_run(plain_prefix)
    bold = p.add_run(bold_secret)
    bold.bold = True
    p.add_run(plain_suffix)
    doc.save(str(path))
