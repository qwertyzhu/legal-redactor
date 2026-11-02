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


def test_cli_directory_does_not_merge_unrelated_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "inbox"
    src.mkdir()
    (src / "a.md").write_text("法定代表人：郝测一\n证人李测二\n电话 13900001111\n", encoding="utf-8")
    (src / "b.md").write_text("法定代表人：李测二\n证人郝测一\n手机 13900001111\n", encoding="utf-8")
    out = tmp_path / "out"
    code = main(["redact", str(src), "--mode", "ai", "--auto-confident", "-o", str(out)])
    assert code == 0
    a = (out / "a.redacted-ai.md").read_text(encoding="utf-8")
    b = (out / "b.redacted-ai.md").read_text(encoding="utf-8")
    assert "郝测一" not in a and "李测二" not in a
    assert "郝测一" not in b and "李测二" not in b
    # Without a case file, the first person in each file keeps a local alias.
    assert "某甲" in a and "某乙" in a
    assert a.index("某甲") < a.index("某乙")
    assert b.index("某甲") < b.index("某乙")


def test_cli_directory_unifies_when_case_file_exists(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "case"
    src.mkdir()
    (src / "entities.json").write_text('{"entities":[]}\n', encoding="utf-8")
    (src / "a.md").write_text("法定代表人：郝测一\n证人李测二\n电话 13900001111\n", encoding="utf-8")
    (src / "b.md").write_text("法定代表人：李测二\n证人郝测一\n手机 13900001111\n", encoding="utf-8")
    out = tmp_path / "out"
    code = main(["redact", str(src), "--mode", "ai", "--auto-confident", "-o", str(out)])
    assert code == 0
    a = (out / "a.redacted-ai.md").read_text(encoding="utf-8")
    b = (out / "b.redacted-ai.md").read_text(encoding="utf-8")
    entities = (src / ".redactor" / "entities.consistent.json").read_text(encoding="utf-8")
    assert "郝测一" in entities and "李测二" in entities
    # Same person, same alias, regardless of which file names them first.
    import json
    import re

    body = json.loads(entities)
    alias = {item["original"]: item["replacement"] for item in body["entities"]}
    assert alias["郝测一"] in a and alias["郝测一"] in b
    assert alias["李测二"] in a and alias["李测二"] in b
    assert alias["郝测一"] != alias["李测二"]
    assert not re.search(r"13900001111", a + b)
