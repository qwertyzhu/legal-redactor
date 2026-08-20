"""DOCX load / text extract / mapped rewrite via OOXML.

Handles track-changes (w:ins / w:del) and run-split entity strings by:
1. accepting insertions and dropping deletions;
2. applying replacements at paragraph granularity.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree

from ..entities import apply_mapping_to_text

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W_NS}
W = "{%s}" % W_NS

# Package parts that may carry visible body text
_TEXT_PART_RE = re.compile(
    r"^word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)


def _local(tag: str) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _flatten_revisions(root: etree._Element) -> None:
    """Accept insertions and discard deletions so visible text is complete."""
    # Drop deletions first
    for el in list(root.iter()):
        if _local(el.tag) == "del":
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    # Unwrap insertions (and moveFrom/moveTo content kept as visible)
    changed = True
    while changed:
        changed = False
        for el in list(root.iter()):
            name = _local(el.tag)
            if name not in {"ins", "moveFrom", "moveTo"}:
                continue
            parent = el.getparent()
            if parent is None:
                continue
            idx = parent.index(el)
            children = list(el)
            for offset, child in enumerate(children):
                parent.insert(idx + offset, child)
            parent.remove(el)
            changed = True
            break


def _paragraph_text(p: etree._Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        if _local(node.tag) == "t":
            parts.append(node.text or "")
        elif _local(node.tag) == "tab":
            parts.append("\t")
        elif _local(node.tag) == "br":
            parts.append("\n")
    return "".join(parts)


def _set_paragraph_text(p: etree._Element, new_text: str) -> None:
    t_nodes = [n for n in p.iter() if _local(n.tag) == "t"]
    if not t_nodes:
        # Build a minimal run
        r = etree.SubElement(p, f"{W}r")
        t = etree.SubElement(r, f"{W}t")
        t.text = new_text
        if new_text[:1].isspace() or new_text[-1:].isspace():
            t.set(f"{{{XML_NS}}}space", "preserve")
        return

    first = t_nodes[0]
    first.text = new_text
    if new_text[:1].isspace() or new_text[-1:].isspace():
        first.set(f"{{{XML_NS}}}space", "preserve")
    else:
        # keep existing xml:space if any
        pass
    for t in t_nodes[1:]:
        t.text = ""


def _process_xml_bytes(data: bytes, mapping: list[tuple[str, str]] | None) -> tuple[bytes, str]:
    root = etree.fromstring(data)
    _flatten_revisions(root)

    texts: list[str] = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        original = _paragraph_text(p)
        if not original:
            continue
        if mapping is None:
            texts.append(original)
            continue
        updated = apply_mapping_to_text(original, mapping)
        texts.append(updated)
        if updated != original:
            _set_paragraph_text(p, updated)

    # Serialize with XML declaration
    out = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    return out, "\n".join(texts)


def extract_text(path: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path, "r") as zin:
        names = [n for n in zin.namelist() if _TEXT_PART_RE.match(n)]
        # document first, then others stable order
        names.sort(key=lambda n: (0 if n.endswith("document.xml") else 1, n))
        for name in names:
            _, text = _process_xml_bytes(zin.read(name), mapping=None)
            if text.strip():
                chunks.append(text)
    return "\n".join(chunks)


def redact_docx(
    input_path: Path,
    output_path: Path,
    mapping: list[tuple[str, str]],
) -> str:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    collected: list[str] = []
    with zipfile.ZipFile(input_path, "r") as zin:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if _TEXT_PART_RE.match(item.filename):
                    data, text = _process_xml_bytes(data, mapping)
                    if text.strip():
                        collected.append(text)
                elif item.filename == "docProps/core.xml":
                    data = _redact_core_props(data, mapping)
                zout.writestr(item, data)

    return "\n".join(collected)


def _redact_core_props(data: bytes, mapping: list[tuple[str, str]]) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for el in root.iter():
        if el.text and isinstance(el.text, str) and el.text.strip():
            el.text = apply_mapping_to_text(el.text, mapping)
        if el.tail and isinstance(el.tail, str) and el.tail.strip():
            el.tail = apply_mapping_to_text(el.tail, mapping)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def create_sample_docx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for block in text.split("\n"):
        doc.add_paragraph(block)
    doc.save(str(path))
