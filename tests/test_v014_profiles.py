"""0.14.0: publish is a public rewrite; judgment-online follows 法释〔2016〕19号."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_publish_keeps_county_level_city_whole(tmp_path: Path):
    src = _src(
        tmp_path,
        "原告：张测伟，住江苏省苏州市昆山市玉山镇测试路88号。\n"
        "被告：李测强，住河南省郑州市测试路9号。\n",
    )
    res = redact_file(src, mode="ai", output_path=tmp_path / "p.md", profile="publish")
    assert "江苏省苏州市昆山市" in res.output_text
    assert "河南省郑州市" in res.output_text
    assert "测试路" not in res.output_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("住北京市海淀区中关村大街27号院3号楼502室为约定送达地址", "住北京市海淀区为约定送达地址"),
        ("送达地址为北京市海淀区中关村大街27号，该地址为约定送达地址。", "送达地址为北京市海淀区，该地址为约定送达地址。"),
        ("住北京市海淀区测试路27号院为民小区3栋2单元作为通讯地址", "住北京市海淀区作为通讯地址"),
    ],
)
def test_publish_keeps_clause_after_numbered_address(tmp_path: Path, text: str, expected: str):
    src = _src(tmp_path, text)
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(("name", "expected"), [("欧阳", "欧某"), ("欧阳明", "欧阳某")])
def test_publish_never_keeps_a_full_compound_surname_name(tmp_path: Path, name: str, expected: str):
    src = _src(tmp_path, f"原告{name}")
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"原告{expected}"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("苏州市阳光小区5栋", "苏州市"),
        ("苏州市星河园区8号楼", "苏州市"),
        ("苏州市云桥社区6号", "苏州市"),
        ("上海市浦东新区晨光小区3栋", "上海市浦东新区"),
        ("山东省东营市东营区测试路8号", "山东省东营市东营区"),
        ("南宁市某部队营区3号楼", "南宁市"),
        ("某市某部队营区3号楼", "某地址"),
    ],
)
def test_publish_uses_city_when_there_is_no_district(tmp_path: Path, address: str, expected: str):
    src = _src(tmp_path, f"住{address}")
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"住{expected}"


def test_publish_redacts_both_addresses_when_one_has_a_shorter_prefix(tmp_path: Path):
    src = _src(tmp_path, "住北京市海淀区测试路8号为约定送达地址。另住北京市海淀区测试路8号院3号楼。")
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "住北京市海淀区为约定送达地址。另住北京市海淀区。"


def test_publish_finds_address_after_unseparated_clause(tmp_path: Path):
    src = _src(tmp_path, "住北京市海淀区测试路8号为送达地址住苏州市测试路5号")
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "住北京市海淀区为送达地址住苏州市"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("住某市某区法院宿舍3号楼为约定送达地址", "住某市某区为约定送达地址"),
        ("住某市某区法院宿舍3号楼", "住某市某区"),
        ("地址北京市海淀区法院。", "地址北京市海淀区法院。"),
        ("地址：上海市浦东新区法院正门。", "地址：上海市浦东新区法院正门。"),
        ("地址：上海市浦东新区法院外。", "地址：上海市浦东新区法院外。"),
        ("地址：某市法院阳光小区旁。", "地址：某市法院阳光小区旁。"),
    ],
)
def test_publish_distinguishes_court_housing_from_court_venues(tmp_path: Path, text: str, expected: str):
    src = _src(tmp_path, text)
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("住北京市海淀区测试路27号为送达地址原告知悉该道路用途。", "住北京市海淀区为送达地址原告知悉该道路用途。"),
        ("住北京市海淀区测试路27号院3号楼502室，送达地址为上述地址且该道路为公共道路。", "住北京市海淀区，送达地址某地址。"),
        ("住北京市海淀区测试路27号为送达地址为上述地址且该道路为公共道路。", "住北京市海淀区为送达地址为上述地址且该道路为公共道路。"),
        ("送达地址为同上系诉讼文书使用的道路送达地址。", "送达地址某地址。"),
        ("送达地址：同上。送达地址为上述地址。", "送达地址：同上。送达地址为上述地址。"),
        ("住址同上3号楼502室", "住址某地址"),
        ("地址为上述地址3号楼502室", "地址某地址"),
        ("住址同上3号楼502室经确认无误。", "住址某地址。"),
        ("住址为同上502室。另在1502室开会。", "住址某地址。另在1502室开会。"),
        ("住址同上8号楼所在街道已确认。", "住址某地址。"),
        ("住址同上二层所在街道已确认。两人一同上二层办理立案手续。", "住址某地址。两人一同上二层办理立案手续。"),
    ],
)
def test_publish_preserves_address_references_and_following_prose(tmp_path: Path, text: str, expected: str):
    src = _src(tmp_path, text)
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("address", ["和平路二十七号", "绿洲公寓第二栋"])
def test_publish_redacts_numbered_addresses_without_an_administrative_area(tmp_path: Path, address: str):
    src = _src(tmp_path, f"住{address}")
    out = tmp_path / "published.md"
    assert main(["redact", str(src), "--profile", "publish", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "住某地址"
