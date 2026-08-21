"""Heuristic natural-language entity suspects for human/agent review.

Never auto-replaces. Only surfaces candidates that look like PRC legal-document
persons/orgs/work titles so the agent can fill entities.json.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

# Org-like endings common in Chinese contracts / pleadings.
_ORG_SUFFIX = (
    r"(?:有限责任公司|股份有限公司|有限公司|集团有限公司|集团|律师事务所|事务所|"
    r"人民法院|检察院|仲裁委员会|医院|大学|学院|银行股份有限公司|"
    r"支行|分行|中心|研究院|研究所|工厂|合作社|基金会|协会|学会|出版社)"
)
_ORG = re.compile(
    rf"(?<![一-鿿])"
    rf"([一-鿿A-Za-z0-9（）()]{{2,40}}{_ORG_SUFFIX})"
)

# Role/title anchored natural persons.
_PERSON_ROLES = (
    r"法定代表人|委托诉讼代理人|诉讼代理人|委托代理人|负责人|联系人|"
    r"指定联系人|授权代表|签字代表|"
    r"原告|被告|上诉人|被上诉人|申请人|被申请人|第三人|证人"
)
# Optional parenthetical annotation, then colon/space, then 2–4 CJK name chars.
# Lookahead requires a boundary so we do not swallow the start of an org name.
_PERSON = re.compile(
    rf"(?:{_PERSON_ROLES})"
    rf"(?:[（(][^）)\n]{{0,20}}[）)])?"
    rf"(?:[：:]\s*|\s+)"
    rf"([·一-鿿]{{2,4}})"
    rf"(?=(?:[，,。；;、\s\d）)\n]|联系|电话|手机|身份证|电子|邮箱|住址|住所|$))"
)

# Work titles in 《》 — high-value matter keys in copyright practice.
_WORK = re.compile(r"《([^》\n]{1,40})》")

_ORG_BLOCKLIST = frozenset(
    {
        "人民法院",
        "最高人民法院",
        "最高人民检察院",
        "人民检察院",
        "仲裁委员会",
        "北京互联网法院",
        "杭州互联网法院",
        "广州互联网法院",
        "测试银行",
        "测试银行北京分行",
    }
)

_PERSON_BLOCKLIST = frozenset(
    {
        "某人",
        "某甲",
        "某乙",
        "某丙",
        "某丁",
        "原告",
        "被告",
        "第三人",
        "当事人",
        "代理人",
        "负责人",
        "联系人",
        "法定代表人",
        "盖章",
        "签字",
        "可方",
        "许可方",
        "被许可方",
    }
)

_WORK_BLOCKLIST = frozenset(
    {
        "合同",
        "协议",
        "附件",
        "附录",
        "证据",
        "目录",
    }
)


@dataclass(frozen=True)
class Suspect:
    text: str
    category: str  # person | organization | work_title
    reason: str
    start: int
    end: int
    role_hint: str = "unknown"  # party | third_party | other | unknown

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _role_hint_for_person(context: str) -> str:
    if any(k in context for k in ("第三人", "证人")):
        return "third_party"
    if any(
        k in context
        for k in (
            "原告",
            "被告",
            "上诉人",
            "被上诉人",
            "申请人",
            "被申请人",
            "法定代表人",
            "授权代表",
        )
    ):
        return "party"
    if any(k in context for k in ("代理人", "联系人", "负责人")):
        return "other"
    return "unknown"


def detect_suspects(
    text: str,
    *,
    known: Iterable[str] | None = None,
    ignore: Iterable[str] | None = None,
) -> list[Suspect]:
    """Return unique NL suspects not already listed in known/ignore."""
    if not text:
        return []
    skip = {s.strip() for s in (known or []) if s and str(s).strip()}
    skip |= {s.strip() for s in (ignore or []) if s and str(s).strip()}
    candidates: list[Suspect] = []

    for m in _ORG.finditer(text):
        val = m.group(1).strip()
        if len(val) < 4:
            continue
        if val in _ORG_BLOCKLIST or val in skip:
            continue
        if val.endswith("人民法院") and len(val) <= 8:
            continue
        # Bare "...银行" without branch is often a short label; keep 分行/支行/股份
        if val.endswith("银行") and not any(
            x in val for x in ("分行", "支行", "股份", "有限", "集团")
        ):
            continue
        candidates.append(
            Suspect(
                text=val,
                category="organization",
                reason="org_suffix",
                start=m.start(1),
                end=m.end(1),
                role_hint="party",
            )
        )

    for m in _PERSON.finditer(text):
        val = m.group(1).strip()
        if val in _PERSON_BLOCKLIST or val in skip:
            continue
        if val.endswith(("公司", "法院", "银行", "事务所")):
            continue
        ctx_start = max(0, m.start() - 16)
        context = text[ctx_start : m.end()]
        candidates.append(
            Suspect(
                text=val,
                category="person",
                reason="role_anchored",
                start=m.start(1),
                end=m.end(1),
                role_hint=_role_hint_for_person(context),
            )
        )

    for m in _WORK.finditer(text):
        val = m.group(1).strip()
        full = f"《{val}》"
        if val in _WORK_BLOCKLIST or full in skip or val in skip:
            continue
        candidates.append(
            Suspect(
                text=full,
                category="work_title",
                reason="book_title_marks",
                start=m.start(),
                end=m.end(),
                role_hint="other",
            )
        )

    # De-dupe by text; prefer longer strings; drop substring overlaps.
    candidates.sort(key=lambda s: (-len(s.text), s.start))
    chosen: list[Suspect] = []
    seen: set[str] = set()
    occupied: list[tuple[int, int]] = []
    for s in candidates:
        if s.text in seen or s.text in skip:
            continue
        if any(s.text != t and s.text in t for t in seen):
            continue
        if any(not (s.end <= a or s.start >= b) for a, b in occupied):
            continue
        seen.add(s.text)
        occupied.append((s.start, s.end))
        chosen.append(s)

    chosen.sort(key=lambda s: s.start)
    return chosen


def suspects_to_entity_rows(suspects: Iterable[Suspect]) -> list[dict[str, Any]]:
    """Rows suitable for entities.draft.json — no replacement (forces review)."""
    rows: list[dict[str, Any]] = []
    for s in suspects:
        rows.append(
            {
                "original": s.text,
                "category": s.category,
                "role": s.role_hint if s.role_hint != "unknown" else "other",
                "source": "suspect-hint",
                "notes": (
                    f"SUSPECT ({s.reason}); review before use — not auto-redacted. "
                    "Set role=party|third_party|counsel and optional replacement."
                ),
            }
        )
    return rows
