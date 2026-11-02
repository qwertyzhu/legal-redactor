"""Named redaction profiles (ai / production / publish / judgment-online / minimal)."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .patterns import STRUCTURAL_CATEGORIES, categories_for_mode


@dataclass
class Profile:
    name: str
    description: str = ""
    mode: str = "ai"
    categories: set[str] | None = None  # if set, exact set to redact
    keep_categories: set[str] = field(default_factory=set)
    extra_categories: set[str] = field(default_factory=set)
    alias_style: str = "generic"  # generic | court
    address_granularity: str = "full"  # full | district
    truncate_addresses: bool = False  # production 下也把带标签地址按 granularity 替换
    redact_third_party: bool = True  # production 下是否自动替换证人等第三人
    amount_fuzzy: bool = True
    redact_court_staff: bool = False  # 未写该键时保留；ai.yaml 写 true
    preserve: list[str] = field(default_factory=list)
    auto_confident: bool = False

    def effective_categories(self) -> set[str]:
        if self.categories is not None:
            cats = {c.lower() for c in self.categories}
            unknown = cats - STRUCTURAL_CATEGORIES
            if unknown:
                raise ValueError(f"profile {self.name}: unknown categories {sorted(unknown)}")
            return cats
        return categories_for_mode(
            self.mode,
            keep_categories=self.keep_categories,
            extra_categories=self.extra_categories,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "categories": sorted(self.categories) if self.categories is not None else None,
            "keep_categories": sorted(self.keep_categories),
            "extra_categories": sorted(self.extra_categories),
            "alias_style": self.alias_style,
            "address_granularity": self.address_granularity,
            "truncate_addresses": self.truncate_addresses,
            "redact_third_party": self.redact_third_party,
            "amount_fuzzy": self.amount_fuzzy,
            "redact_court_staff": self.redact_court_staff,
            "preserve": list(self.preserve),
            "auto_confident": self.auto_confident,
        }


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML subset: key: value, key: null, key: [a, b], nested lists with '-'."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Fallback hand parser for our flat profiles
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip().startswith("- ") and current_list_key:
            out.setdefault(current_list_key, []).append(line.strip()[2:].strip().strip("'\""))
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        current_list_key = None
        if val == "" or val == "|" or val == ">":
            current_list_key = key
            out[key] = []
            continue
        if val.lower() in {"null", "~", ""}:
            out[key] = None
        elif val.lower() == "true":
            out[key] = True
        elif val.lower() == "false":
            out[key] = False
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()] if inner else []
        else:
            out[key] = val.strip("'\"" )
    return out


def _profile_from_dict(data: dict[str, Any], *, fallback_name: str) -> Profile:
    cats = data.get("categories")
    cat_set: set[str] | None
    if cats is None:
        cat_set = None
    else:
        cat_set = {str(c).strip().lower() for c in cats if str(c).strip()}
    keep = {str(c).strip().lower() for c in (data.get("keep_categories") or []) if str(c).strip()}
    extra = {str(c).strip().lower() for c in (data.get("extra_categories") or []) if str(c).strip()}
    preserve = [str(p) for p in (data.get("preserve") or []) if str(p).strip()]
    return Profile(
        name=str(data.get("name") or fallback_name),
        description=str(data.get("description") or ""),
        mode=str(data.get("mode") or "ai").lower(),
        categories=cat_set,
        keep_categories=keep,
        extra_categories=extra,
        alias_style=str(data.get("alias_style") or "generic").lower(),
        address_granularity=str(data.get("address_granularity") or "full").lower(),
        truncate_addresses=bool(data.get("truncate_addresses", False)),
        redact_third_party=bool(data.get("redact_third_party", True)),
        amount_fuzzy=bool(data.get("amount_fuzzy", True)),
        redact_court_staff=bool(data.get("redact_court_staff", False)),
        preserve=preserve,
        auto_confident=bool(data.get("auto_confident", False)),
    )


def profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def list_profiles() -> list[str]:
    names = sorted(p.stem for p in profiles_dir().glob("*.yaml"))
    return names


def load_profile(name: str) -> Profile:
    name = name.strip().lower()
    path = profiles_dir() / f"{name}.yaml"
    if not path.is_file():
        # package resources fallback
        try:
            ref = resources.files("legal_redactor").joinpath("profiles", f"{name}.yaml")
            text = ref.read_text(encoding="utf-8")
        except Exception as exc:
            known = ", ".join(list_profiles()) or "(none)"
            raise FileNotFoundError(f"unknown profile {name!r}; known: {known}") from exc
    else:
        text = path.read_text(encoding="utf-8")
    data = _parse_simple_yaml(text)
    return _profile_from_dict(data, fallback_name=name)


def resolve_profile(
    *,
    profile: str | None,
    mode: str | None,
) -> Profile:
    """CLI helper: --profile wins; else map --mode to built-in profile."""
    if profile:
        return load_profile(profile)
    if mode:
        # ai/production map to same-named profiles for compat
        try:
            return load_profile(mode)
        except FileNotFoundError:
            return Profile(name=mode, mode=mode)
    return load_profile("ai")
