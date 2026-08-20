"""Local OCR helpers for scanned Chinese legal PDFs.

Requires a system Tesseract install (chi_sim). No cloud calls.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import fitz

DEFAULT_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "tesseract",
)

DEFAULT_TESSDATA_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
)


def collapse_cjk_spaces(text: str) -> str:
    """Remove spaces inserted between CJK characters by OCR engines."""
    prev = None
    out = text
    while prev != out:
        prev = out
        out = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", out)
    out = re.sub(r"([\u4e00-\u9fff])\s+([，。、；：\"\"''（）【】《》])", r"\1\2", out)
    out = re.sub(r"([，。、；：\"\"''（）【】《》])\s+([\u4e00-\u9fff])", r"\1\2", out)
    return out


def find_tesseract(explicit: str | Path | None = None) -> str:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        found = shutil.which(str(explicit))
        if found:
            return found
        raise FileNotFoundError(f"tesseract not found: {explicit}")
    env = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
    if env and Path(env).is_file():
        return env
    for cand in DEFAULT_TESSERACT_CANDIDATES:
        if cand == "tesseract":
            found = shutil.which("tesseract")
            if found:
                return found
        elif Path(cand).is_file():
            return cand
    raise FileNotFoundError(
        "Tesseract not found. Install it or set TESSERACT_CMD to the executable path."
    )


def find_tessdata(explicit: str | Path | None = None) -> str | None:
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return str(p)
        raise FileNotFoundError(f"tessdata dir not found: {explicit}")
    for key in ("TESSDATA_PREFIX", "LEGAL_REDACTOR_TESSDATA"):
        env = os.environ.get(key)
        if env and Path(env).is_dir():
            return env
    for cand in DEFAULT_TESSDATA_CANDIDATES:
        p = Path(cand)
        if p.is_dir() and any(p.glob("chi_sim*.traineddata")):
            return str(p)
    # Let tesseract use its default install tessdata
    return None


@dataclass
class OcrPageResult:
    page: int
    text: str
    char_count: int


@dataclass
class OcrResult:
    input_path: Path
    pages: list[OcrPageResult] = field(default_factory=list)
    text_raw: str = ""
    text_normalized: str = ""
    meta_path: Path | None = None
    raw_path: Path | None = None
    normalized_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": str(self.input_path),
            "pages": len(self.pages),
            "total_chars_raw": len(self.text_raw),
            "total_chars_normalized": len(self.text_normalized),
            "per_page_chars": [p.char_count for p in self.pages],
            "raw_path": str(self.raw_path) if self.raw_path else None,
            "normalized_path": str(self.normalized_path) if self.normalized_path else None,
            "meta_path": str(self.meta_path) if self.meta_path else None,
        }


def pdf_has_text_layer(path: Path, *, min_chars: int = 20) -> bool:
    doc = fitz.open(str(path))
    try:
        total = 0
        for page in doc:
            total += len((page.get_text("text") or "").strip())
            if total >= min_chars:
                return True
        return False
    finally:
        doc.close()


def _ocr_image_file(
    image_path: Path,
    *,
    tesseract: str,
    tessdata: str | None,
    lang: str,
    psm: int,
) -> str:
    env = os.environ.copy()
    if tessdata:
        env["TESSDATA_PREFIX"] = tessdata
    cmd = [
        tesseract,
        str(image_path),
        "stdout",
        "-l",
        lang,
        "--psm",
        str(psm),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        env=env,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"tesseract failed: {err}")
    text = proc.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        text = proc.stdout.decode("gbk", errors="replace")
    return text.strip()


def ocr_pdf(
    input_path: Path,
    output_dir: Path,
    *,
    dpi: int = 200,
    lang: str = "chi_sim+eng",
    psm: int = 6,
    tesseract_cmd: str | Path | None = None,
    tessdata_dir: str | Path | None = None,
) -> OcrResult:
    """Render each PDF page and OCR to markdown + normalized text."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tesseract = find_tesseract(tesseract_cmd)
    tessdata = find_tessdata(tessdata_dir)

    doc = fitz.open(str(input_path))
    page_results: list[OcrPageResult] = []
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        with tempfile.TemporaryDirectory(prefix="legal-redactor-ocr-") as tmp:
            tmp_path = Path(tmp)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_path = tmp_path / f"page_{i + 1:04d}.png"
                pix.save(str(img_path))
                text = _ocr_image_file(
                    img_path,
                    tesseract=tesseract,
                    tessdata=tessdata,
                    lang=lang,
                    psm=psm,
                )
                page_results.append(OcrPageResult(page=i + 1, text=text, char_count=len(text)))
    finally:
        doc.close()

    parts = [f"<!-- 第 {p.page} 页 -->\n\n{p.text}" for p in page_results]
    raw = "\n\n---\n\n".join(parts)
    normalized = collapse_cjk_spaces(raw)

    raw_path = output_dir / "ocr.md"
    norm_path = output_dir / "ocr.normalized.md"
    meta_path = output_dir / "ocr_meta.json"
    raw_path.write_text(raw, encoding="utf-8")
    norm_path.write_text(normalized, encoding="utf-8")

    result = OcrResult(
        input_path=input_path,
        pages=page_results,
        text_raw=raw,
        text_normalized=normalized,
        meta_path=meta_path,
        raw_path=raw_path,
        normalized_path=norm_path,
    )
    meta_path.write_text(
        json.dumps(
            {
                **result.to_dict(),
                "dpi": dpi,
                "lang": lang,
                "psm": psm,
                "tesseract": tesseract,
                "tessdata": tessdata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result
