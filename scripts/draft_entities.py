#!/usr/bin/env python3
"""Draft a starter entities.json from structural hits.

Thin wrapper around `legal-redactor draft-entities` for git-clone workflows.

Usage:
  python scripts/draft_entities.py path/to/doc.docx -o entities.draft.json
  legal-redactor draft-entities path/to/doc.docx -o entities.draft.json
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_redactor.cli import main  # noqa: E402


if __name__ == "__main__":
    argv = ["draft-entities", *sys.argv[1:]]
    raise SystemExit(main(argv))
