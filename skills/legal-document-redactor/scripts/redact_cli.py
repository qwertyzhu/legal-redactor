#!/usr/bin/env python3
"""Thin wrapper so the skill works from a git clone, with a clear install hint."""

from __future__ import annotations

import sys
from pathlib import Path

INSTALL_HINT = (
    "legal-redactor Python package is not installed.\n"
    "Install one of:\n"
    "  python -m pip install legal-redactor\n"
    "  python -m pip install -e /path/to/legal-redactor   # from a git clone\n"
    "Then re-run, or call `legal-redactor --version` to confirm."
)


def _bootstrap() -> int:
    root = Path(__file__).resolve().parents[3]
    src = root / "src"
    if (src / "legal_redactor").is_dir():
        sys.path.insert(0, str(src))
    try:
        from legal_redactor.cli import main
    except ImportError as exc:
        print(f"ERROR: {exc}\n\n{INSTALL_HINT}", file=sys.stderr)
        return 3
    return int(main() or 0)


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
