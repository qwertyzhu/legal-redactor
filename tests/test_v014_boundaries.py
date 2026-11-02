"""Org boundaries, spaced taxpayer numbers, and punctuation before a name."""

from legal_redactor.entities import apply_mapping_to_text, build_plan
from legal_redactor.patterns import detect_structural
from legal_redactor.suspects import detect_suspects

VALID_USCC = "91110105MA01ABCDEB"


def _orgs(text: str) -> list[str]:
    return [s.text for s in detect_suspects(text) if s.category == "organization"]


def _out(text: str) -> str:
    return apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())


def test_company_after_licensee_label_is_only_the_name():
    text = "现将授权作品的权利授予被授权方星测影视文化传媒有限公司，具体如下。"
    assert _orgs(text) == ["星测影视文化传媒有限公司"]
    out = _out(text)
    assert "星测影视文化传媒有限公司" not in out
    assert "授予被授权方" in out and "具体如下" in out


def test_clause_before_firm_or_court_is_kept():
    text = "向其合作的律师事务所披露。协商不成的，任一方均可向本协议签署地人民法院提起诉讼。"
    assert _orgs(text) == []
    out = _out(text)
    assert "向其合作的律师事务所披露" in out
    assert "签署地人民法院提起诉讼" in out


def test_named_firm_still_replaced():
    text = "委托北京星测律师事务所办理。"
    assert _orgs(text) == ["北京星测律师事务所"]
    assert "北京星测律师事务所" not in _out(text)
    assert "委托" in _out(text) and "办理" in _out(text)


def test_spaced_taxpayer_id_replaced():
    spaced = "9111 0105 MA01 ABCD EB"
    assert spaced.replace(" ", "") == VALID_USCC
    text = f"纳税人识别号：{spaced}。"
    hits = [h for h in detect_structural(text) if h.category == "uscc"]
    assert hits and hits[0].auto_replace and hits[0].text == spaced
    out = _out(text)
    assert spaced not in out and "91110105" not in out
    assert "纳税人识别号：" in out


def test_contact_name_after_extra_comma():
    text = "联系人:，张测港 13912345678"
    names = [s.text for s in detect_suspects(text) if s.category == "person"]
    assert "张测港" in names
    assert "张测港" not in _out(text)
    assert "联系人" in _out(text)
