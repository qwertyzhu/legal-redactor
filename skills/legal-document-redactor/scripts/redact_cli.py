#!/usr/bin/env python3
"""Thin wrapper so the skill works from a git clone without install."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
src = ROOT / "src"
if src.is_dir():
    sys.path.insert(0, str(src))

from legal_redactor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
