"""Textless PDFs are refused. Contract titles in book marks stay."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from legal_redactor.cli import main
from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.ocr_engine import page_is_uncovered
from legal_redactor.pipeline import redact_file
from legal_redactor.suspects import detect_suspects


def test_contract_titles_in_book_marks_are_not_works():
    text = (
        "双方签署《星测许可协议》及《星测借款合同》。"
        "授权作品为《星河测例》。依据《中华人民共和国著作权法》第十条。"
        "影片《星河协议》另行约定。"
    )
    works = {s.text for s in detect_suspects(text) if s.category == "work_title"}
    assert works == {"《星河测例》"}
    out = apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())
    assert "《星测许可协议》" in out
    assert "《星测借款合同》" in out
    assert "《星河协议》" in out
    assert "《中华人民共和国著作权法》第十条" in out
    assert "《星河测例》" not in out


def test_page_is_uncovered_when_chinese_is_missing_or_sparse():
    assert page_is_uncovered("测" * 39) is True
    assert page_is_uncovered("") is True
    assert page_is_uncovered("x" * 200 + "第1页") is True
    assert page_is_uncovered("A" * 100 + "测" * 40) is True
    assert page_is_uncovered("测" * 40) is False
    assert page_is_uncovered("测 " * 40) is False
    assert page_is_uncovered("测" * 40 + "A" * 40) is False


def _image_only_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(page.rect, color=(0, 0, 0), width=2)
    doc.save(path)
    doc.close()


def test_short_text_pdf_still_redacts(tmp_path: Path):
    src = tmp_path / "short.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Mobile 13900001111", fontsize=11)
    doc.save(src)
    doc.close()
    assert len("Mobile 13900001111") < 20
    out = tmp_path / "short.out.pdf"
    res = redact_file(src, mode="ai", output_path=out)
    assert out.is_file()
    assert "13900001111" not in res.output_text


def test_redact_refuses_textless_pdf(tmp_path: Path):
    src = tmp_path / "scan.pdf"
    out = tmp_path / "scan.redacted.pdf"
    _image_only_pdf(src)
    with pytest.raises(ValueError, match="没有文字层"):
        redact_file(src, mode="ai", output_path=out, auto_confident=True)
    assert not out.exists()


def test_cli_redact_textless_pdf_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = tmp_path / "scan.pdf"
    out = tmp_path / "scan.redacted.pdf"
    _image_only_pdf(src)
    code = main(["redact", str(src), "--mode", "ai", "--auto-confident", "-o", str(out)])
    assert code == 1
    assert not out.exists()
    assert "没有文字层" in capsys.readouterr().err
