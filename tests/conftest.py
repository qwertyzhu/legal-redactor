from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_ledger_root(tmp_path, monkeypatch):
    """Never write test ledgers into the real ~/.legal-redactor."""
    monkeypatch.setenv("LEGAL_REDACTOR_LEDGER_ROOT", str(tmp_path / "_ledgers"))
    monkeypatch.delenv("LEGAL_REDACTOR_LEDGER_PASS", raising=False)


@pytest.fixture(autouse=True)
def _ner_off_by_default(monkeypatch):
    """Keep tests deterministic/fast even when the [ner] extra is installed."""
    from legal_redactor import ner

    monkeypatch.setenv("LEGAL_REDACTOR_NER", "off")
    ner.set_backend(None)
    yield
    ner.set_backend(None)
