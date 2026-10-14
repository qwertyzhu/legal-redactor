"""Regression corpus: tests/corpus/*.txt + *.expected.json, with precision/recall floors."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from legal_redactor.pipeline import redact_file

CORPUS = Path(__file__).parent / "corpus"
CASES = sorted(p.stem for p in CORPUS.glob("*.txt"))

# Floors apply over the whole corpus (must_remove = recall, must_keep = precision proxy)
RECALL_FLOOR = 0.95
KEEP_FLOOR = 1.0


def _run(case: str, tmp_path: Path):
    spec = json.loads((CORPUS / f"{case}.expected.json").read_text(encoding="utf-8"))
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / f"{case}.txt"
    shutil.copyfile(CORPUS / f"{case}.txt", src)
    res = redact_file(
        src,
        mode=spec["mode"],
        output_path=tmp_path / f"{case}.out.txt",
        auto_confident=spec.get("auto_confident", True),
        use_default_allowlist=True,
    )
    return spec, res


@pytest.mark.parametrize("case", CASES)
def test_corpus_case(case: str, tmp_path: Path):
    spec, res = _run(case, tmp_path)
    out = res.output_text
    missed = [s for s in spec["must_remove"] if s in out]
    lost = [s for s in spec["must_keep"] if s not in out]
    absent = [s for s in spec.get("must_contain", []) if s not in out]
    assert not missed, f"{case}: still present {missed}"
    assert not lost, f"{case}: over-redacted {lost}"
    assert not absent, f"{case}: expected substrings missing {absent}"
    assert res.status in spec["residual_status"], (case, res.status, res.residual.summary)
    assert res.audit_json_path and res.audit_json_path.is_file()


def test_corpus_floors(tmp_path: Path):
    removed = total_remove = kept = total_keep = 0
    for case in CASES:
        spec, res = _run(case, tmp_path / case)
        out = res.output_text
        total_remove += len(spec["must_remove"])
        removed += sum(1 for s in spec["must_remove"] if s not in out)
        total_keep += len(spec["must_keep"])
        kept += sum(1 for s in spec["must_keep"] if s in out)
    recall = removed / total_remove if total_remove else 1.0
    keep_rate = kept / total_keep if total_keep else 1.0
    assert recall >= RECALL_FLOOR, f"recall {recall:.3f} < {RECALL_FLOOR}"
    assert keep_rate >= KEEP_FLOOR, f"keep rate {keep_rate:.3f} < {KEEP_FLOOR}"


def test_corpus_has_required_genres():
    assert {"complaint", "judgment", "contract", "lawyer_letter", "negatives"} <= set(CASES)
