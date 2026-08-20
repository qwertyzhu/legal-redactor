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
