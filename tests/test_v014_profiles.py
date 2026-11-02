"""0.14.0: publish is a public rewrite; judgment-online follows 法释〔2016〕19号."""

from __future__ import annotations

import json
from pathlib import Path

from legal_redactor.cli import main
from legal_redactor.patterns import detect_structural
from legal_redactor.pipeline import redact_file
from legal_redactor.profiles_loader import load_profile

VALID_USCC = "91110105MA01ABCDEB"
LUHN_OK_CARD = "6222021234567894"

_ONLINE = (
    "原告：张测伟，男，1985年3月12日出生，住北京市朝阳区测试路88号院3号楼501室，"
    f"手机13912345678。统一社会信用代码：{VALID_USCC}。\n"
    "委托诉讼代理人：刘测律，执业证号：6222021234567894。\n"
    "证人王测娟出庭作证。\n"
    "审判长 赵测审\n"
    "案号（2024）京0491民初1234号。\n"
)


def _src(tmp_path: Path, text: str, name: str = "j.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_ai_mode_redacts_court_staff_without_flag(tmp_path: Path):
    src = _src(tmp_path, "原告：张测伟\n审判长 赵测审\n")
    res = redact_file(src, mode="ai", output_path=tmp_path / "out.md", auto_confident=True)
    assert "赵测审" not in res.output_text
    assert "张测伟" not in res.output_text
    kept = tmp_path / "prod.md"
    assert main(["redact", str(src), "--profile", "production", "--auto-confident", "-o", str(kept)]) in (0, 1)
    assert "赵测审" in kept.read_text(encoding="utf-8")


def test_license_number_is_not_a_bank_account():
    hits = detect_structural(f"执业证号：{LUHN_OK_CARD}")
    assert all(h.category != "bank_account" for h in hits)
    labeled = detect_structural(f"账号：{LUHN_OK_CARD}")
    assert any(h.category == "bank_account" and h.auto_replace for h in labeled)


def test_judgment_online_keeps_parties_and_truncates_address(tmp_path: Path):
    prof = load_profile("judgment-online")
    assert prof.mode == "production"
    assert prof.truncate_addresses is True
    assert "uscc" in prof.keep_categories
    assert prof.amount_fuzzy is False
    entities = tmp_path / "entities.json"
    entities.write_text(
        json.dumps(
            [
                {"original": "周测童", "category": "person", "role": "article8"},
                {"original": "周测宁", "category": "person", "role": "article8"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    text = _ONLINE + "未成年人周测童、周测宁。赔偿人民币500000元。\n"
    src = _src(tmp_path, text)
    res = redact_file(
        src,
        mode="production",
        output_path=tmp_path / "out.md",
        profile="judgment-online",
        entities_path=entities,
    )
    out = res.output_text
    for kept in (
        "张测伟",
        "1985年3月12日出生",
        VALID_USCC,
        "刘测律",
        LUHN_OK_CARD,
        "王测娟",
        "赵测审",
        "（2024）京0491民初1234号",
        "北京市朝阳区",
        "500000元",
    ):
        assert kept in out, kept
    assert "测试路88号" not in out
    assert "13912345678" not in out
    assert "周测童" not in out and "周测宁" not in out
    assert "周某1" in out and "周某2" in out
    assert main(["verify", str(res.output_path), "--profile", "judgment-online", "--strict"]) == 0


def test_publish_still_renames_everyone(tmp_path: Path):
    src = _src(tmp_path, "原告：张测伟，住北京市朝阳区测试路88号。审判长 赵测审\n")
    res = redact_file(src, mode="ai", output_path=tmp_path / "p.md", profile="publish")
    assert "张测伟" not in res.output_text
    assert "张某" in res.output_text
    assert "赵测审" in res.output_text
    assert "测试路88号" not in res.output_text
