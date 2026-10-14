"""Deterministic detectors for structural personal data in Chinese legal text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Fictional samples in tests/examples intentionally match these patterns.


@dataclass(frozen=True)
class PatternHit:
    category: str
    text: str
    start: int
    end: int
    confidence: str = "high"  # high | medium
    auto_replace: bool = True
    notes: str = ""


# 18-digit PRC resident ID (loose date check; checksum validated separately when possible)
_ID18 = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
# 15-digit legacy ID
_ID15 = re.compile(r"(?<!\d)[1-9]\d{7}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)")
# Mainland mobile, including groups split by spaces or dashes: 138 0000 2222
_MOBILE_SPACED = re.compile(r"(?<!\d)1[3-9](?:[\s\u3000-]{0,2}\d){9}(?!\d)")
# 18-digit ID with optional separators
_ID18_SPACED = re.compile(
    r"(?<!\d)[1-9]\d{5}[\s-]?(?:19|20)\d{2}[\s-]?(?:0[1-9]|1[0-2])[\s-]?"
    r"(?:0[1-9]|[12]\d|3[01])[\s-]?\d{3}[\s-]?[\dXx](?!\d)"
)
_LABELED_ACCOUNT = re.compile(
    r"(?:账号|帐号|账户|卡号|银行账号|收款账户)\s*[：:]\s*([0-9][0-9 \u3000-]{8,32}[0-9])"
)
# Simple landline: 0xx-xxxxxxx or 0xx xxxxxxxx
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
# Email. Chinese labels like 邮箱 are word chars in Python re, so \b fails for
# label-adjacent addresses (邮箱zhang@example.com). Use charset lookarounds instead.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])"
)
# Bank card-ish 16-19 digits (exclude IDs already matched by length context later)
_BANK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# PRC case number, e.g. （2024）京73民初1234号
_CASE_NO = re.compile(
    r"[（(]\d{4}[）)][^（）()\n]{0,20}?"
    r"(?:民|刑|行|执|知|商|赔|清|破|辖|认)"
    r"[^（）()\n]{0,12}?\d+号"
)
# Unified social credit code (18 alnum)
_USCC = re.compile(
    r"(?<![A-Z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Z0-9])"
)

# Passport / travel permit — only with label prefix
_LABELED_PASSPORT = re.compile(
    r"(?:护照号?|普通护照号?)\s*[：: ]\s*([EGPSD]\d{7,8}|[A-Z]\d{7,8})",
    re.IGNORECASE,
)
_LABELED_HK_MO = re.compile(
    r"(?:港澳通行证|通行证号?|回乡证号?)\s*[：: ]\s*([A-Z0-9]{8,11})",
    re.IGNORECASE,
)
# Plate: 京A12345 / 浙A·12345 / 应急等尾缀
_PLATE = re.compile(
    r"(?<![A-Z0-9])"
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-HJ-NP-Z]"
    r"[·\s]?"
    r"[A-HJ-NP-Z0-9]{4,5}"
    r"[挂学警港澳使领]?"
    r"(?![A-Z0-9])"
)
# 不动产权证号
_PROPERTY = re.compile(
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[（(]\d{4}[）)]"
    r"[^（）()\n]{0,20}?"
    r"不动产权第\d{4,10}号"
)
# WeChat / QQ — labeled only
_LABELED_WECHAT_QQ = re.compile(
    r"(?:微信号?|微信|QQ号?|QQ)\s*[：: ]\s*([A-Za-z][A-Za-z0-9_-]{4,19}|\d{5,12})"
)
# IPv4
_IP = re.compile(
    r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)"
)
# Birth date: …日出生 or labeled 出生日期
_BIRTH_INLINE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日(?=出生)")
_BIRTH_LABELED = re.compile(
    r"(?:出生日期|出生年月)\s*[：: ]\s*(\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})"
)

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
_USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def _id18_checksum_ok(value: str) -> bool:
    body = re.sub(r"[\s-]", "", value).upper()
    if len(body) != 18:
        return False
    try:
        total = sum(int(body[i]) * _ID_WEIGHTS[i] for i in range(17))
    except ValueError:
        return False
    return _ID_CHECK[total % 11] == body[17]


def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 16:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _uscc_checksum_ok(value: str) -> bool:
    body = value.upper()
    if len(body) != 18:
        return False
    try:
        total = sum(_USCC_ALPHABET.index(body[i]) * _USCC_WEIGHTS[i] for i in range(17))
    except ValueError:
        return False
    return _USCC_ALPHABET[(31 - total % 31) % 31] == body[17]


def case_number_placeholder(raw: str) -> str:
    """Preserve case-type letter and 初/终/再/执 stage in the alias."""
    match = re.search(
        r"(民|刑|行|执|知|商|赔|清|破|辖|认)(初|终|再|执|督|特|确)?",
        raw,
    )
    if not match:
        return "（20XX）XX民初XX号"
    kind = match.group(1)
    stage = match.group(2) or "初"
    return f"（20XX）X{kind}{stage}X号"


def detect_structural(text: str) -> list[PatternHit]:
    """Return non-overlapping structural hits, longest/earliest first."""
    candidates: list[PatternHit] = []

    for m in _ID18.finditer(text):
        val = m.group(0)
        ok = _id18_checksum_ok(val)
        candidates.append(
            PatternHit(
                "id_card",
                val,
                m.start(),
                m.end(),
                confidence="high" if ok else "medium",
                auto_replace=True,
                notes="" if ok else "id_checksum_fail",
            )
        )

    for m in _ID15.finditer(text):
        candidates.append(
            PatternHit(
                "id_card",
                m.group(0),
                m.start(),
                m.end(),
                confidence="medium",
                auto_replace=True,
                notes="legacy_15_digit",
            )
        )

    for m in _MOBILE_SPACED.finditer(text):
        candidates.append(PatternHit("mobile", m.group(0), m.start(), m.end()))

    for m in _ID18_SPACED.finditer(text):
        compact = re.sub(r"[\s-]", "", m.group(0))
        if len(compact) == 18 and re.fullmatch(
            r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            compact,
        ):
            ok = _id18_checksum_ok(compact)
            candidates.append(
                PatternHit(
                    "id_card",
                    m.group(0),
                    m.start(),
                    m.end(),
                    confidence="high" if ok else "medium",
                    auto_replace=True,
                    notes="" if ok else "id_checksum_fail",
                )
            )

    for m in _LANDLINE.finditer(text):
        candidates.append(PatternHit("landline", m.group(0), m.start(), m.end()))

    for m in _EMAIL.finditer(text):
        candidates.append(PatternHit("email", m.group(0), m.start(), m.end()))

    for m in _CASE_NO.finditer(text):
        candidates.append(PatternHit("case_number", m.group(0), m.start(), m.end()))

    for m in _USCC.finditer(text):
        val = m.group(0)
        if val.isdigit():
            continue
        ok = _uscc_checksum_ok(val)
        labeled = "信用代码" in text[max(0, m.start() - 12) : m.start()]
        candidates.append(
            PatternHit(
                "uscc",
                val,
                m.start(),
                m.end(),
                confidence="high" if ok else "medium",
                # checksum fail and no 信用代码 label → suspects only
                auto_replace=ok or labeled,
                notes="" if ok else ("uscc_checksum_fail_labeled" if labeled else "uscc_checksum_fail"),
            )
        )

    labeled_bank_spans: list[tuple[int, int]] = []
    for m in _LABELED_ACCOUNT.finditer(text):
        raw = m.group(1)
        compact = re.sub(r"[\s\u3000-]", "", raw)
        if not (10 <= len(compact) <= 22) or not compact.isdigit():
            continue
        if re.fullmatch(
            r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            compact,
        ):
            continue
        start = m.start(1)
        end = start + len(raw)
        labeled_bank_spans.append((start, end))
        ok = _luhn_ok(compact)
        candidates.append(
            PatternHit(
                "bank_account",
                raw,
                start,
                end,
                confidence="high" if ok else "medium",
                auto_replace=True,  # labeled → still replace
                notes="" if ok else "luhn_fail_labeled",
            )
        )

    for m in _BANK.finditer(text):
        val = m.group(0)
        if any(not (m.end() <= a or m.start() >= b) for a, b in labeled_bank_spans):
            continue
        if len(val) == 18 and re.fullmatch(
            r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            val,
        ):
            continue
        ok = _luhn_ok(val)
        if ok:
            candidates.append(
                PatternHit("bank_account", val, m.start(), m.end(), confidence="high")
            )
        else:
            # Luhn fail and no account prefix → suspects only
            candidates.append(
                PatternHit(
                    "bank_account",
                    val,
                    m.start(),
                    m.end(),
                    confidence="medium",
                    auto_replace=False,
                    notes="luhn_fail_unlabeled",
                )
            )

    for m in _LABELED_PASSPORT.finditer(text):
        raw = m.group(1)
        start = m.start(1)
        candidates.append(PatternHit("passport", raw, start, start + len(raw)))

    for m in _LABELED_HK_MO.finditer(text):
        raw = m.group(1)
        start = m.start(1)
        candidates.append(PatternHit("hk_mo_permit", raw, start, start + len(raw)))

    for m in _PLATE.finditer(text):
        candidates.append(PatternHit("plate", m.group(0), m.start(), m.end()))

    for m in _PROPERTY.finditer(text):
        candidates.append(PatternHit("property_cert", m.group(0), m.start(), m.end()))

    for m in _LABELED_WECHAT_QQ.finditer(text):
        raw = m.group(1)
        start = m.start(1)
        candidates.append(PatternHit("wechat_qq", raw, start, start + len(raw)))

    for m in _IP.finditer(text):
        candidates.append(PatternHit("ip", m.group(0), m.start(), m.end()))

    for m in _BIRTH_INLINE.finditer(text):
        # include trailing 出生 for a stable replace span when present
        end = m.end()
        text_span = m.group(0)
        if text[end : end + 2] == "出生":
            text_span = text_span + "出生"
            end = end + 2
        candidates.append(PatternHit("birth_date", text_span, m.start(), end))

    for m in _BIRTH_LABELED.finditer(text):
        raw = m.group(1)
        start = m.start(1)
        candidates.append(PatternHit("birth_date", raw, start, start + len(raw)))

    # Resolve overlaps: prefer longer, then earlier; prefer auto_replace
    candidates.sort(
        key=lambda h: (-(h.end - h.start), 0 if h.auto_replace else 1, h.start)
    )
    chosen: list[PatternHit] = []
    occupied: list[tuple[int, int]] = []
    for hit in candidates:
        if any(not (hit.end <= a or hit.start >= b) for a, b in occupied):
            continue
        chosen.append(hit)
        occupied.append((hit.start, hit.end))

    chosen.sort(key=lambda h: h.start)
    return chosen


# Placeholders used when no stable alias is supplied
DEFAULT_PLACEHOLDERS = {
    "id_card": "[身份证号]",
    "mobile": "[手机号]",
    "landline": "[电话]",
    "email": "[邮箱]",
    "bank_account": "[账号]",
    "case_number": "（20XX）XX民初XX号",
    "uscc": "[统一社会信用代码]",
    "passport": "[护照号]",
    "hk_mo_permit": "[通行证号]",
    "plate": "[车牌号]",
    "property_cert": "[不动产权证号]",
    "wechat_qq": "[社交账号]",
    "ip": "[IP地址]",
    "birth_date": "[出生日期]",
    "person": "某自然人",
    "organization": "某单位",
    "address": "某地址",
    "amount": "X元",
    "work_title": "某作品",
    "other": "[已脱敏]",
}


STRUCTURAL_CATEGORIES = frozenset(
    {
        "id_card",
        "mobile",
        "landline",
        "email",
        "bank_account",
        "case_number",
        "uscc",
        "passport",
        "hk_mo_permit",
        "plate",
        "property_cert",
        "wechat_qq",
        "ip",
        "birth_date",
    }
)

# Categories that get numbered placeholders when multiple distinct values appear
_NUMBERED_STRUCTURAL = frozenset(
    {
        "mobile",
        "landline",
        "email",
        "id_card",
        "bank_account",
        "uscc",
        "passport",
        "hk_mo_permit",
        "plate",
        "property_cert",
        "wechat_qq",
        "ip",
        "birth_date",
    }
)


def structural_placeholder(category: str, original: str, index: int) -> str:
    if category == "case_number":
        return case_number_placeholder(original)
    base = DEFAULT_PLACEHOLDERS.get(category, DEFAULT_PLACEHOLDERS["other"])
    if category in _NUMBERED_STRUCTURAL:
        return base if index == 0 else f"{base.rstrip(']')}{index + 1}]"
    return base


def categories_for_mode(
    mode: str,
    *,
    keep_categories: set[str] | None = None,
    extra_categories: set[str] | None = None,
    only_categories: set[str] | None = None,
) -> set[str]:
    """Which structural categories auto-redact in each mode.

    keep_categories: remove from the mode default set (e.g. keep USCC on a filing).
    extra_categories: add beyond the mode default (must be known structural types).
    only_categories: if set, use this exact set (ignore mode defaults).
    """
    mode = mode.lower().strip()
    if only_categories is not None:
        cats = {c.strip().lower() for c in only_categories if c and str(c).strip()}
        unknown = cats - STRUCTURAL_CATEGORIES
        if unknown:
            raise ValueError(
                f"unknown structural category/ies: {sorted(unknown)}; "
                f"expected one of {sorted(STRUCTURAL_CATEGORIES)}"
            )
        return cats
    if mode == "ai":
        cats = set(STRUCTURAL_CATEGORIES)
    elif mode == "production":
        # Keep case numbers, birth dates, party identity by default;
        # strip high-risk contact/account data + 法释 plate/property_cert.
        cats = {
            "id_card",
            "mobile",
            "landline",
            "email",
            "bank_account",
            "uscc",
            "passport",
            "hk_mo_permit",
            "plate",
            "property_cert",
            "wechat_qq",
            "ip",
        }
    else:
        raise ValueError(f"unknown mode: {mode!r}; expected 'ai' or 'production'")

    keep = {c.strip().lower() for c in (keep_categories or set()) if c and c.strip()}
    extra = {c.strip().lower() for c in (extra_categories or set()) if c and c.strip()}
    unknown = (keep | extra) - STRUCTURAL_CATEGORIES
    if unknown:
        raise ValueError(
            f"unknown structural category/ies: {sorted(unknown)}; "
            f"expected one of {sorted(STRUCTURAL_CATEGORIES)}"
        )
    cats |= extra
    cats -= keep
    return cats


def iter_unique_texts(hits: Iterable[PatternHit]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        if h.text not in seen:
            seen.add(h.text)
            out.append(h.text)
    return out
