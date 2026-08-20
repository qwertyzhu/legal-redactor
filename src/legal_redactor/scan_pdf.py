"""Scanned-PDF structural redaction via OCR word boxes (production court path).

Draws black redaction boxes on the original page images for structural hits
(id/mobile/landline/email/bank/uscc). Party names are intentionally not
auto-blacked (production default).

Requires local Tesseract. Experimental but usable for phone/account cover-up.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

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


@dataclass
class ScanRedactResult:
    input_path: Path
    output_path: Path
    mode: str
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
) -> ScanRedactResult:
    """Black-box structural PII on a scanned PDF; keep visual layout."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".pdf":
        raise ValueError("scanned redaction output must be .pdf")

    cats = categories_for_mode(mode, keep_categories=keep_categories, extra_categories=extra_categories)
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
                boxes = _collect_boxes(words, cats)
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
                "categories": sorted(cats),
                "count": len(all_hits),
                "hits": all_hits,
                "warning": (
                    "Best-effort OCR coordinate redaction. Human page-flip review required. "
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
        hits=all_hits,
        hits_path=hits_path,
    )
