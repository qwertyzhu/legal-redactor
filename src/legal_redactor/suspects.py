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
_ORG_SUFFIX_RE = re.compile(_ORG_SUFFIX)
_ORG_NAME_CHAR = re.compile(r"[一-鿿A-Za-z0-9（）()·&]")
# Longest plausible name part before the suffix. A walk that hits this limit without
# a boundary is "unanchored": surfaced for review, never auto-replaced.
_ORG_MAX_HEAD = 30
# Left boundaries inside an unpunctuated clause, e.g.
# 原告X公司（以下简称X）与被告Y公司 / 赔偿原告X公司 / 开户行为X银行.
# The name starts after the last boundary found.
_ORG_LEFT_CUT = re.compile(
    r"[（(]以下(?:简称|称)[^）)]{0,20}[）)]"
    r"|(?:公司|集团|事务所|中心|研究院|研究所|医院|大学|学院|协会|学会|出版社|基金会|合作社|工厂|支行|分行)"
    r"(?:和|与|及|同)"
    r"|原审原告|原审被告|原审第三人|被上诉人|上诉人|被申请人|申请执行人|被执行人|申请人|第三人|原告|被告"
    r"|甲方|乙方|丙方|丁方|被授权方|被许可方|授权方|许可方|受让方|转让方|委托|聘请"
    r"|开户行为|开户银行|开户行|开户名称|公司名称|收款行|收款单位|付款单位|收款人|付款人"
    r"|与|及"
)
# 人民法院 / 律师事务所 / 出版社 也是条款用语。头里带这些字就不是名称。
_INSTITUTIONAL_SUFFIX = (
    "人民法院",
    "检察院",
    "仲裁委员会",
    "律师事务所",
    "事务所",
    "出版社",
)
_CLAUSE_IN_HEAD = re.compile(
    r"[的向其与及或]|协议|签署|任一|均可|合作|披露|授予|根据|可以|应当"
)
_ORG_LEAD = re.compile(
    r"^(?:(?:开户行|收款行|开户银行|甲方|乙方|丙方)[：:\s]*|(?:本所|本律师|我所)?(?:接受|受)"
    r"|[（(][^）)]{0,10}[）)])"
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
    rf"(?P<delim>[：:]\s*[，,、]\s*|[：:]\s*|\s*)"
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

# Common words that follow a role word and start with a surname character
# (第三人陈述 / 被告明知 / 原告经济损失). Without this they become "names" and get
# replaced everywhere in the document.
_PERSON_STOPWORDS = frozenset(
    {
        "陈述", "明知", "方面", "平台", "管理", "经营", "经济", "经济损失", "时间", "于是",
        "应当", "承担", "全部", "安全", "高度", "成立", "关于", "支付", "单方", "常年",
        "曾经", "曾多次", "向其", "和解", "合理开支", "公开", "公司", "单位", "家庭",
        "双方", "代表", "工作", "居住", "出庭", "作证",
    }
)

# Names in narrative, anchored by a relation word instead of a party role:
# 证人王丽娟出庭作证 / 其表弟周小川实际运营 / 丈夫刘德胜亦知情.
_NARRATIVE_ANCHORS = (
    "证人|案外人|表弟|表哥|表姐|表妹|堂弟|堂兄|堂姐|堂妹|丈夫|妻子|前夫|前妻|"
    "父亲|母亲|儿子|女儿|哥哥|姐姐|弟弟|妹妹|员工|职工|股东|实际控制人|实际经营者|"
    "业务员|经办人|朋友|同事"
)
_PERSON_NARRATIVE = re.compile(
    rf"(?P<anchor>{_NARRATIVE_ANCHORS})"
    rf"(?P<name>[一-鿿]{{2,3}}?)"
    rf"(?=出庭|作证|证实|陈述|称|系|于|在|与|和|向|的|是|为|将|已|亦|也|曾|按|对|从|把|被|"
    rf"所|等|及|实际|负责|签|收|[，,。；;、）)\s]|$)"
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


def _org_candidates(text: str) -> list[tuple[str, int, bool]]:
    """(name, start, anchored) for every org suffix, walking left to a boundary.

    Walking left from each suffix (instead of one greedy regex from the clause
    start) keeps 「原告X公司（以下简称X）与被告Y公司」 from becoming one blob that
    hides both real names.
    """
    out: list[tuple[str, int, bool]] = []
    for m in _ORG_SUFFIX_RE.finditer(text):
        s = m.start()
        floor = max(0, s - _ORG_MAX_HEAD)
        i = s
        while i > floor and _ORG_NAME_CHAR.match(text[i - 1]):
            i -= 1
        anchored = i == 0 or not _ORG_NAME_CHAR.match(text[i - 1])
        head = text[i:s]
        cut = 0
        for c in _ORG_LEFT_CUT.finditer(head):
            cut = c.end()
        if cut:
            head = head[cut:]
            anchored = True
        while True:
            lead = _ORG_LEAD.match(head)
            if not lead or not lead.end():
                break
            head = head[lead.end() :]
        if len(head) < 2:
            continue
        suffix = m.group(0)
        if suffix.endswith(_INSTITUTIONAL_SUFFIX) and _CLAUSE_IN_HEAD.search(head):
            continue
        out.append((head + suffix, s - len(head), anchored))
    return out


def _person_ok(val: str) -> bool:
    if not val or val in _PERSON_BLOCKLIST or val.startswith(tuple(_PERSON_STOPWORDS)):
        return False
    if "某" in val:  # already an alias (王某 / 某甲)
        return False
    return not val.endswith(("公司", "法院", "银行", "事务所"))


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

    for val, start, anchored in _org_candidates(text):
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
                reason="org_suffix" if anchored else "org_suffix_unanchored",
                start=start,
                end=start + len(val),
                role_hint="party",
            )
        )

    for m in _PERSON.finditer(text):
        val = m.group("name").strip()
        delim = m.group("delim") or ""
        if not _person_ok(val) or val in skip:
            continue
        # No colon delimiter → require surname to avoid 原告提交…, a clear right
        # edge (原告张三，男 — not 原告经济损失100000元), and a compound surname
        # for 4-char names.
        has_delim = bool(delim.strip()) or delim.startswith(("：", ":"))
        if not has_delim:
            if not _has_surname(val):
                continue
            nxt = text[m.end("name") : m.end("name") + 1]
            if nxt.isdigit():
                continue
            if len(val) == 4 and not val.startswith(_COMPOUND_SURNAMES):
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

    for m in _PERSON_NARRATIVE.finditer(text):
        val = m.group("name")
        if not _person_ok(val) or val in skip or not _has_surname(val):
            continue
        if re.search(_NARRATIVE_ANCHORS, val):  # 丈夫和妻子 → not a name
            continue
        anchor = m.group("anchor")
        candidates.append(
            Suspect(
                text=val,
                category="person",
                reason="relation_anchored",
                start=m.start("name"),
                end=m.end("name"),
                role_hint="third_party" if anchor in ("证人", "案外人") else "other",
            )
        )

    for m in _ENTRUSTED.finditer(text):
        val = m.group("name").strip()
        if not _person_ok(val) or val in skip or not _has_surname(val):
            continue
        if val in {"本人", "当事人"}:
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
        # 《许可协议》《借款合同》是文书名称，不是作品。
        if val.endswith(("协议", "合同")):
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

    # An org candidate that merely extends a shorter one with the same ending
    # (本合同由X有限公司 vs X有限公司) carries clause text, not a longer name.
    orgs = {s.text for s in candidates if s.category == "organization"}
    candidates = [
        s
        for s in candidates
        if s.category != "organization"
        or not any(o != s.text and s.text.endswith(o) for o in orgs)
    ]

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
