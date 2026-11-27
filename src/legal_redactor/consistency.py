"""Cross-file entity consistency: unify aliases and report conflicts.

Used for multi-document matters so the same original string always maps to the
same replacement. Never invents legal judgment — only stable aliases and diffs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .allowlist import load_allowlist
from .draft import extract_text
from .entities import build_plan, load_entities_file
from .ledger_store import load_ledger
from .patterns import DEFAULT_PLACEHOLDERS, STRUCTURAL_CATEGORIES, structural_placeholder
from .pipeline import _normalize_keep_extra, iter_batch_inputs
from .profiles_loader import resolve_profile

_PERSON_COUNTER = "甲乙丙丁戊己庚辛壬癸"
_ORG_COUNTER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_INDEXED_ALIAS_CATEGORIES = STRUCTURAL_CATEGORIES | {
    "person", "organization", "address", "work_title", "account"
}
_ABBR_NOTE_PREFIX = "以下简称:"


def _stable_alias(category: str, index: int, original: str = "") -> str:
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
    if category == "account":
        return f"某账号{index + 1}" if index else "某账号"
    if category == "amount":
        return DEFAULT_PLACEHOLDERS["amount"]
    if category == "case_number":
        return structural_placeholder("case_number", original or "（20XX）民初1号", index)
    return structural_placeholder(category, original, index)


def _abbreviation_of(notes: str) -> str | None:
    if notes.startswith(_ABBR_NOTE_PREFIX):
        return notes[len(_ABBR_NOTE_PREFIX):].strip() or None
    return None


@dataclass
class Observation:
    original: str
    category: str
    role: str
    source: str
    files: list[str] = field(default_factory=list)
    replacements_seen: list[str] = field(default_factory=list)
    abbr_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Conflict:
    original: str
    replacements: list[str]
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        return f"{self.original}: {', '.join(self.replacements)}"


@dataclass
class PersonAliasCollision:
    replacement: str
    originals: list[str]
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        return f"{self.replacement} shared by {', '.join(self.originals)}"


@dataclass
class ConsistencyReport:
    files_scanned: int
    entity_count: int
    conflict_count: int
    observations: list[Observation]
    conflicts: list[Conflict | PersonAliasCollision]
    entities_path: Path | None = None
    report_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.conflict_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "entity_count": self.entity_count,
            "conflict_count": self.conflict_count,
            "ok": self.ok,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "observations": [o.to_dict() for o in self.observations],
            "entities_path": str(self.entities_path) if self.entities_path else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "warning": (
                "Unified entities assign stable aliases only. "
                "Review roles before production filings; suspects are still hints."
            ),
        }


def _add_obs(
    bucket: dict[str, Observation],
    *,
    original: str,
    category: str,
    role: str,
    source: str,
    file_label: str,
    replacement: str | None = None,
    abbr_of: str | None = None,
) -> None:
    original = original.strip()
    if not original:
        return
    obs = bucket.get(original)
    if obs is None:
        obs = Observation(
            original=original,
            category=category,
            role=role,
            source=source,
            files=[],
            replacements_seen=[],
            abbr_of=abbr_of,
        )
        bucket[original] = obs
    if file_label not in obs.files:
        obs.files.append(file_label)
    # Prefer richer category/role when later sources are more specific
    if obs.category in {"other", "unknown"} and category not in {"other", "unknown"}:
        obs.category = category
    if obs.role in {"unknown", "other"} and role not in {"unknown", "other"}:
        obs.role = role
    if replacement and replacement not in obs.replacements_seen:
        obs.replacements_seen.append(replacement)
    if abbr_of and not obs.abbr_of:
        obs.abbr_of = abbr_of


def collect_observations_from_file(
    path: Path,
    *,
    mode: str = "ai",
    entities_path: Path | None = None,
    include_suspects: bool = True,
    preserve: list[str] | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    allowlist: set[str] | None = None,
    profile: str | None = None,
    redact_court_staff: bool | None = None,
) -> dict[str, Observation]:
    """Scan one source document into original→observation map respecting mode/profile."""
    path = Path(path)
    text = extract_text(path)
    label = path.name
    bucket: dict[str, Observation] = {}

    prof = resolve_profile(profile=profile, mode=None) if profile else None
    eff_mode = (prof.mode if prof else mode).lower().strip()
    eff_preserve = list(preserve or []) + (list(prof.preserve) if prof else [])
    keep, extra = _normalize_keep_extra(keep_categories, extra_categories)
    only = prof.categories if prof is not None else None
    if prof is not None and prof.categories is None:
        keep |= prof.keep_categories
        extra |= prof.extra_categories
    eff_allow = allowlist if allowlist is not None else load_allowlist(search_dirs=[Path.cwd(), path.parent])
    alias_style = prof.alias_style if prof else "generic"
    address_granularity = prof.address_granularity if prof else "full"
    amount_fuzzy = prof.amount_fuzzy if prof else True
    staff = (
        redact_court_staff
        if redact_court_staff is not None
        else (prof.redact_court_staff if prof else (eff_mode == "ai"))
    )

    for raw in load_entities_file(entities_path):
        original = str(raw.get("original") or raw.get("text") or "").strip()
        if not original or "填写当事人" in original:
            continue
        _add_obs(
            bucket,
            original=original,
            category=str(raw.get("category") or raw.get("type") or "other").strip().lower(),
            role=str(raw.get("role") or "unknown").strip().lower(),
            source=str(raw.get("source") or "entities-file"),
            file_label=label,
            replacement=str(raw.get("replacement") or "").strip() or None,
        )

    plan = build_plan(
        text,
        mode=eff_mode,
        entities_file=entities_path,
        preserve=eff_preserve,
        keep_categories=keep,
        extra_categories=extra,
        auto_confident=include_suspects,
        allowlist=eff_allow,
        only_categories=only,
        alias_style=alias_style,
        address_granularity=address_granularity,
        amount_fuzzy=amount_fuzzy,
        redact_court_staff=staff,
        truncate_addresses=bool(prof.truncate_addresses) if prof else False,
        redact_third_party=prof.redact_third_party if prof else True,
    )
    for ent in plan.entities:
        keep_fixed_repl = (
            (
                ent.category not in _INDEXED_ALIAS_CATEGORIES
                and ent.source not in {"structural", "auto-confident"}
            )
            or ent.category == "amount"
            or (ent.category == "address" and address_granularity == "district")
            or (ent.category == "person" and (alias_style == "court" or ent.role == "article8"))
        )
        _add_obs(
            bucket,
            original=ent.original,
            category=ent.category,
            role=ent.role,
            source=ent.source,
            file_label=label,
            replacement=ent.replacement if keep_fixed_repl else None,
            abbr_of=_abbreviation_of(ent.notes),
        )
    return bucket


def collect_observations_from_ledgers(ledger_paths: Iterable[Path]) -> dict[str, Observation]:
    """Rebuild observations from plain or encrypted per-file ledgers."""
    bucket: dict[str, Observation] = {}
    for path in ledger_paths:
        path = Path(path)
        data = load_ledger(path)
        entities = data.get("entities") if isinstance(data, dict) else data
        if not isinstance(entities, list):
            continue
        label = path.name
        for raw in entities:
            original = str(raw.get("original") or "").strip()
            if not original:
                continue
            _add_obs(
                bucket,
                original=original,
                category=str(raw.get("category") or "other").strip().lower(),
                role=str(raw.get("role") or "unknown").strip().lower(),
                source=str(raw.get("source") or "ledger"),
                file_label=label,
                replacement=str(raw.get("replacement") or "").strip() or None,
                abbr_of=_abbreviation_of(str(raw.get("notes") or "")),
            )
    return bucket


def merge_observation_maps(maps: Iterable[dict[str, Observation]]) -> dict[str, Observation]:
    merged: dict[str, Observation] = {}
    for m in maps:
        for original, obs in m.items():
            existing = merged.get(original)
            if existing is None:
                merged[original] = Observation(
                    original=obs.original,
                    category=obs.category,
                    role=obs.role,
                    source=obs.source,
                    files=list(obs.files),
                    replacements_seen=list(obs.replacements_seen),
                    abbr_of=obs.abbr_of,
                )
                continue
            for f in obs.files:
                if f not in existing.files:
                    existing.files.append(f)
            for r in obs.replacements_seen:
                if r not in existing.replacements_seen:
                    existing.replacements_seen.append(r)
            if obs.abbr_of and not existing.abbr_of:
                existing.abbr_of = obs.abbr_of
            if existing.category in {"other", "unknown"} and obs.category not in {
                "other",
                "unknown",
            }:
                existing.category = obs.category
            if existing.role in {"unknown", "other"} and obs.role not in {"unknown", "other"}:
                existing.role = obs.role
            # Prefer non-suspect source labels when merging
            if existing.source == "suspect-hint" and obs.source != "suspect-hint":
                existing.source = obs.source
    return merged


def find_conflicts(bucket: dict[str, Observation]) -> list[Conflict | PersonAliasCollision]:
    conflicts: list[Conflict | PersonAliasCollision] = []

    def identity(obs: Observation) -> tuple[str, str]:
        if obs.category == "person" and obs.abbr_of:
            target = bucket.get(obs.abbr_of)
            if target is None or target.category == "person":
                return ("person", obs.abbr_of)
        return (obs.category, obs.original)

    person_aliases = {
        r for obs in bucket.values() if obs.category == "person" for r in obs.replacements_seen if r
    }
    by_alias: dict[str, list[Observation]] = {}
    for obs in sorted(bucket.values(), key=lambda o: o.original):
        replacements = {r for r in obs.replacements_seen if r}
        if len(replacements) > 1:
            conflicts.append(
                Conflict(original=obs.original, replacements=sorted(replacements), files=list(obs.files))
            )
        for alias in replacements & person_aliases:
            by_alias.setdefault(alias, []).append(obs)
    for alias, observations in sorted(by_alias.items()):
        if len({identity(obs) for obs in observations}) > 1:
            conflicts.append(
                PersonAliasCollision(
                    replacement=alias,
                    originals=[obs.original for obs in observations],
                    files=sorted({f for obs in observations for f in obs.files}),
                )
            )
    return conflicts


def build_unified_entities(
    bucket: dict[str, Observation],
    *,
    mode: str = "ai",
) -> list[dict[str, Any]]:
    """Assign one stable replacement per original (full names before abbreviations)."""
    mode = mode.lower().strip()
    items = sorted(
        bucket.values(),
        key=lambda o: (1 if o.abbr_of else 0, -len(o.original), o.original),
    )
    counters: dict[str, int] = {}
    assigned: dict[str, str] = {}
    reserved = {r for obs in items for r in obs.replacements_seen if r}
    entities: list[dict[str, Any]] = []

    for obs in items:
        category = obs.category or "other"
        role = obs.role or "unknown"
        # production: keep party person/org/address unless a replacement was already chosen
        if (
            mode == "production"
            and role == "party"
            and category in {"person", "organization", "address"}
            and not obs.replacements_seen
        ):
            # Still list for review, but no replacement — redact will keep them
            entities.append(
                {
                    "original": obs.original,
                    "category": category,
                    "role": role,
                    "source": "unified",
                    "notes": (
                        f"production keep candidate; seen in {len(obs.files)} file(s). "
                        "Add replacement only if you intentionally want it stripped."
                    ),
                    "files": list(obs.files),
                }
            )
            continue

        if obs.replacements_seen:
            # Prefer first non-empty; conflicts reported separately
            replacement = obs.replacements_seen[0]
        elif obs.abbr_of and obs.abbr_of in assigned:
            replacement = assigned[obs.abbr_of]
        else:
            idx = counters.get(category, 0)
            replacement = _stable_alias(category, idx, obs.original)
            if category in _INDEXED_ALIAS_CATEGORIES:
                while replacement in reserved:
                    idx += 1
                    replacement = _stable_alias(category, idx, obs.original)
            counters[category] = idx + 1

        assigned[obs.original] = replacement
        reserved.add(replacement)
        row: dict[str, Any] = {
            "original": obs.original,
            "category": category,
            "role": role if role != "structural" else "structural",
            "replacement": replacement,
            "source": "unified",
            "notes": (
                f"{_ABBR_NOTE_PREFIX}{obs.abbr_of}"
                if obs.abbr_of
                else f"stable across {len(obs.files)} file(s); from {obs.source}"
            ),
            "files": list(obs.files),
        }
        entities.append(row)

    # Drop helper keys not in schema when dumping pure entities list — keep files in _meta only
    return entities


def _entities_for_file(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip batch-only keys for a standard entities.json."""
    clean: list[dict[str, Any]] = []
    for e in entities:
        row = {
            "original": e["original"],
            "category": e.get("category", "other"),
            "role": e.get("role", "other"),
            "source": e.get("source", "unified"),
        }
        if e.get("replacement"):
            row["replacement"] = e["replacement"]
        if e.get("notes"):
            row["notes"] = e["notes"]
        clean.append(row)
    return clean


