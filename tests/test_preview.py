"""README dual-mode preview is generated from the shipped PDF redact path."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_demo_preview.py"
PNG = ROOT / "docs" / "images" / "dual-mode-preview.png"

SPEC = importlib.util.spec_from_file_location("legal_redactor_render_preview", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preview = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preview
SPEC.loader.exec_module(preview)


def test_committed_preview_png_exists() -> None:
    assert PNG.is_file()
    assert PNG.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert PNG.stat().st_size > 8_000


def test_render_preview_uses_shipped_redact(tmp_path: Path) -> None:
    out = tmp_path / "preview.png"
    try:
        preview.render_preview(out, tmp_path / "work")
    except RuntimeError as exc:
        pytest.skip(str(exc))
    assert out.is_file()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "work" / "sample.redacted-ai.md").is_file()
    assert (tmp_path / "work" / "sample.redacted-production.md").is_file()
    ai = (tmp_path / "work" / "sample.redacted-ai.md").read_text(encoding="utf-8")
    prod = (tmp_path / "work" / "sample.redacted-production.md").read_text(encoding="utf-8")
    assert "郝测一" not in ai
    assert "郝测一" in prod
    assert "13900001111" not in ai and "13900001111" not in prod
