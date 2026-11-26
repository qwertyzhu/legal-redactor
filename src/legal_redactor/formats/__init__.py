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
    part: str,
    mapping: list[tuple[str, str]],
    *,
    escape_slash: bool = False,
    escape_query: bool = False,
) -> str:
    decoded = unquote(part)
    if decoded == part:
        return part
    scrubbed = scrub_structural_text(
        apply_mapping_to_text(decoded, mapping), categories=URL_SECRET_CATEGORIES
    )
    if scrubbed == decoded:
        return part
    if escape_slash:
        scrubbed = scrubbed.replace("/", "%2F")
    if escape_query:
        scrubbed = scrubbed.replace("&", "%26").replace("#", "%23")
    return scrubbed


def _is_url_secret_param(name: str) -> bool:
    return any(h.category == "url_secret" for h in detect_structural(f"?{name}=XXXX"))


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
    path_start = base.find("/", scheme_idx + 3) if scheme_idx >= 0 else 0
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
                dk = unquote(k)
                if _is_url_secret_param(dk) and len(unquote(v)) >= 4:
                    new_query_items.append(f"{dk}={structural_placeholder('url_secret', item, 0)}")
                else:
                    new_k = _scrub_encoded_part(k, pairs, escape_query=True)
                    new_v = _scrub_encoded_part(v, pairs, escape_query=True)
                    new_query_items.append(f"{new_k}={new_v}")
            else:
                new_query_items.append(_scrub_encoded_part(item, pairs, escape_query=True))
        new_query = "?" + "&".join(new_query_items)
    else:
        new_query = ""
    new_frag = f"#{_scrub_encoded_part(fragment, pairs)}" if hash_sep else ""
    return f"{new_base}{new_query}{new_frag}"
