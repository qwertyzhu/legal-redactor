"""Labeled works, accounts and names are replaced in one pass. Filings keep them."""

from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.suspects import detect_suspects


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


def test_contract_party_labels_require_field_boundary_and_colon():
    text = (
        "  甲方：付测远；乙方 签名：陆测青\n"
        "乙方付尾款，甲方交货。\n"
        "如由乙方：王测云，按本条约定。\n"
        "丙方：\n丁测初\n"
        "丁方：澜岫测试有限公司。"
    )
    suspects = {s.text: (s.category, s.role_hint) for s in detect_suspects(text)}
    assert {name for name, (category, _) in suspects.items() if category == "person"} == {
        "付测远", "陆测青"
    }
    assert suspects["付测远"] == suspects["陆测青"] == ("person", "party")
    assert suspects["澜岫测试有限公司"][0] == "organization"


def test_contract_party_labels_distinguish_overlapping_names():
    text = "甲方：顾南枝\n乙方：顾南"
    plan = build_plan(text, mode="ai", auto_confident=True)
    assert apply_mapping_to_text(text, plan.mapping()) == "甲方：某甲\n乙方：某乙"


def test_signature_name_does_not_hide_a_shorter_name_in_another_field():
    text = "乙方签字：沈测青禾\n姓名：沈测青\n联系人：沈测"
    plan = build_plan(text, mode="ai", auto_confident=True)
    assert apply_mapping_to_text(text, plan.mapping()) == "乙方签字：某甲\n姓名：某乙\n联系人：某丙"


def test_contract_party_labels_do_not_replace_four_character_obligations():
    text = "甲方：欧阳测青\n乙方：包工包料"
    plan = build_plan(text, mode="ai", auto_confident=True)
    assert apply_mapping_to_text(text, plan.mapping()) == "甲方：某甲\n乙方：包工包料"


def test_signature_fields_preserve_signing_and_stamp_instructions():
    text = "甲方签字：盖章生效\n乙方签名：盖章确认\n丙方签字：加盖公章"
    assert not detect_suspects(text)
