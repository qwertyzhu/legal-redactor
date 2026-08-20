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


# 18-digit PRC resident ID (loose date check; checksum validated separately when possible)
_ID18 = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
# 15-digit legacy ID
_ID15 = re.compile(r"(?<!\d)[1-9]\d{7}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)")
# Mainland mobile
_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# Simple landline: 0xx-xxxxxxx or 0xx xxxxxxxx
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
# Email
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Bank card-ish 16-19 digits (exclude IDs already matched by length context later)
_BANK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# PRC case number, e.g. （2024）京73民初1234号
_CASE_NO = re.compile(
    r"[（(]\d{4}[）)][^（）()\n]{0,20}?"
    r"(?:民|刑|行|执|知|商|赔|清|破|辖|认)"
    r"[^（）()\n]{0,12}?\d+号"
)
# Unified social credit code (18 alnum, starts with digit or letter, common pattern)
_USCC = re.compile(r"(?<![A-Z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Z0-9])")


_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _id18_checksum_ok(value: str) -> bool:
    body = value.upper()
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


def detect_structural(text: str) -> list[PatternHit]:
    """Return non-overlapping structural hits, longest/earliest first."""
    candidates: list[PatternHit] = []

    for m in _ID18.finditer(text):
        val = m.group(0)
        # Prefer checksum-valid; still keep invalid-looking IDs that match shape
        # so residual scan can catch them, but mark category the same.
        candidates.append(PatternHit("id_card", val, m.start(), m.end()))

    for m in _ID15.finditer(text):
        candidates.append(PatternHit("id_card", m.group(0), m.start(), m.end()))

    for m in _MOBILE.finditer(text):
        candidates.append(PatternHit("mobile", m.group(0), m.start(), m.end()))

    for m in _LANDLINE.finditer(text):
        candidates.append(PatternHit("landline", m.group(0), m.start(), m.end()))

    for m in _EMAIL.finditer(text):
        candidates.append(PatternHit("email", m.group(0), m.start(), m.end()))

    for m in _CASE_NO.finditer(text):
        candidates.append(PatternHit("case_number", m.group(0), m.start(), m.end()))

    for m in _USCC.finditer(text):
        candidates.append(PatternHit("uscc", m.group(0), m.start(), m.end()))

    for m in _BANK.finditer(text):
        val = m.group(0)
        # Skip if already covered as ID-shaped 18-digit
        if len(val) == 18 and re.fullmatch(
            r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            val,
        ):
            continue
        if len(val) >= 16 and _luhn_ok(val):
            candidates.append(PatternHit("bank_account", val, m.start(), m.end()))
        elif len(val) in (16, 17, 19):
            # Keep obvious card-length numbers even if Luhn fails (test fixtures)
            candidates.append(PatternHit("bank_account", val, m.start(), m.end()))

    # Resolve overlaps: prefer longer, then earlier
    candidates.sort(key=lambda h: (-(h.end - h.start), h.start))
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
    "person": "某自然人",
    "organization": "某单位",
    "address": "某地址",
    "amount": "X元",
    "work_title": "某作品",
    "other": "[已脱敏]",
}


def categories_for_mode(mode: str) -> set[str]:
    """Which structural categories auto-redact in each mode."""
    mode = mode.lower().strip()
    if mode == "ai":
        return {
            "id_card",
            "mobile",
            "landline",
            "email",
            "bank_account",
            "case_number",
            "uscc",
        }
    if mode == "production":
        # Keep case numbers and party identity by default; strip high-risk contact/account data.
        return {
            "id_card",
            "mobile",
            "landline",
            "email",
            "bank_account",
            "uscc",
        }
    raise ValueError(f"unknown mode: {mode!r}; expected 'ai' or 'production'")


def iter_unique_texts(hits: Iterable[PatternHit]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        if h.text not in seen:
            seen.add(h.text)
            out.append(h.text)
    return out