def write_unified_bundle(
    bucket: dict[str, Observation],
    output_dir: Path,
    *,
    mode: str = "ai",
    files_scanned: int = 0,
) -> ConsistencyReport:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    conflicts = find_conflicts(bucket)
    entities = build_unified_entities(bucket, mode=mode)
    entities_path = output_dir / "entities.consistent.json"
    report_path = output_dir / "consistency.report.json"

    payload = {
        "entities": _entities_for_file(entities),
        "_meta": {
            "mode": mode,
            "files_scanned": files_scanned,
            "entity_count": len(entities),
            "conflict_count": len(conflicts),
            "per_entity_files": {
                e["original"]: e.get("files", []) for e in entities if e.get("files")
            },
            "warning": (
                "Review before use. Resolve conflicting replacements or shared person aliases manually."
            ),
        },
    }
    entities_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    observations = sorted(bucket.values(), key=lambda o: o.original)
    report = ConsistencyReport(
        files_scanned=files_scanned,
        entity_count=len(entities),
        conflict_count=len(conflicts),
        observations=observations,
        conflicts=conflicts,
        entities_path=entities_path,
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Human markdown
    md_path = output_dir / "consistency.report.md"
    lines = [
        "# Entity consistency report",
        "",
        f"- files scanned: **{files_scanned}**",
        f"- unified entities: **{len(entities)}**",
        f"- conflicts: **{len(conflicts)}** ({'PASS' if not conflicts else 'FAIL'})",
        f"- entities file: `{entities_path.name}`",
        "",
    ]
    if conflicts:
        lines.extend(["## Conflicts", ""])
        for c in conflicts:
            lines.append(f"- {c.describe()}")
            lines.append(f"  - files: {', '.join(c.files)}")
        lines.append("")
    lines.extend(
        [
            "## Unified entities",
            "",
            "| original | replacement | category | role | files |",
            "|---|---|---|---|---|",
        ]
    )
    for e in entities:
        rep = e.get("replacement") or "*(keep)*"
        files = ", ".join(e.get("files") or [])
        lines.append(
            f"| `{e['original']}` | `{rep}` | {e.get('category')} | {e.get('role')} | {files} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Use `entities.consistent.json` as `--entities` for the whole batch.",
            "- Conflicts must be resolved before trusting cross-file anonymity.",
            "- production mode may list party rows without replacement (intentional keep).",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return report


def unify_directory(
    input_dir: Path,
    output_dir: Path,
    *,
    mode: str = "ai",
    entities_path: Path | None = None,
    recursive: bool = False,
    include_suspects: bool = True,
    preserve: list[str] | None = None,
    keep_categories: list[str] | set[str] | None = None,
    extra_categories: list[str] | set[str] | None = None,
    allowlist: set[str] | None = None,
    profile: str | None = None,
    redact_court_staff: bool | None = None,
) -> ConsistencyReport:
    """Scan a directory of source docs and write unified entities + report."""
    input_dir = Path(input_dir)
    paths = iter_batch_inputs(input_dir, recursive=recursive)
    eff_allow = allowlist if allowlist is not None else load_allowlist(search_dirs=[Path.cwd(), input_dir])
    maps = [
        collect_observations_from_file(
            p,
            mode=mode,
            entities_path=entities_path,
            include_suspects=include_suspects,
            preserve=preserve,
            keep_categories=keep_categories,
            extra_categories=extra_categories,
            allowlist=eff_allow,
            profile=profile,
            redact_court_staff=redact_court_staff,
        )
        for p in paths
    ]
    bucket = merge_observation_maps(maps)
    return write_unified_bundle(
        bucket, output_dir, mode=mode, files_scanned=len(paths)
    )


def unify_from_ledgers(
    ledger_dir: Path,
    output_dir: Path,
    *,
    mode: str = "ai",
) -> ConsistencyReport:
    """Merge already-written plain and encrypted per-file ledgers."""
    ledger_dir = Path(ledger_dir)
    patterns = ("*.ledger.json", "*.ledger.json.enc")
    ledgers = [path for pattern in patterns for path in ledger_dir.glob(pattern)]
    if not ledgers:
        ledgers = [path for pattern in patterns for path in ledger_dir.rglob(pattern)]

    newest: dict[Path, Path] = {}
    for path in ledgers:
        base = path.with_suffix("") if path.suffix == ".enc" else path
        previous = newest.get(base)
        # Encrypted copies are discovered second and win an exact timestamp tie.
        if previous is None or path.stat().st_mtime_ns >= previous.stat().st_mtime_ns:
            newest[base] = path
    return unify_ledger_paths(newest.values(), output_dir, mode=mode)


def unify_ledger_paths(
    ledger_paths: Iterable[Path],
    output_dir: Path,
    *,
    mode: str = "ai",
) -> ConsistencyReport:
    """Write the consistency bundle for an explicit set of ledger files."""
    ledgers = sorted(ledger_paths)
    bucket = collect_observations_from_ledgers(ledgers)
    return write_unified_bundle(
        bucket, output_dir, mode=mode, files_scanned=len(ledgers)
    )
