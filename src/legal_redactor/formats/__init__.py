from __future__ import annotations

from urllib.parse import unquote

from ..entities import apply_mapping_to_text
from ..patterns import detect_structural, structural_placeholder

# Non-whitespace separator between independent body/metadata/link fields
# so spaced patterns (e.g. mobile/landline regex allowing \s) cannot bridge across fields.
FIELD_BOUNDARY = "\n|\n"

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


def _scrub_encoded_part(
    part: str, mapping: list[tuple[str, str]], *, escape_slash: bool = False
) -> str:
    decoded = unquote(part)
    if decoded == part:
        return part
    scrubbed = scrub_structural_text(
        apply_mapping_to_text(decoded, mapping), categories=URL_SECRET_CATEGORIES
    )
    if scrubbed == decoded:
        return part
    return scrubbed.replace("/", "%2F") if escape_slash else scrubbed


def scrub_external_url(target: str, mapping: list[tuple[str, str]] | None = None) -> str:
    """Apply planned mapping and strip structural PII/tokens inside a preserved URL."""
    pairs = mapping or []
    redacted = apply_mapping_to_text(target, pairs)
    scrubbed = scrub_structural_text(redacted, categories=URL_SECRET_CATEGORIES)
    if "%" not in scrubbed:
        return scrubbed
    before_hash, hash_sep, fragment = scrubbed.partition("#")
    base, q_sep, query = before_hash.partition("?")
    scheme_idx = base.find("://")
    path_start = base.find("/", scheme_idx + 3) if scheme_idx >= 0 else base.find("/")
    if path_start >= 0:
        prefix, path = base[:path_start], base[path_start:]
        new_base = prefix + "/".join(
            _scrub_encoded_part(seg, pairs, escape_slash=True) for seg in path.split("/")
        )
    else:
        new_base = base
    if q_sep:
        new_query_items = []
        for item in query.split("&"):
            if "=" in item:
                k, v = item.split("=", 1)
                new_query_items.append(f"{k}={_scrub_encoded_part(v, pairs)}")
            else:
                new_query_items.append(_scrub_encoded_part(item, pairs))
        new_query = "?" + "&".join(new_query_items)
    else:
        new_query = ""
    new_frag = f"#{_scrub_encoded_part(fragment, pairs)}" if hash_sep else ""
    return f"{new_base}{new_query}{new_frag}"
