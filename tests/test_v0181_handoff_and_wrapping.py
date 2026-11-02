"""0.18.1: honest handoff on FAIL, wrapped names on PDF, role words, permit labels."""

from __future__ import annotations

from pathlib import Path

import pytest

from legal_redactor.cli import _print_handoff, main
from legal_redactor.patterns import detect_structural
from legal_redactor.suspects import Suspect, detect_suspects

ORG = "北京星澜影业有限公司"
WRAPPED_ORG = "西安云海数字娱乐发行股份有限公司"
PERSON = "王建国"


def _suspect(text: str, category: str = "organization") -> Suspect:
    return Suspect(text=text, category=category, reason="org_suffix", start=0, end=len(text))


def test_handoff_fail_never_says_stop(capsys):
    """A failed residual scan must not print 无待补实体/到此停止."""
    _print_handoff(
        [],
        31,
        hits=[{"category": "unreplaced_entity", "text": ORG}],
        ok=False,
        mode="ai",
    )
    out = capsys.readouterr().out
    assert "status=FAIL" in out
    assert "不得交付" in out
    assert ORG in out
    assert "无待补实体" not in out
    assert "到此停止" not in out


def test_handoff_fail_without_hits_still_warns(capsys):
    _print_handoff([], 3, hits=[], ok=False, mode="ai")
    out = capsys.readouterr().out
    assert "status=FAIL" in out
    assert "无待补实体" not in out


def test_handoff_pass_lists_pending(capsys):
    _print_handoff([_suspect(ORG)], 12, hits=[], ok=True, mode="ai")
    out = capsys.readouterr().out
    assert "pending=1" in out
    assert ORG in out
    assert "status=FAIL" not in out


def test_handoff_production_does_not_ask_for_parties(capsys):
    _print_handoff([_suspect(ORG, "organization"), _suspect(PERSON, "person")], 11, ok=True, mode="production")
    out = capsys.readouterr().out
    assert "production" in out
    assert "不要为它们补 entities" in out
    assert "只把下面几条补进 entities.json" not in out


def test_role_words_are_not_person_suspects():
    """PDF placeholders push a role word behind 法定代表人：, which used to be
    reported as a person leak."""
    for text in (
        "法定代表人：\n，董事长。",
        "委托诉讼代理人：\n，\n律师。",
        "法定代表人：\n，总经理。",
    ):
        names = {s.text for s in detect_suspects(text) if s.category == "person"}
        assert not (names & {"董事长", "律师", "总经理"}), text


def test_taiwan_permit_keeps_permit_label():
    """0123456789 is also a 10-digit landline; the labelled permit must win."""
    hits = detect_structural("台湾居民来往大陆通行证（台胞证）号：0123456789")
    assert [(h.category, h.text) for h in hits] == [("hk_mo_permit", "0123456789")]
    hits = detect_structural("台胞证号：0123456789")
    assert hits[0].category == "hk_mo_permit"
    # A plain 10-digit number is still a landline.
    assert detect_structural("电话：0123456789")[0].category == "landline"


def _cjk_font() -> str | None:
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"):
        if Path(candidate).exists():
            return candidate
    return None


def _wrapped_pdf(path: Path) -> None:
    """Write a PDF whose party name is split across a line break.

    Mirrors the real thing: the name never appears on one line, so
    `Page.search_for` cannot see it and only an entities entry plus
    character-level matching can redact it.
    """
    import pymupdf as fitz

    font = _cjk_font()
    if font is None:  # pragma: no cover - no CJK font installed
        pytest.skip("no CJK font available")
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_font(fontname="cjk", fontfile=font)
    lines = [
        "北京互联网法院民事判决书",
        "原告：" + ORG + "，住所地北京市朝阳区。",
        "本院查明，涉案节目信息网络传播权由西安云海数",
        "字娱乐发行股份有限公司独占专有，被告星澜公司实施侵权。",
    ]
    y = 60.0
    for line in lines:
        page.insert_text((24, y), line, fontsize=10, fontname="cjk")
        y += 22
    doc.save(str(path))
    doc.close()


def test_pdf_wrapped_org_name_is_redacted(tmp_path, capsys):
    """A name wrapped across a line break used to survive the replacement while
    the residual scan still reported it — an unfixable-looking FAIL."""
    import json
    import re

    import pymupdf as fitz

    d = tmp_path / "case"
    d.mkdir()
    src = d / "judgment.pdf"
    _wrapped_pdf(src)
    raw = "\n".join(p.get_text() for p in fitz.open(str(src)))
    assert WRAPPED_ORG not in raw, "sample must wrap the name, not print it inline"
    assert WRAPPED_ORG in re.sub(r"\s+", "", raw)
    (d / "entities.json").write_text(
        json.dumps(
            {"entities": [{"original": WRAPPED_ORG, "category": "organization", "role": "party"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = d / "out.pdf"
    rc = main(["redact", str(src), "--mode", "ai", "-o", str(out)])
    printed = capsys.readouterr().out
    assert rc == 0, printed
    assert "status=FAIL" not in printed
    text = re.sub(r"\s+", "", "\n".join(p.get_text() for p in fitz.open(str(out))))
    assert WRAPPED_ORG not in text
    assert "云海" not in text


def test_truncated_variant_is_not_reported_again(tmp_path, capsys):
    """A wrapped name must not come back as a shorter 待补 entry after it was
    already replaced, or the handoff loop never converges."""
    import json

    d = tmp_path / "case"
    d.mkdir()
    src = d / "judgment.pdf"
    _wrapped_pdf(src)
    (d / "entities.json").write_text(
        json.dumps(
            {"entities": [{"original": WRAPPED_ORG, "category": "organization", "role": "party"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rc = main(["redact", str(src), "--mode", "ai", "-o", str(d / "out.pdf")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "pending=0" in out, out
    assert WRAPPED_ORG not in out
