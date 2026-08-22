"""Cross-file entity consistency: unify aliases and report conflicts.

Used for multi-document matters so the same original string always maps to the
same replacement. Never invents legal judgment — only stable aliases and diffs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .draft import extract_text
from .entities import load_entities_file
from .patterns import DEFAULT_PLACEHOLDERS, detect_structural
from .pipeline import iter_batch_inputs
from .suspects import detect_suspects

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
    base = DEFAULT_PLACEHOLDERS.get(category, DEFAULT_PLACEHOLDERS["other"])
    if category in {"mobile", "landline", "email", "id_card", "bank_account", "uscc", "case_number"}:
        return base if index == 0 else f"{base.rstrip(']')}{index + 1}]"
    return base


@dataclass
class Observation:
    original: str
    category: str
    role: str
    source: str
    files: list[str] = field(default_factory=list)
    replacements_seen: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Conflict:
    original: str
    replacements: list[str]
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConsistencyReport:
    files_scanned: int
    entity_count: int
    conflict_count: int
    observations: list[Observation]
    conflicts: list[Conflict]
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


def collect_observations_from_file(
    path: Path,
    *,
    entities_path: Path | None = None,
    include_suspects: bool = True,
) -> dict[str, Observation]:
    """Scan one source document into original→observation map."""
    path = Path(path)
    text = extract_text(path)
    label = path.name
    bucket: dict[str, Observation] = {}

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

    for hit in detect_structural(text):
        _add_obs(
            bucket,
            original=hit.text,
            category=hit.category,
            role="structural",
            source="structural",
            file_label=label,
        )

    if include_suspects:
        known = set(bucket)
        for s in detect_suspects(text, known=known):
            _add_obs(
                bucket,
                original=s.text,
                category=s.category,
                role=s.role_hint if s.role_hint != "unknown" else "other",
                source="suspect-hint",
                file_label=label,
            )
    return bucket


def collect_observations_from_ledgers(ledger_paths: Iterable[Path]) -> dict[str, Observation]:
    """Rebuild observations from per-file ledger.json outputs."""
    bucket: dict[str, Observation] = {}
    for path in ledger_paths:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
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
                )
                continue
            for f in obs.files:
                if f not in existing.files:
                    existing.files.append(f)
            for r in obs.replacements_seen:
                if r not in existing.replacements_seen:
                    existing.replacements_seen.append(r)
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


def find_conflicts(bucket: dict[str, Observation]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for obs in bucket.values():
        uniq = [r for r in obs.replacements_seen if r]
        if len(set(uniq)) > 1:
            conflicts.append(
                Conflict(original=obs.original, replacements=sorted(set(uniq)), files=list(obs.files))
            )
    conflicts.sort(key=lambda c: c.original)
    return conflicts


def build_unified_entities(
    bucket: dict[str, Observation],
    *,
    mode: str = "ai",
) -> list[dict[str, Any]]:
    """Assign one stable replacement per original (longest originals first for readability)."""
    mode = mode.lower().strip()
    items = sorted(bucket.values(), key=lambda o: (-len(o.original), o.original))
    counters: dict[str, int] = {}
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
        else:
            idx = counters.get(category, 0)
            replacement = _stable_alias(category, idx)
            counters[category] = idx + 1

        row: dict[str, Any] = {
            "original": obs.original,
            "category": category,
            "role": role if role != "structural" else "structural",
            "replacement": replacement,
            "source": "unified",
            "notes": f"stable across {len(obs.files)} file(s); from {obs.source}",
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
                "Review before use. Conflicts mean the same original had multiple "
                "replacements across inputs — resolve manually."
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
        lines.extend(["## Conflicts (same original → different replacements)", ""])
        for c in conflicts:
            lines.append(f"- `{c.original}`: {', '.join(f'`{r}`' for r in c.replacements)}")
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
) -> ConsistencyReport:
    """Scan a directory of source docs and write unified entities + report."""
    input_dir = Path(input_dir)
    paths = iter_batch_inputs(input_dir, recursive=recursive)
    maps = [
        collect_observations_from_file(
            p, entities_path=entities_path, include_suspects=include_suspects
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
    """Merge already-written *.ledger.json files (post-batch check)."""
    ledger_dir = Path(ledger_dir)
    ledgers = sorted(ledger_dir.glob("*.ledger.json"))
    if not ledgers:
        ledgers = sorted(ledger_dir.rglob("*.ledger.json"))
    bucket = collect_observations_from_ledgers(ledgers)
    return write_unified_bundle(
        bucket, output_dir, mode=mode, files_scanned=len(ledgers)
    )
