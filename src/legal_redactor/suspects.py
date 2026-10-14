"""Heuristic natural-language entity suspects for human/agent review.

Never auto-replaces. Only surfaces candidates that look like PRC legal-document
persons/orgs/work titles so the agent can fill entities.json.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .allowlist import is_statute_title

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
_ORG_LEAD = re.compile(
    r"^(?:(?:开户行|收款行|开户银行|甲方|乙方|丙方)[：:\s]*|(?:本所|本律师|我所)?(?:接受|受))"
)

# Role/title anchored natural persons.
_PERSON_ROLES = (
    r"法定代表人|委托诉讼代理人|诉讼代理人|委托代理人|负责人|联系人|"
    r"指定联系人|授权代表|签字代表|"
    r"原告|被告|上诉人|被上诉人|申请人|被申请人|第三人|证人|"
    r"审判长|审判员|代理审判员|人民陪审员|陪审员|书记员|法官助理|执行员"
)
_COURT_STAFF_ROLES = ("审判长", "审判员", "陪审员", "书记员", "法官助理", "执行员")
# 受X委托 / 接受X的委托 — the name has no role word but is a client.
_ENTRUSTED = re.compile(
    r"(?:接受|受)(?P<name>[·一-鿿]{2,4}?)(?:的)?(?:全权)?委托"
)
# Optional parenthetical annotation, then optional colon/space, then 2–4 CJK name chars.
_PERSON = re.compile(
    rf"(?:{_PERSON_ROLES})"
    rf"(?:[（(][^）)\n]{{0,20}}[）)])?"
    rf"(?P<delim>[：:]\s*|\s*)"
    rf"(?P<name>[·一-鿿]{{2,4}})"
    rf"(?=(?:[，,。；;、\s\d）)\n]|男|女|联系|电话|手机|身份证|电子|邮箱|住址|住所|$))"
)

_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜"
    "阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞"
    "万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉钮龚程嵇邢"
    "滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧山谷"
    "车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶郜"
    "黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡"
    "劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀浦尚农温别庄晏柴瞿阎充慕"
    "连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧"
    "殳沃利蔚越夔隆师巩聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关"
    "蒯相查后荆红游竺权逯盖益桓公"
)
_COMPOUND_SURNAMES = (
    "欧阳",
    "司马",
    "上官",
    "诸葛",
    "司徒",
    "宇文",
    "长孙",
    "慕容",
    "东方",
    "皇甫",
    "令狐",
)

# Work titles in 《》 — high-value matter keys in copyright practice.
_WORK = re.compile(r"《([^》\n]{1,40})》")

# Address-like: label + PRC admin unit chain ending in 号/室/层 etc.
_ADDRESS_LABEL = (
    r"(?:住所地|住址|地址|送达地址|联系地址|经营场所|注册地址|所在地|"
    r"住|现住|户籍地|经常居住地)"
)
_ADDRESS = re.compile(
    rf"(?:{_ADDRESS_LABEL})[：:\s]*"
    rf"("
    rf"[一-鿿0-9A-Za-z（）()·\- ]{{6,80}}?"
    rf"(?:号|室|层|幢|栋|弄|巷|街|路|道|村|组|区|县|市|省|镇|乡)"
    rf"[一-鿿0-9A-Za-z（）()·\- ]{{0,30}}"
    rf")"
)

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
    category: str  # person | organization | work_title | address | structural medium
    reason: str
    start: int
    end: int
    role_hint: str = "unknown"  # party | third_party | other | unknown

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_surname(name: str) -> bool:
    if any(name.startswith(item) for item in _COMPOUND_SURNAMES):
        return True
    return bool(name) and name[0] in _SURNAMES


def _role_hint_for_person(context: str) -> str:
    if any(k in context[-12:] for k in _COURT_STAFF_ROLES):
        return "court_staff"
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
    allowlist: Iterable[str] | None = None,
) -> list[Suspect]:
    """Return unique NL suspects not already listed in known/ignore/allowlist."""
    if not text:
        return []
    skip = {s.strip() for s in (known or []) if s and str(s).strip()}
    skip |= {s.strip() for s in (ignore or []) if s and str(s).strip()}
    skip |= {s.strip() for s in (allowlist or []) if s and str(s).strip()}
    candidates: list[Suspect] = []

    for m in _ORG.finditer(text):
        raw = m.group(1)
        lead = _ORG_LEAD.match(raw)
        if lead:
            val = raw[lead.end() :].strip()
            start = m.start(1) + lead.end()
        else:
            val = raw.strip()
            start = m.start(1)
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
                start=start,
                end=start + len(val),
                role_hint="party",
            )
        )

    for m in _PERSON.finditer(text):
        val = m.group("name").strip()
        delim = m.group("delim") or ""
        if val in _PERSON_BLOCKLIST or val in skip:
            continue
        if val.endswith(("公司", "法院", "银行", "事务所")):
            continue
        # No colon/space delimiter → require surname to avoid 原告提交…
        has_delim = bool(delim.strip()) or delim.startswith(("：", ":"))
        if not has_delim and not _has_surname(val):
            continue
        ctx_start = max(0, m.start() - 16)
        context = text[ctx_start : m.end()]
        candidates.append(
            Suspect(
                text=val,
                category="person",
                reason="role_anchored",
                start=m.start("name"),
                end=m.end("name"),
                role_hint=_role_hint_for_person(context),
            )
        )

    for m in _ENTRUSTED.finditer(text):
        val = m.group("name").strip()
        if val in _PERSON_BLOCKLIST or val in skip or not _has_surname(val):
            continue
        if val.endswith(("公司", "法院", "银行", "事务所")) or val in {"本人", "当事人"}:
            continue
        candidates.append(
            Suspect(
                text=val,
                category="person",
                reason="entrusted_by",
                start=m.start("name"),
                end=m.end("name"),
                role_hint="party",
            )
        )

    for m in _WORK.finditer(text):
        val = m.group(1).strip()
        full = f"《{val}》"
        if val in _WORK_BLOCKLIST or full in skip or val in skip:
            continue
        following = text[m.end() : m.end() + 12]
        if is_statute_title(full, following=following):
            continue
        if full in skip:
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

    for m in _ADDRESS.finditer(text):
        val = m.group(1).strip().rstrip("，,。；;、")
        if len(val) < 6 or val in skip:
            continue
        # Skip pure court venue phrases
        if "法院" in val and len(val) < 12:
            continue
        candidates.append(
            Suspect(
                text=val,
                category="address",
                reason="address_label",
                start=m.start(1),
                end=m.start(1) + len(val),
                role_hint="party",
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
