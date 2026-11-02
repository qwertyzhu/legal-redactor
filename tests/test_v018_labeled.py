"""Labeled works, accounts and names are replaced in one pass. Filings keep them."""

from legal_redactor.entities import apply_mapping_to_text, build_plan


def test_labeled_fields_redact_in_ai_and_repeat_without_label():
    text = (
        "著作权人：郝测三。姓名：John Smith。\n"
        "作品名称：街口回声。后文再次出现街口回声。\n"
        "抖音号：fan_account。作者认为本法不溯及既往。\n"
    )
    ai = apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())
    assert "郝测三" not in ai
    assert "John Smith" not in ai
    assert "街口回声" not in ai
    assert "fan_account" not in ai
    assert "作者认为" in ai
    filing = apply_mapping_to_text(
        text, build_plan(text, mode="production", auto_confident=True).mapping()
    )
    assert "郝测三" in filing
    assert "街口回声" in filing
    assert "fan_account" in filing
