"""Scanned-PDF redaction via OCR word boxes and reviewed party regions.

Draws black redaction boxes on the original page images for structural hits
(id/mobile/landline/email/bank/uscc). The optional whole-party path redacts
only the user-selected party's confirmed identifiers plus reviewed regions
such as signatures and complete seals.

Requires local Tesseract. Experimental but usable for phone/account cover-up.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz

from .ocr_engine import find_tesseract, find_tessdata
from .patterns import categories_for_mode

BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LAND_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*[.,]?\s*[A-Za-z]{2,}")
ID18_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
USCC_RE = re.compile(r"(?<![A-Z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Z0-9])")

PARTY_SCOPES = {"a", "b", "both"}


def _hits_in(text: str, cats: set[str]) -> list[tuple[str, str]]:
    t = text or ""
    out: list[tuple[str, str]] = []
    bank_spans: list[tuple[int, int]] = []
    if "bank_account" in cats:
        for m in BANK_RE.finditer(t):
            out.append(("bank_account", m.group(0)))
            bank_spans.append((m.start(), m.end()))

    def covered(a: int, b: int) -> bool:
        return any(a >= s and b <= e for s, e in bank_spans)

    if "mobile" in cats:
        for m in MOBILE_RE.finditer(t):
            if not covered(m.start(), m.end()):
                out.append(("mobile", m.group(0)))
    if "landline" in cats:
        for m in LAND_RE.finditer(t):
            out.append(("landline", m.group(0)))
    if "email" in cats:
        for m in EMAIL_RE.finditer(t):
            out.append(("email", re.sub(r"\s+", "", m.group(0))))
    if "id_card" in cats:
        for m in ID18_RE.finditer(t):
            out.append(("id_card", m.group(0)))
    if "uscc" in cats:
        for m in USCC_RE.finditer(t):
            out.append(("uscc", m.group(0)))
    return out


def _ocr_words(
    image_path: Path,
    *,
    tesseract: str,
    tessdata: str | None,
    lang: str,
    psm: int,
) -> list[dict[str, Any]]:
    env = os.environ.copy()
    if tessdata:
        env["TESSDATA_PREFIX"] = tessdata
    base = str(image_path.with_suffix(""))
    cmd = [
        tesseract,
        str(image_path),
        base,
        "-l",
        lang,
        "--psm",
        str(psm),
        "-c",
        "tessedit_create_tsv=1",
    ]
    subprocess.run(cmd, capture_output=True, env=env, timeout=180, check=False)
    tsv = Path(base + ".tsv")
    if not tsv.is_file():
        return []
    rows: list[dict[str, Any]] = []
    lines = tsv.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        try:
            if int(float(row.get("level", 0))) != 5:
                continue
            if float(row.get("conf", -1)) < 0:
                continue
            txt = row.get("text") or ""
            if not txt.strip():
                continue
            rows.append(
                {
                    "left": int(row["left"]),
                    "top": int(row["top"]),
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                    "text": txt,
                    "block_num": int(row.get("block_num", 0)),
                    "par_num": int(row.get("par_num", 0)),
                    "line_num": int(row.get("line_num", 0)),
                    "word_num": int(row.get("word_num", 0)),
                }
            )
        except Exception:
            continue
    try:
        tsv.unlink()
    except OSError:
        pass
    return rows


def _collect_boxes(words: list[dict[str, Any]], cats: set[str]) -> list[tuple[float, float, float, float, str, str]]:
    boxes: list[tuple[float, float, float, float, str, str]] = []
    for w in words:
        hs = _hits_in(w["text"], cats)
        if hs:
            boxes.append(
                (
                    w["left"] - 4,
                    w["top"] - 4,
                    w["left"] + w["width"] + 4,
                    w["top"] + w["height"] + 4,
                    hs[0][0],
                    hs[0][1],
                )
            )
    ws = sorted(words, key=lambda w: (w["top"] // 12, w["left"]))
    for a in range(len(ws)):
        for b in range(a + 1, min(a + 7, len(ws))):
            run = ws[a : b + 1]
            if max(x["top"] for x in run) - min(x["top"] for x in run) > 16:
                break
            ok = True
            for u, v in zip(run, run[1:]):
                if v["left"] - (u["left"] + u["width"]) > 60:
                    ok = False
                    break
            if not ok:
                break
            joined = "".join(x["text"] for x in run)
            hs = _hits_in(joined, cats)
            if hs and b > a:
                boxes.append(
                    (
                        min(x["left"] for x in run) - 4,
                        min(x["top"] for x in run) - 4,
                        max(x["left"] + x["width"] for x in run) + 4,
                        max(x["top"] + x["height"] for x in run) + 4,
                        hs[0][0],
                        hs[0][1],
                    )
                )
    uniq: list[tuple[float, float, float, float, str, str]] = []
    for b in boxes:
        if any(abs(b[0] - u[0]) < 6 and abs(b[1] - u[1]) < 6 and abs(b[2] - u[2]) < 6 for u in uniq):
            continue
        uniq.append(b)
    return uniq


def _normalize_identifier(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


def _load_party_spec(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read party spec: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("parties"), dict):
        raise ValueError("party spec must contain an object named 'parties'")
    for key in data["parties"]:
        if key not in {"a", "b"}:
            raise ValueError("party spec keys must be 'a' and/or 'b'")
    return data


def _selected_party_keys(scope: str) -> tuple[str, ...]:
    if scope not in PARTY_SCOPES:
        raise ValueError("redact_party must be one of: a, b, both")
    return ("a", "b") if scope == "both" else (scope,)


def _party_entries(
    spec: dict[str, Any], scope: str
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    identifiers: list[dict[str, str]] = []
    regions: list[dict[str, Any]] = []
    parties = spec["parties"]
    for key in _selected_party_keys(scope):
        party = parties.get(key)
        if not isinstance(party, dict):
            raise ValueError(f"party spec is missing selected party '{key}'")
        raw_identifiers = party.get("identifiers", [])
        raw_regions = party.get("regions", [])
        if not isinstance(raw_identifiers, list) or not isinstance(raw_regions, list):
            raise ValueError(f"party '{key}' identifiers and regions must be arrays")
        for item in raw_identifiers:
            if isinstance(item, str):
                text = item
                category = "identifier"
            elif isinstance(item, dict):
                text = item.get("text")
                category = item.get("category", "identifier")
            else:
                raise ValueError(f"party '{key}' identifier must be a string or object")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"party '{key}' identifier text must be non-empty")
            identifiers.append(
                {"party": key, "text": text, "category": str(category or "identifier")}
            )
        for region in raw_regions:
            if not isinstance(region, dict):
                raise ValueError(f"party '{key}' region must be an object")
            rect = region.get("rect")
            if (
                not isinstance(rect, list)
                or len(rect) != 4
                or not all(isinstance(v, (int, float)) for v in rect)
            ):
                raise ValueError(f"party '{key}' region rect must have four numbers")
            x0, y0, x1, y1 = (float(v) for v in rect)
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError(
                    f"party '{key}' region rect must use normalized 0..1 coordinates"
                )
            page = region.get("page")
            if not isinstance(page, int) or page < 1:
                raise ValueError(f"party '{key}' region page must be a positive integer")
            regions.append({**region, "party": key, "rect": [x0, y0, x1, y1]})
    if not identifiers and not regions:
        raise ValueError("selected party has no identifiers or reviewed regions")
    return identifiers, regions


def _collect_identifier_boxes(
    words: list[dict[str, Any]], identifiers: list[dict[str, str]]
) -> list[tuple[float, float, float, float, str, str]]:
    """Locate confirmed identifiers, including those split across OCR words/lines."""
    boxes: list[tuple[float, float, float, float, str, str]] = []
    usable = [word for word in words if _normalize_identifier(str(word.get("text", "")))]
    for identifier in identifiers:
        target = _normalize_identifier(identifier["text"])
        if not target:
            continue
        category = f"party_{identifier['party']}_{identifier['category']}"
        for start in range(len(usable)):
            joined = ""
            spans: list[tuple[dict[str, Any], int, int]] = []
            for word in usable[start : start + 32]:
                piece = _normalize_identifier(str(word.get("text", "")))
                begin = len(joined)
                joined += piece
                spans.append((word, begin, len(joined)))
                found = joined.find(target)
                if found >= 0:
                    end = found + len(target)
                    selected = [w for w, a, b in spans if a < end and b > found]
                    by_line: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
                    for selected_word in selected:
                        line_key = (
                            int(selected_word.get("block_num", 0)),
                            int(selected_word.get("par_num", 0)),
                            int(selected_word.get("line_num", 0)),
                        )
                        by_line.setdefault(line_key, []).append(selected_word)
                    for line_words in by_line.values():
                        boxes.append(
                            (
                                min(w["left"] for w in line_words) - 6,
                                min(w["top"] for w in line_words) - 6,
                                max(w["left"] + w["width"] for w in line_words) + 6,
                                max(w["top"] + w["height"] for w in line_words) + 6,
                                category,
                                identifier["text"],
                            )
                        )
                    break
                if len(joined) > len(target) + 24:
                    break
    uniq: list[tuple[float, float, float, float, str, str]] = []
    for box in boxes:
        if any(
            box[4] == other[4]
            and abs(box[0] - other[0]) < 6
            and abs(box[1] - other[1]) < 6
            and abs(box[2] - other[2]) < 6
            and abs(box[3] - other[3]) < 6
            for other in uniq
        ):
            continue
        uniq.append(box)
    return uniq


def _collect_region_boxes(
    regions: list[dict[str, Any]], page_number: int, width: int, height: int
) -> list[tuple[float, float, float, float, str, str]]:
    boxes: list[tuple[float, float, float, float, str, str]] = []
    for region in regions:
        if region["page"] != page_number:
            continue
        x0, y0, x1, y1 = region["rect"]
        kind = str(region.get("category") or "reviewed_region")
        label = str(region.get("note") or kind)
        boxes.append(
            (
                x0 * width,
                y0 * height,
                x1 * width,
                y1 * height,
                f"party_{region['party']}_{kind}",
                label,
            )
        )
    return boxes


@dataclass
class ScanRedactResult:
    input_path: Path
    output_path: Path
    mode: str
    party_scope: str | None = None
    hits: list[dict[str, Any]] = field(default_factory=list)
    hits_path: Path | None = None

    @property
    def ok(self) -> bool:
        # Structural cover-up is best-effort; "ok" means ran and wrote output.
        return self.output_path.is_file()


def redact_scanned_pdf(
    input_path: Path,
    output_path: Path,
    *,
    mode: str = "production",
    dpi: int = 200,
    lang: str = "chi_sim+eng",
    psm: int = 6,
    keep_categories: set[str] | None = None,
    extra_categories: set[str] | None = None,
    tesseract_cmd: str | Path | None = None,
    tessdata_dir: str | Path | None = None,
    work_dir: Path | None = None,
    redact_party: str | None = None,
    party_spec_path: Path | None = None,
    also_redact_structural_all: bool = False,
) -> ScanRedactResult:
    """Black-box structural PII or a reviewed whole party; keep visual layout."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".pdf":
        raise ValueError("scanned redaction output must be .pdf")

    if redact_party is None and party_spec_path is not None:
        raise ValueError("--party-spec requires --redact-party")
    party_identifiers: list[dict[str, str]] = []
    party_regions: list[dict[str, Any]] = []
    if redact_party is not None:
        if party_spec_path is None:
            raise ValueError("--redact-party requires --party-spec")
        party_identifiers, party_regions = _party_entries(
            _load_party_spec(Path(party_spec_path)), redact_party
        )
    cats = categories_for_mode(
        mode, keep_categories=keep_categories, extra_categories=extra_categories
    )
    # Whole-party selection must not silently redact the unselected party's
    # phone/account/email. Legacy structural-all behavior remains available.
    if redact_party is not None and not also_redact_structural_all:
        cats = set()
    tesseract = find_tesseract(tesseract_cmd)
    tessdata = find_tessdata(tessdata_dir)

    doc = fitz.open(str(input_path))
    all_hits: list[dict[str, Any]] = []
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        with tempfile.TemporaryDirectory(prefix="legal-redactor-scan-") as tmp:
            tmp_path = Path(tmp)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_path = tmp_path / f"page_{i + 1:04d}.png"
                pix.save(str(img_path))
                words = _ocr_words(
                    img_path, tesseract=tesseract, tessdata=tessdata, lang=lang, psm=psm
                )
                boxes = _collect_boxes(words, cats) if cats else []
                if redact_party is not None:
                    boxes.extend(_collect_identifier_boxes(words, party_identifiers))
                    boxes.extend(
                        _collect_region_boxes(
                            party_regions, i + 1, width=pix.width, height=pix.height
                        )
                    )
                sx = page.rect.width / pix.width
                sy = page.rect.height / pix.height
                for x0, y0, x1, y1, cat, lab in boxes:
                    rect = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    all_hits.append(
                        {
                            "page": i + 1,
                            "category": cat,
                            "label": lab,
                            "rect": [round(v, 1) for v in rect],
                        }
                    )
                if boxes:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), deflate=True, garbage=3)
    finally:
        doc.close()

    wd = Path(work_dir) if work_dir else output_path.parent
    wd.mkdir(parents=True, exist_ok=True)
    hits_path = wd / f"{output_path.stem}.bbox_hits.json"
    hits_path.write_text(
        json.dumps(
            {
                "mode": mode,
                "party_scope": redact_party,
                "categories": sorted({hit["category"] for hit in all_hits}),
                "count": len(all_hits),
                "hits": all_hits,
                "warning": (
                    "Best-effort OCR coordinate redaction. Human page-flip review required. "
                    "Whole-party seals/signatures require reviewed regions. "
                    "bbox_hits.json may contain sensitive strings — keep local."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ScanRedactResult(
        input_path=input_path,
        output_path=output_path,
        mode=mode,
        party_scope=redact_party,
        hits=all_hits,
        hits_path=hits_path,
    )
