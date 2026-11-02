"""Optional Chinese NER for un-anchored person names (``pip install legal-redactor[ner]``).

Results are *suspects only*: never auto-replaced. If no backend is installed,
``detect_ner_suspects`` returns ``[]`` and ``ner_available()`` is False.

Backend order: LTP (``ltp``, lighter: LTP/tiny model) then HanLP (``hanlp``).
Override with env ``LEGAL_REDACTOR_NER=ltp|hanlp|off`` and model with
``LEGAL_REDACTOR_NER_MODEL`` (default ``LTP/tiny``).
"""

from __future__ import annotations

import os
from typing import Callable, Iterable

from .suspects import Suspect

# A backend takes text and returns (name, start, end) person spans.
Backend = Callable[[str], list[tuple[str, int, int]]]
_BACKEND: Backend | None = None
_LOADED = False
_ERROR: str | None = None


def _ltp_backend() -> Backend:
    from ltp import LTP  # type: ignore

    model = LTP(os.environ.get("LEGAL_REDACTOR_NER_MODEL", "LTP/tiny"))

    def run(text: str) -> list[tuple[str, int, int]]:
        out: list[tuple[str, int, int]] = []
        for line_start, line in _lines(text):
            if not line.strip():
                continue
            res = model.pipeline([line], tasks=["cws", "ner"])
            for tag, name, *_ in res.ner[0]:
                if tag == "Nh":
                    idx = line.find(name)
                    if idx >= 0:
                        out.append((name, line_start + idx, line_start + idx + len(name)))
        return out

    return run


def _hanlp_backend() -> Backend:
    import hanlp  # type: ignore

    tok = hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH)
    ner = hanlp.load(hanlp.pretrained.ner.MSRA_NER_ELECTRA_SMALL_ZH)

    def run(text: str) -> list[tuple[str, int, int]]:
        out: list[tuple[str, int, int]] = []
        for line_start, line in _lines(text):
            if not line.strip():
                continue
            words = tok(line)
            for name, tag, *_ in ner(words):
                if tag in ("PERSON", "NR"):
                    idx = line.find(name)
                    if idx >= 0:
                        out.append((name, line_start + idx, line_start + idx + len(name)))
        return out

    return run


def _lines(text: str) -> Iterable[tuple[int, str]]:
    pos = 0
    for line in text.split("\n"):
        yield pos, line
        pos += len(line) + 1


def set_backend(backend: Backend | None) -> None:
    """Inject a backend (tests / custom models). ``None`` resets to auto-detect."""
    global _BACKEND, _LOADED, _ERROR
    _BACKEND, _LOADED, _ERROR = backend, backend is not None, None


def _load() -> Backend | None:
    global _BACKEND, _LOADED, _ERROR
    if _LOADED:
        return _BACKEND
    _LOADED = True
    choice = os.environ.get("LEGAL_REDACTOR_NER", "auto").lower()
    if choice == "off":
        return None
    loaders = {"ltp": _ltp_backend, "hanlp": _hanlp_backend}
    order = [choice] if choice in loaders else ["ltp", "hanlp"]
    errors = []
    for name in order:
        try:
            _BACKEND = loaders[name]()
            return _BACKEND
        except Exception as exc:  # ImportError, model download failure, ...
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    _ERROR = "; ".join(errors)
    return None


def ner_available() -> bool:
    return _load() is not None


def ner_status() -> str:
    if _load() is not None:
        return "available"
    return f"unavailable ({_ERROR or 'disabled'}) — pip install 'legal-redactor[ner]'"


def detect_ner_suspects(
    text: str,
    *,
    known: Iterable[str] | None = None,
) -> list[Suspect]:
    """Person names found by NER that are not in ``known``. Never auto-replaced."""
    backend = _load()
    if backend is None or not text:
        return []
    skip = {k for k in (known or []) if k}
    seen: set[str] = set()
    out: list[Suspect] = []
    try:
        spans = backend(text)
    except Exception:
        return []
    for name, start, end in spans:
        name = name.strip()
        if len(name) < 2 or name in skip or name in seen:
            continue
        if any(name in k or k in name for k in skip if len(k) >= 2):
            continue
        seen.add(name)
        out.append(
            Suspect(
                text=name,
                category="person",
                reason="ner",
                start=start,
                end=end,
                role_hint="unknown",
            )
        )
    return out
