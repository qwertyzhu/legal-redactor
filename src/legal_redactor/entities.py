"""Entity ledger: agent-supplied names/orgs plus structural hits → stable replacements."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .patterns import DEFAULT_PLACEHOLDERS, PatternHit, categories_for_mode, detect_structural


@dataclass
class EntityRecord:
    original: str
    category: str
    replacement: str
    role: str = "unknown"  # party | third_party | counsel | other | structural
    source: str = "manual"  # manual | structural | agent
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RedactionPlan:
    mode: str
    entities: list[EntityRecord] = field(default_factory=list)

    def mapping(self) -> list[tuple[str, str]]:
        """Longest original first to avoid partial clobber."""
        pairs = [(e.original, e.replacement) for e in self.entities if e.original and e.replacement]
        # de-dupe by original, first wins
        seen: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for o, r in pairs:
            if o in seen:
                continue
            if o == r:
                continue
            seen.add(o)
            uniq.append((o, r))
        uniq.sort(key=lambda p: len(p[0]), reverse=True)
        return uniq

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "entities": [e.to_dict() for e in self.entities],
        }

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


_PERSON_COUNTER = "甲乙丙丁戊己庚辛壬癸"
_ORG_COUNTER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
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
_COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "司徒", "宇文", "长孙", "慕容", "东方", "皇甫", "令狐")
_ABBR = re.compile(r"(?:[（(]\s*)?以下简称\s*[“\"「『]([^”\"」』）)\n]{2,12})[”\"」』]")
_ABBR_BLOCK = {"甲方", "乙方", "丙方", "原告", "被告", "本公司", "我方", "对方", "双方", "许可方", "被许可方"}
_AMOUNT = re.compile(
    r"(?<!\d)(?:人民币|RMB|[￥¥])?\s*(?:(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?)\s*(?:万元|元)"
)


def _stable_alias(category: str, index: int) -> str:
    if category == "person":
        label = _PERSON_COUNTER[index % len(_PERSON_COUNTER)]
        cycle = index // len(_PERSON_COUNTER)
        return f"某{label}" if cycle == 0 else f"某{label}{cycle + 1}"
    if category == "organization":
        label = _ORG_COUNTER[index % len(_ORG_COUNTER)]
        cycle = index // len(_ORG_COUNTER)
        return f"某单位{label}" if cycle == 0 else f"某单位{label}{cycle + 1}"
    if category == "address":
        return f"某地址{index + 1}"
    if category == "work_title":
        return f"某作品{index + 1}" if index else "某作品"
    if category == "amount":
        return DEFAULT_PLACEHOLDERS["amount"]
    return DEFAULT_PLACEHOLDERS.get(category, DEFAULT_PLACEHOLDERS["other"])


def _has_surname(name: str) -> bool:
    if any(name.startswith(item) for item in _COMPOUND_SURNAMES):
        return True
    return bool(name) and name[0] in _SURNAMES


def _amount_alias(raw: str) -> str:
    compact = re.sub(r"[,，\s人民币RMB￥¥]", "", raw)
    wan = "万" in compact
    digits = re.sub(r"万元|元", "", compact)
    try:
        number = float(digits)
    except ValueError:
        return "X元"
    value = number * 10000 if wan else number
    if value < 10_000:
        return "不足万元"
    if value < 100_000:
        return "数万元"
    if value < 1_000_000:
        return "数十万元"
    if value < 10_000_000:
        return "百万元级"
    if value < 100_000_000:
        return "千万元级"
    return "亿元级"


def _link_abbreviations(text: str, plan: RedactionPlan) -> None:
    existing = {entity.original for entity in plan.entities}
    orgs = [entity for entity in plan.entities if entity.category == "organization"]
    for org in orgs:
        start = 0
        while True:
            at = text.find(org.original, start)
            if at < 0:
                break
            window = text[at + len(org.original) : at + len(org.original) + 24]
            match = _ABBR.search(window)
            if match:
                short = match.group(1).strip()
                if len(short) >= 2 and short not in existing and short not in _ABBR_BLOCK:
                    plan.entities.append(
                        EntityRecord(
                            original=short,
                            category="organization",
                            replacement=org.replacement,
                            role=org.role,
                            source="auto-confident",
                            notes="以下简称，与全称同一替身",
                        )
                    )
                    existing.add(short)
            start = at + max(1, len(org.original))


def _append_amounts(text: str, plan: RedactionPlan, preserve_set: set[str]) -> None:
    existing = {entity.original for entity in plan.entities}
    for match in _AMOUNT.finditer(text):
        raw = match.group(0).strip()
        if not raw or raw in existing or raw in preserve_set:
            continue
        plan.entities.append(
            EntityRecord(
                original=raw,
                category="amount",
                replacement=_amount_alias(raw),
                role="other",
                source="auto-confident",
                notes="精确金额改为量级",
            )
        )
        existing.add(raw)


def load_entities_file(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entities" in data:
        data = data["entities"]
    if not isinstance(data, list):
        raise ValueError("entities file must be a list or {\"entities\": [...]}")
    return data


def build_plan(
    text: str,
    mode: str,
    entities_file: Path | None = None,
    preserve: list[str] | None = None,
    keep_categories: set[str] | None = None,
    extra_categories: set[str] | None = None,
    auto_confident: bool = False,
) -> RedactionPlan:
    """Merge structural detections with optional agent/manual entity list."""
    mode = mode.lower().strip()
    cats = categories_for_mode(
        mode, keep_categories=keep_categories, extra_categories=extra_categories
    )
    preserve_set = {p.strip() for p in (preserve or []) if p and p.strip()}
    plan = RedactionPlan(mode=mode)

    # 1) agent / manual entities
    person_i = org_i = addr_i = work_i = 0
    for raw in load_entities_file(entities_file):
        original = str(raw.get("original") or raw.get("text") or "").strip()
        if not original:
            continue
        if original in preserve_set:
            continue
        category = str(raw.get("category") or raw.get("type") or "other").strip().lower()
        role = str(raw.get("role") or "unknown").strip().lower()
        replacement = str(raw.get("replacement") or "").strip()

        if mode == "production" and role == "party" and category in {
            "person",
            "organization",
            "address",
        }:
            # Keep litigation/contract parties unless explicitly given a replacement
            if not replacement:
                continue

        if not replacement:
            if category == "person":
                replacement = _stable_alias("person", person_i)
                person_i += 1
            elif category == "organization":
                replacement = _stable_alias("organization", org_i)
                org_i += 1
            elif category == "address":
                replacement = _stable_alias("address", addr_i)
                addr_i += 1
            elif category == "work_title":
                replacement = _stable_alias("work_title", work_i)
                work_i += 1
            else:
                replacement = DEFAULT_PLACEHOLDERS.get(category, DEFAULT_PLACEHOLDERS["other"])

        plan.entities.append(
            EntityRecord(
                original=original,
                category=category,
                replacement=replacement,
                role=role,
                source=str(raw.get("source") or "agent"),
                notes=str(raw.get("notes") or ""),
            )
        )

    # 2) structural auto-detect
    already = {e.original for e in plan.entities}
    hits: list[PatternHit] = detect_structural(text)
    # stable placeholders per distinct value within category
    counters: dict[str, dict[str, str]] = {}
    for hit in hits:
        if hit.category not in cats:
            continue
        if hit.text in preserve_set or hit.text in already:
            continue
        bucket = counters.setdefault(hit.category, {})
        if hit.text not in bucket:
            base = DEFAULT_PLACEHOLDERS[hit.category]
            # For multiples of same category structural type, suffix index when needed
            if hit.category in {"mobile", "landline", "email", "id_card", "bank_account", "uscc"}:
                n = len(bucket) + 1
                # keep short fixed token; suffix only after first
                bucket[hit.text] = base if n == 1 else f"{base.rstrip(']')}{n}]"
            else:
                bucket[hit.text] = base
        plan.entities.append(
            EntityRecord(
                original=hit.text,
                category=hit.category,
                replacement=bucket[hit.text],
                role="structural",
                source="structural",
            )
        )
        already.add(hit.text)

    if auto_confident:
        from .suspects import detect_suspects

        for suspect in detect_suspects(text, known=already | preserve_set):
            if suspect.text in already or suspect.text in preserve_set:
                continue
            if mode == "production" and not (
                suspect.category == "person" and suspect.role_hint == "third_party"
            ):
                continue
            if suspect.category == "person":
                if not _has_surname(suspect.text):
                    continue
                replacement = _stable_alias("person", person_i)
                person_i += 1
            elif suspect.category == "organization":
                replacement = _stable_alias("organization", org_i)
                org_i += 1
            elif suspect.category == "address" and mode == "ai":
                replacement = _stable_alias("address", addr_i)
                addr_i += 1
            elif suspect.category == "work_title" and mode == "ai":
                replacement = _stable_alias("work_title", work_i)
                work_i += 1
            else:
                continue
            plan.entities.append(
                EntityRecord(
                    original=suspect.text,
                    category=suspect.category,
                    replacement=replacement,
                    role=suspect.role_hint if suspect.role_hint != "unknown" else "other",
                    source="auto-confident",
                    notes=suspect.reason,
                )
            )
            already.add(suspect.text)
        if mode == "ai":
            _link_abbreviations(text, plan)
            _append_amounts(text, plan, preserve_set)

    return plan


_TOKEN_SPLIT = re.compile(r"(\s+)")


def apply_mapping_to_text(text: str, mapping: list[tuple[str, str]]) -> str:
    if not text or not mapping:
        return text
    # Non-overlapping sequential replace using a marker-free approach:
    # sort by length already done; replace left-to-right with a temporary sentinel map
    # to avoid double-redaction of replacements that look like sources.
    out = text
    sentinels: list[tuple[str, str]] = []
    for i, (original, replacement) in enumerate(mapping):
        if not original or original not in out:
            continue
        token = f"⟦R{i}⟧"
        out = out.replace(original, token)
        sentinels.append((token, replacement))
    for token, replacement in sentinels:
        out = out.replace(token, replacement)
    return out
