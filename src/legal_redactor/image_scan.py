"""Optional OCR of embedded images + red-seal region drafts.

Everything here degrades gracefully:
- no Tesseract  -> ``ocr_available()`` is False, callers emit a warning instead;
- no OpenCV     -> ``seal_detection_available()`` is False, ``detect_seals`` returns [].

Image hits reuse the scanned-PDF path (Tesseract TSV word boxes + structural
patterns), so the same categories/checksums apply.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp"}
MIN_IMAGE_SIDE = 40  # skip icons/bullets


def ocr_available() -> bool:
    try:
        from .ocr_engine import find_tesseract

        find_tesseract()
        return True
    except Exception:
        return False


def seal_detection_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class ImageHit:
    category: str
    text: str
    box: tuple[float, float, float, float]  # pixel coords in the image

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "text": self.text, "box": list(self.box)}


def _png_bytes(data: bytes) -> tuple[bytes, int, int] | None:
    """Normalise any raster to PNG via PyMuPDF; None when undecodable/tiny."""
    import pymupdf as fitz

    try:
        pix = fitz.Pixmap(data)
    except Exception:
        return None
    if pix.width < MIN_IMAGE_SIDE or pix.height < MIN_IMAGE_SIDE:
        return None
    if pix.alpha or (pix.colorspace and pix.colorspace.n not in (1, 3)):
        pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
    return pix.tobytes("png"), pix.width, pix.height


def ocr_image_hits(
    data: bytes,
    *,
    categories: Iterable[str],
    extra_terms: Iterable[str] = (),
    lang: str = "chi_sim+eng",
    psm: int = 6,
) -> tuple[list[ImageHit], int, int, str]:
    """OCR one raster image. Returns (hits, width, height, ocr_text)."""
    from .ocr_engine import collapse_cjk_spaces, find_tessdata, find_tesseract
    from .scan_pdf import _collect_boxes, _ocr_words

    norm = _png_bytes(data)
    if norm is None:
        return [], 0, 0, ""
    png, w, h = norm
    cats = set(categories)
    with tempfile.TemporaryDirectory(prefix="lr-img-") as td:
        img = Path(td) / "img.png"
        img.write_bytes(png)
        words = _ocr_words(
            img, tesseract=find_tesseract(), tessdata=find_tessdata(), lang=lang, psm=psm
        )
    hits = [ImageHit(c, t, (x0, y0, x1, y1)) for x0, y0, x1, y1, c, t in _collect_boxes(words, cats)]
    terms = [t for t in extra_terms if t and len(t) >= 2]
    if terms:
        for word in words:
            wt = collapse_cjk_spaces(word["text"])
            for term in terms:
                if term in wt or (len(wt) >= 2 and wt in term and len(wt) >= len(term) // 2):
                    hits.append(
                        ImageHit(
                            "entity",
                            term,
                            (
                                word["left"] - 3,
                                word["top"] - 3,
                                word["left"] + word["width"] + 3,
                                word["top"] + word["height"] + 3,
                            ),
                        )
                    )
                    break
    text = collapse_cjk_spaces(" ".join(wd["text"] for wd in words))
    return hits, w, h, text


def detect_seals(
    data: bytes,
    *,
    min_radius_frac: float = 0.04,
    max_radius_frac: float = 0.25,
) -> list[dict[str, Any]]:
    """Find red circular stamps with OpenCV Hough. Returns normalised rect drafts.

    Each item: ``{"rect": [x0,y0,x1,y1] (0..1), "radius_px": r, "red_ratio": f}``.
    Drafts only — never applied automatically.
    """
    if not seal_detection_available():
        return []
    import cv2
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 70, 60), (12, 255, 255)) | cv2.inRange(
        hsv, (165, 70, 60), (180, 255, 255)
    )
    if int(mask.sum()) == 0:
        return []
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    blur = cv2.GaussianBlur(mask, (9, 9), 2)
    side = min(w, h)
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(10, int(side * min_radius_frac * 2)),
        param1=100,
        param2=30,
        minRadius=max(5, int(side * min_radius_frac)),
        maxRadius=int(side * max_radius_frac),
    )
    out: list[dict[str, Any]] = []
    if circles is None:
        return out
    for x, y, r in np.round(circles[0]).astype(int):
        x0, y0 = max(0, x - r), max(0, y - r)
        x1, y1 = min(w, x + r), min(h, y + r)
        roi = mask[y0:y1, x0:x1]
        ratio = float((roi > 0).mean()) if roi.size else 0.0
        if ratio < 0.05:
            continue
        pad = 0.08 * r
        out.append(
            {
                "rect": [
                    round(max(0.0, (x0 - pad) / w), 4),
                    round(max(0.0, (y0 - pad) / h), 4),
                    round(min(1.0, (x1 + pad) / w), 4),
                    round(min(1.0, (y1 + pad) / h), 4),
                ],
                "radius_px": int(r),
                "red_ratio": round(ratio, 3),
            }
        )
    return out


def seal_draft_for_pdf(path: Path, *, dpi: int = 150) -> dict[str, Any]:
    """Render each page and propose seal regions in the party-spec region format."""
    import pymupdf as fitz

    regions: list[dict[str, Any]] = []
    doc = fitz.open(str(path))
    try:
        for i, page in enumerate(doc, start=1):
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            for seal in detect_seals(png):
                regions.append(
                    {
                        "page": i,
                        "rect": seal["rect"],
                        "category": "seal",
                        "note": f"DRAFT seal r={seal['radius_px']}px red={seal['red_ratio']} — review, then move into parties.<a|b>.regions",
                    }
                )
    finally:
        doc.close()
    return {
        "draft": True,
        "available": seal_detection_available(),
        "source": str(path),
        "notes": "Seal drafts only; nothing was redacted. Copy reviewed entries into a party spec for redact-scan --party-spec.",
        "regions": regions,
    }


def scan_docx_media(
    path: Path,
    *,
    categories: Iterable[str],
    extra_terms: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """OCR images under word/media/. Each item: {name, ocr, hits:[...]} (ocr False if skipped)."""
    import zipfile

    out: list[dict[str, Any]] = []
    can = ocr_available()
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.startswith("word/media/"):
                continue
            item: dict[str, Any] = {"name": name, "ocr": False, "hits": []}
            if can and Path(name).suffix.lower() in IMAGE_EXTS:
                try:
                    hits, w, _h, _t = ocr_image_hits(
                        z.read(name), categories=categories, extra_terms=extra_terms
                    )
                    item["ocr"] = bool(w)
                    item["hits"] = [h.to_dict() for h in hits]
                except Exception as exc:  # pragma: no cover - defensive
                    item["error"] = f"{type(exc).__name__}: {exc}"
            out.append(item)
    return out
