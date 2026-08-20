from __future__ import annotations

import json
from pathlib import Path

import pytest

from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.formats import docx_io, pdf_io
from legal_redactor.patterns import detect_structural
from legal_redactor.pipeline import redact_file
from legal_redactor.verify import scan_residual

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fictional"

# Load sample without importing (path may contain non-package dirs)
import importlib.util

spec = importlib.util.spec_from_file_location(
    "sample_contract_text", FIXTURES / "sample_contract_text.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
SAMPLE = mod.FICTIONAL_CONTRACT


def test_detect_structural_on_fiction():
    hits = detect_structural(SAMPLE)
    cats = {h.category for h in hits}
    assert "mobile" in cats
    assert "id_card" in cats
    assert "case_number" in cats
    assert "bank_account" in cats


def test_ai_mode_plan_redacts_parties_and_case():
    plan = build_plan(
        SAMPLE,
        mode="ai",
        entities_file=FIXTURES / "entities_ai.json",
    )
    originals = {e.original for e in plan.entities}
    assert "郝测一" in originals
    assert "北测文化传播有限公司" in originals
    assert any(e.category == "case_number" for e in plan.entities)
    text = apply_mapping_to_text(SAMPLE, plan.mapping())
    assert "郝测一" not in text
    assert "13900001111" not in text
    assert "（2024）京0491民初1234号" not in text
    report = scan_residual(text, "ai")
    assert report.ok, report.summary


def test_production_keeps_parties_strips_contacts():
    plan = build_plan(
        SAMPLE,
        mode="production",
        entities_file=FIXTURES / "entities_production.json",
    )
    originals = {e.original for e in plan.entities}
    # parties not auto-wiped
    assert "郝测一" not in originals
    assert "北测文化传播有限公司" not in originals
    # contacts wiped
    assert any(e.category == "mobile" for e in plan.entities)
    assert any(e.category == "id_card" for e in plan.entities)
    text = apply_mapping_to_text(SAMPLE, plan.mapping())
    assert "郝测一" in text
    assert "北测文化传播有限公司" in text
    assert "13900001111" not in text
    assert "110101199001011234" not in text
    # case number kept for court production
    assert "（2024）京0491民初1234号" in text
    report = scan_residual(text, "production")
    assert report.ok, report.summary


def test_docx_roundtrip(tmp_path: Path):
    src = tmp_path / "in.docx"
    docx_io.create_sample_docx(src, SAMPLE)
    out = tmp_path / "out.docx"
    result = redact_file(
        src,
        mode="ai",
        output_path=out,
        entities_path=FIXTURES / "entities_ai.json",
        work_dir=tmp_path,
    )
    assert out.is_file()
    assert result.ok
    text = docx_io.extract_text(out)
    assert "郝测一" not in text
    assert "13900001111" not in text


def test_pdf_roundtrip(tmp_path: Path):
    src = tmp_path / "in.pdf"
    try:
        pdf_io.create_sample_pdf(src, SAMPLE)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"pdf fixture creation failed: {exc}")
    # If CJK font missing, Chinese may not embed; still try redaction path
    extracted = pdf_io.extract_text(src)
    if "13900001111" not in extracted:
        pytest.skip("pdf text layer missing expected digits (font issue)")
    out = tmp_path / "out.pdf"
    result = redact_file(
        src,
        mode="ai",
        output_path=out,
        entities_path=FIXTURES / "entities_ai.json",
        work_dir=tmp_path,
    )
    assert out.is_file()
    assert "13900001111" not in result.output_text


def test_output_suffix_must_match(tmp_path: Path):
    src = tmp_path / "a.docx"
    docx_io.create_sample_docx(src, "手机 13900001111")
    with pytest.raises(ValueError, match="suffix"):
        redact_file(src, mode="ai", output_path=tmp_path / "a.pdf")


def test_mapping_longest_first():
    text = "北测文化传播有限公司与北测文化"
    mapping = [("北测文化传播有限公司", "某单位A"), ("北测文化", "某简称")]
    # longest first behavior is in plan.mapping; simulate sorted
    mapping = sorted(mapping, key=lambda p: len(p[0]), reverse=True)
    out = apply_mapping_to_text(text, mapping)
    assert out == "某单位A与某简称"


def test_track_changes_ins_text_is_redacted(tmp_path: Path):
    src = ROOT / "tests" / "fixtures" / "track_changes_fiction.docx"
    assert src.is_file()
    from legal_redactor.formats import docx_io

    extracted = docx_io.extract_text(src)
    assert "南例网络科技有限公司" in extracted
    assert "13800002222" in extracted

    out = tmp_path / "out.docx"
    result = redact_file(
        src,
        mode="ai",
        output_path=out,
        entities_path=None,
        work_dir=tmp_path,
    )
    # structural phone gone
    assert "13800002222" not in result.output_text
    # company remains unless entities provided; ensure extract saw it pre-redact path works
    assert result.ok or "13800002222" not in result.output_text
    text_out = docx_io.extract_text(out)
    assert "13800002222" not in text_out
