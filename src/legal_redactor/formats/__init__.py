from __future__ import annotations

from urllib.parse import unquote

from ..entities import apply_mapping_to_text
from ..patterns import detect_structural, structural_placeholder

# Non-whitespace record separator between independent body/metadata/link fields
# so spaced patterns (e.g. mobile regex allowing \s) cannot bridge across fields.
FIELD_BOUNDARY = "\n\x1e\n"

URL_SECRET_CATEGORIES = frozenset(
    {
        "url_secret",
        "mobile",
        "landline",
        "id_card",
        "email",
        "bank_account",
        "passport",
        "hk_mo_permit",
        "uscc",
    }
)


def _replacement_for_hit(category: str, text: str) -> str:
    if category == "url_secret" and text.startswith(("?", "&")) and "=" in text:
        prefix = text.split("=", 1)[0] + "="
        return prefix + structural_placeholder(category, text, 0)
    return structural_placeholder(category, text, 0)


def scrub_structural_text(
    text: str, *, categories: set[str] | frozenset[str] | None = None
) -> str:
    """Replace standalone structural PII in metadata or link targets."""
    if not text:
        return text
    pairs = [
        (h.text, _replacement_for_hit(h.category, h.text))
        for h in detect_structural(text)
        if h.auto_replace
        and h.category != "url"
        and (categories is None or h.category in categories)
    ]
    if not pairs:
        return text
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return apply_mapping_to_text(text, pairs)


def scrub_external_url(target: str, mapping: list[tuple[str, str]] | None = None) -> str:
    """Apply planned mapping and strip structural PII/tokens inside a preserved URL."""
    redacted = apply_mapping_to_text(target, mapping or [])
    scrubbed = scrub_structural_text(redacted, categories=URL_SECRET_CATEGORIES)
    decoded = unquote(scrubbed)
    if decoded != scrubbed:
        decoded_scrubbed = scrub_structural_text(
            apply_mapping_to_text(decoded, mapping or []), categories=URL_SECRET_CATEGORIES
        )
        if decoded_scrubbed != decoded:
            return decoded_scrubbed
    return scrubbed
