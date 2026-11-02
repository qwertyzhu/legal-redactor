"""Gaps borrowed from offline redaction practice: spaced lines, hotlines, MAC, URLs, 大写金额."""

from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.patterns import categories_for_mode, detect_structural
from legal_redactor.profiles_loader import load_profile


def _cats(text: str) -> dict[str, str]:
    return {hit.text: hit.category for hit in detect_structural(text)}


def test_hotline_spaced_landline_and_taiwan_permit():
    text = "联系电话：010-1234 5678，客服 400-800-1234，台胞证：12345678。"
    cats = _cats(text)
    assert cats.get("010-1234 5678") == "landline"
    assert cats.get("400-800-1234") == "landline"
    assert cats.get("12345678") == "hk_mo_permit"


def test_mac_and_url_modes():
    text = "设备 AA:BB:CC:DD:EE:FF，链接 https://example-fictional.test/a。"
    cats = _cats(text)
    assert cats.get("AA:BB:CC:DD:EE:FF") == "mac"
    assert cats.get("https://example-fictional.test/a") == "url"
    assert "www.court.example" not in cats
    assert "url" in categories_for_mode("ai")
    assert "url" not in categories_for_mode("production")
    assert "mac" in categories_for_mode("production")
    assert "url" not in load_profile("publish").effective_categories()
    assert "url" in load_profile("ai").effective_categories()


def test_chinese_amount_only_when_fuzzy():
    text = "报酬为壹佰贰拾万元整，另付500元。"
    ai = build_plan(text, mode="ai", auto_confident=True, amount_fuzzy=True)
    out = apply_mapping_to_text(text, ai.mapping())
    assert "壹佰贰拾万元整" not in out
    assert "500元" not in out
    assert "百万元级" in out
    filing = build_plan(text, mode="production", auto_confident=True, amount_fuzzy=False)
    kept = apply_mapping_to_text(text, filing.mapping())
    assert "壹佰贰拾万元整" in kept
    assert "500元" in kept


def test_production_keeps_url():
    text = "证据链接 https://example-fictional.test/clip 。"
    plan = build_plan(text, mode="production", auto_confident=True)
    out = apply_mapping_to_text(text, plan.mapping())
    assert "https://example-fictional.test/clip" in out
