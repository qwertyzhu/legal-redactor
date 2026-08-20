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


def _load_weiquan() -> str:
    spec_w = importlib.util.spec_from_file_location(
        "sample_weiquan_text", FIXTURES / "sample_weiquan_text.py"
    )
    mod_w = importlib.util.module_from_spec(spec_w)
    assert spec_w.loader is not None
    spec_w.loader.exec_module(mod_w)
    return mod_w.FICTIONAL_WEIQUAN


def test_weiquan_ai_mode_strips_contacts_and_parties():
    sample = _load_weiquan()
    plan = build_plan(
        sample,
        mode="ai",
        entities_file=FIXTURES / "entities_weiquan_ai.json",
    )
    text = apply_mapping_to_text(sample, plan.mapping())
    assert "西测影业有限公司" not in text
    assert "钱测四" not in text
    assert "13700003333" not in text
    assert "500101199203033456" not in text
    assert "6222021234567890123" not in text
    assert "91110108MA0123456X" not in text
    assert "《镜湖测案》" not in text
    assert "（2025）浙0192民初5678号" not in text
    report = scan_residual(text, "ai")
    assert report.ok, report.summary


def test_weiquan_production_keeps_parties_strips_high_risk():
    sample = _load_weiquan()
    plan = build_plan(
        sample,
        mode="production",
        entities_file=FIXTURES / "entities_weiquan_production.json",
    )
    text = apply_mapping_to_text(sample, plan.mapping())
    assert "西测影业有限公司" in text
    assert "东例律师事务所（普通合伙）" in text
    assert "钱测四" in text
    assert "13700003333" not in text
    assert "500101199203033456" not in text
    assert "6222021234567890123" not in text
    assert "91110108MA0123456X" not in text
    # case number kept by default in production
    assert "（2025）浙0192民初5678号" in text
    report = scan_residual(text, "production")
    assert report.ok, report.summary


def test_keep_categories_uscc_on_production():
    sample = _load_weiquan()
    plan = build_plan(
        sample,
        mode="production",
        entities_file=FIXTURES / "entities_weiquan_production.json",
        keep_categories={"uscc"},
    )
    originals = {e.original for e in plan.entities}
    assert "91110108MA0123456X" not in originals
    assert "31310000MA0198765X" not in originals
    text = apply_mapping_to_text(sample, plan.mapping())
    assert "91110108MA0123456X" in text
    assert "13700003333" not in text
    report = scan_residual(text, "production", keep_categories={"uscc"})
    assert report.ok, report.summary


def test_keep_categories_unknown_raises():
    with pytest.raises(ValueError, match="unknown structural"):
        build_plan(SAMPLE, mode="ai", keep_categories={"passport"})


def test_pipeline_keep_categories_roundtrip(tmp_path: Path):
    sample = _load_weiquan()
    src = tmp_path / "weiquan.md"
    src.write_text(sample, encoding="utf-8")
    out = tmp_path / "weiquan.redacted-production.md"
    result = redact_file(
        src,
        mode="production",
        output_path=out,
        entities_path=FIXTURES / "entities_weiquan_production.json",
        work_dir=tmp_path,
        keep_categories=["uscc"],
    )
    assert result.ok, result.residual.summary
    body = out.read_text(encoding="utf-8")
    assert "91110108MA0123456X" in body
    assert "13700003333" not in body
