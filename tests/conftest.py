from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_ledger_root(tmp_path, monkeypatch):
    """Never write test ledgers into the real ~/.legal-redactor."""
    monkeypatch.setenv("LEGAL_REDACTOR_LEDGER_ROOT", str(tmp_path / "_ledgers"))
    monkeypatch.delenv("LEGAL_REDACTOR_LEDGER_PASS", raising=False)
