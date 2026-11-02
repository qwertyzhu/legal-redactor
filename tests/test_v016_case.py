"""One entities.json per case; batch ledgers stay out of the deliverable folder."""

import json
from pathlib import Path

from legal_redactor.cli import main
from legal_redactor.pipeline import find_case_entities, iter_batch_inputs, redact_tree


def test_find_case_entities_prefers_the_folder(tmp_path: Path, monkeypatch):
    case = tmp_path / "case"
    case.mkdir()
    (case / "entities.json").write_text('{"entities":[]}\n', encoding="utf-8")
    (case / "a.md").write_text("手机 13900001111\n", encoding="utf-8")
    other = tmp_path / "entities.json"
    other.write_text('{"entities":[]}\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert find_case_entities(case / "a.md") == case / "entities.json"
    assert find_case_entities(case) == case / "entities.json"


def test_batch_skips_entities_and_keeps_ledgers_private(tmp_path: Path):
    src = tmp_path / "case"
    out = tmp_path / "out"
    src.mkdir()
    (src / "a.md").write_text("手机 13900001111，见于云帆视频。\n", encoding="utf-8")
    (src / "entities.json").write_text(
        json.dumps({"entities": [{"original": "云帆视频", "category": "other", "replacement": "某平台"}]}),
        encoding="utf-8",
    )
    (src / ".redactor").mkdir()
    (src / ".redactor" / "old.md").write_text("手机 13800002222\n", encoding="utf-8")
    names = {p.name for p in iter_batch_inputs(src)}
    assert names == {"a.md"}
    batch = redact_tree(
        src,
        mode="ai",
        output_dir=out,
        entities_path=src / "entities.json",
        auto_confident=True,
    )
    assert batch.ok
    assert "13900001111" not in (out / "a.redacted-ai.md").read_text(encoding="utf-8")
    assert "云帆视频" not in (out / "a.redacted-ai.md").read_text(encoding="utf-8")
    assert not list(out.glob("*.ledger.json"))
    assert list((src / ".redactor").glob("*.ledger.json"))
    assert not (out / "entities.json").exists()


def test_cli_directory_unifies_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "case"
    src.mkdir()
    (src / "a.md").write_text("法定代表人：郝测一\n电话 13900001111\n", encoding="utf-8")
    (src / "b.md").write_text("法定代表人：郝测一\n手机 13900001111\n", encoding="utf-8")
    out = tmp_path / "out"
    code = main(["redact", str(src), "--mode", "ai", "--auto-confident", "-o", str(out)])
    assert code == 0
    texts = [(out / name).read_text(encoding="utf-8") for name in ("a.redacted-ai.md", "b.redacted-ai.md")]
    assert all("郝测一" not in text for text in texts)
    assert all("13900001111" not in text for text in texts)
    assert (src / ".redactor" / "entities.consistent.json").is_file()
    assert not list(out.glob("*.ledger.json"))
