"""0.17 keeps evidence the previous release was deleting, and removes what it left."""

from legal_redactor.entities import apply_mapping_to_text, build_plan


def test_link_stays_but_secret_phone_and_share_code_go():
    text = (
        "证据 https://user:secret1@example-fictional.test/watch?id=1&token=abcdEFGH1234 ，"
        "提取码 ab12，联系 13900001111。"
    )
    ai = apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())
    assert "https://" in ai
    assert "example-fictional.test/watch" in ai
    assert "secret1" not in ai
    assert "abcdEFGH1234" not in ai
    assert "ab12" not in ai
    assert "13900001111" not in ai
    filing = apply_mapping_to_text(
        text, build_plan(text, mode="production", auto_confident=True).mapping()
    )
    assert "example-fictional.test/watch" in filing
    assert "abcdEFGH1234" not in filing
    assert "ab12" in filing
    assert "13900001111" not in filing


def test_whole_url_only_when_asked():
    text = "证据 https://example-fictional.test/watch 。"
    kept = apply_mapping_to_text(text, build_plan(text, mode="ai", auto_confident=True).mapping())
    assert "https://example-fictional.test/watch" in kept
    gone = apply_mapping_to_text(
        text,
        build_plan(text, mode="ai", auto_confident=True, extra_categories={"url"}).mapping(),
    )
    assert "https://example-fictional.test/watch" not in gone
