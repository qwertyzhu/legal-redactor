"""P1: ledger store/restore/encryption, profiles, audit, verify levels, CLI json, wrapper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from legal_redactor.cli import main
from legal_redactor.formats import docx_io
from legal_redactor.ledger_store import load_ledger, restore_text
from legal_redactor.pipeline import redact_file
from legal_redactor.profiles_loader import list_profiles, load_profile

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (
    "原告：张测伟，男，1985年3月12日出生，住北京市朝阳区测试路88号院3号楼501室，手机13912345678。\n"
    "被告：北京星测科技有限公司，法定代表人：李测强。\n"
    "请求赔偿人民币1,280,000元。依据《中华人民共和国著作权法》第十条。\n"
)


def _src(tmp_path: Path, text: str = SAMPLE, name: str = "c.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# 8. ledger ------------------------------------------------------------------

def test_default_ledger_in_home_store_private(tmp_path: Path):
    src = _src(tmp_path)
    out = tmp_path / "out" / "c.redacted.md"
    res = redact_file(src, mode="ai", output_path=out, auto_confident=True)
    root = Path(os.environ["LEGAL_REDACTOR_LEDGER_ROOT"])
    assert root in res.ledger_path.parents
    assert not (out.parent / "c.redacted.ledger.json").exists()
    assert (out.parent / "c.redacted.ledger-path.txt").is_file()
    if os.name == "posix":
        assert stat.S_IMODE(res.ledger_path.stat().st_mode) == 0o600
    data = load_ledger(res.ledger_path)
    assert any(e["original"] == "张测伟" for e in data["entities"])


def test_explicit_ledger_path_and_legacy_beside(tmp_path: Path):
    src = _src(tmp_path)
    custom = tmp_path / "custom" / "l.json"
    res = redact_file(src, mode="ai", output_path=tmp_path / "a.md", ledger=custom)
    assert res.ledger_path == custom and custom.is_file()
    res2 = redact_file(
        src, mode="ai", output_path=tmp_path / "b.md", ledger_beside_output=True
    )
    assert res2.ledger_path == tmp_path / "b.ledger.json"


def test_restore_roundtrip_text(tmp_path: Path):
    src = _src(tmp_path)
    out = tmp_path / "c.out.md"
    res = redact_file(src, mode="ai", output_path=out, auto_confident=True)
    reply = tmp_path / "reply.md"
    alias = next(e.replacement for e in res.plan.entities if e.original == "张测伟")
    org = next(e.replacement for e in res.plan.entities if e.original == "北京星测科技有限公司")
    reply.write_text(f"分析：{alias}可起诉{org}，联系[手机号]。", encoding="utf-8")
    restored = tmp_path / "reply.restored.md"
    rc = main(["restore", str(reply), "--ledger", str(res.ledger_path), "-o", str(restored)])
    assert rc == 0
    text = restored.read_text(encoding="utf-8")
    assert "张测伟" in text and "北京星测科技有限公司" in text and "13912345678" in text


def test_restore_warns_on_ambiguous_tokens():
    ledger = {"entities": [
        {"original": "甲公司全称", "replacement": "某单位A", "category": "organization"},
        {"original": "甲公司", "replacement": "某单位A", "category": "organization"},
    ]}
    out, warns = restore_text("某单位A违约", ledger)
    assert out == "甲公司全称违约"
    assert any("ambiguous" in w for w in warns)


def test_restore_docx(tmp_path: Path):
    src = tmp_path / "d.docx"
    docx_io.create_sample_docx(src, "原告：张测伟\n手机13912345678")
    res = redact_file(src, mode="ai", output_path=tmp_path / "d.out.docx", auto_confident=True)
    restored = tmp_path / "d.restored.docx"
    assert main(["restore", str(res.output_path), "--ledger", str(res.ledger_path), "-o", str(restored)]) == 0
    assert "张测伟" in docx_io.extract_text(restored)


def test_ledger_encrypt_roundtrip(tmp_path: Path, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setenv("LEGAL_REDACTOR_LEDGER_PASS", "fictional-pass")
    src = _src(tmp_path)
    res = redact_file(src, mode="ai", output_path=tmp_path / "e.md", ledger_encrypt=True, auto_confident=True)
    assert res.ledger_path.name.endswith(".enc")
    assert not res.ledger_path.with_name("ledger.json").exists()
    assert b"\xe5\xbc\xa0\xe6\xb5\x8b\xe4\xbc\x9f" not in res.ledger_path.read_bytes()  # 张测伟
    assert any(e["original"] == "张测伟" for e in load_ledger(res.ledger_path)["entities"])
    monkeypatch.setenv("LEGAL_REDACTOR_LEDGER_PASS", "wrong")
    with pytest.raises(Exception):
        load_ledger(res.ledger_path)


def test_ledger_encrypt_without_password_errors(tmp_path: Path):
    pytest.importorskip("cryptography")
    with pytest.raises(RuntimeError, match="LEGAL_REDACTOR_LEDGER_PASS"):
        redact_file(_src(tmp_path), mode="ai", output_path=tmp_path / "x.md", ledger_encrypt=True)


# 9. profiles ----------------------------------------------------------------

def test_builtin_profiles_present():
    assert {"ai", "production", "publish", "minimal"} <= set(list_profiles())
    assert load_profile("publish").alias_style == "court"
    assert load_profile("minimal").effective_categories() == {"id_card", "mobile", "email", "bank_account"}


def test_publish_profile_court_style(tmp_path: Path):
    res = redact_file(_src(tmp_path), mode="ai", output_path=tmp_path / "p.md", profile="publish")
    out = res.output_text
    assert "张某" in out and "李某" in out
    assert "张测伟" not in out
    assert "北京市朝阳区" in out and "测试路88号" not in out
    assert "1,280,000" not in out
    assert "《中华人民共和国著作权法》第十条" in out
    assert res.profile == "publish"


def test_minimal_profile_only_core(tmp_path: Path):
    text = "原告：张测伟，手机13912345678，座机010-88886666，车牌京A12345，案号（2024）京0105民初1号。"
    res = redact_file(_src(tmp_path, text), mode="ai", output_path=tmp_path / "m.md", profile="minimal")
    out = res.output_text
    assert "13912345678" not in out
    for keep in ("张测伟", "010-88886666", "京A12345", "（2024）京0105民初1号"):
        assert keep in out, keep
    assert res.ok


def test_unknown_profile_errors():
    with pytest.raises(FileNotFoundError):
        load_profile("nope")


# 10. audit + verify levels -------------------------------------------------

def test_audit_report_contents(tmp_path: Path):
    res = redact_file(_src(tmp_path), mode="ai", output_path=tmp_path / "a.md", auto_confident=True)
    data = json.loads(res.audit_json_path.read_text(encoding="utf-8"))
    assert data["status"] in {"PASS", "WARN", "FAIL"}
    row = next(e for e in data["entries"] if e["original"] == "13912345678")
    assert row["category"] == "mobile" and row["confidence"] == "high"
    assert row["rule"] == "structural:mobile"
    assert row["paragraph_index"] == 0
    assert row["context_before"].endswith("手机")
    assert "residual_risks" in data and "long_digit_strings" in data["residual_risks"]
    md = res.audit_md_path.read_text(encoding="utf-8")
    assert "Residual risks" in md


def test_verify_three_levels(tmp_path: Path, capsys):
    clean = _src(tmp_path, "无敏感信息。", "clean.md")
    warn = _src(tmp_path, "订单号 1234567890123456。", "warn.md")
    bad = _src(tmp_path, "手机13912345678。", "bad.md")
    assert main(["verify", str(clean), "--mode", "ai"]) == 0
    assert main(["verify", str(warn), "--mode", "ai"]) == 0
    assert main(["verify", str(warn), "--mode", "ai", "--strict"]) == 1
    assert main(["verify", str(bad), "--mode", "ai"]) == 2
    capsys.readouterr()
    main(["verify", str(warn), "--mode", "ai", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "WARN"


def test_verify_dir_skips_sidecars(tmp_path: Path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "x.md").write_text("无敏感信息", encoding="utf-8")
    (d / "x.ledger.json").write_text('{"entities":[{"original":"13912345678"}]}', encoding="utf-8")
    assert main(["verify", str(d), "--mode", "ai"]) == 0


# 12. CLI json / entry ------------------------------------------------------

def test_redact_json_output(tmp_path: Path, capsys):
    src = _src(tmp_path)
    rc = main(["redact", str(src), "--profile", "ai", "-o", str(tmp_path / "j.md"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["profile"] == "ai" and payload["status"] in {"PASS", "WARN"}
    for key in ("output", "ledger", "audit_json", "replaced", "suspect_count"):
        assert key in payload


def test_profiles_command_json(capsys):
    assert main(["profiles", "--json"]) == 0
    names = {r["name"] for r in json.loads(capsys.readouterr().out)}
    assert {"ai", "production", "publish", "minimal"} <= names


def test_redact_requires_mode_or_profile(tmp_path: Path):
    with pytest.raises(SystemExit):
        main(["redact", str(_src(tmp_path)), "-o", str(tmp_path / "z.md")])


def test_wrapper_prints_install_hint_when_package_missing(tmp_path: Path):
    wrapper = ROOT / "skills" / "legal-document-redactor" / "scripts" / "redact_cli.py"
    copy = tmp_path / "skills" / "x" / "scripts" / "redact_cli.py"
    copy.parent.mkdir(parents=True)
    copy.write_text(wrapper.read_text(encoding="utf-8"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH"}}
    proc = subprocess.run(
        [sys.executable, "-S", "-I", str(copy), "--version"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 3
    assert "pip install" in proc.stderr


# soft switches -----------------------------------------------------------

def test_preserve_hyperlinks_switch(tmp_path: Path):
    src = tmp_path / "h.docx"
    docx_io.create_sample_docx(src, "证据链接")
    tmp = tmp_path / "h2.docx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="https://example-fictional.test/infringing" TargetMode="External"/></Relationships>',
                )
            zout.writestr(item, data)
    kept = redact_file(tmp, mode="ai", output_path=tmp_path / "k.docx", preserve_hyperlinks=True)
    scrubbed = redact_file(tmp, mode="ai", output_path=tmp_path / "s.docx")
    with zipfile.ZipFile(kept.output_path) as z:
        assert b"example-fictional.test/infringing" in z.read("word/_rels/document.xml.rels")
    with zipfile.ZipFile(scrubbed.output_path) as z:
        assert b"example-fictional.test/infringing" not in z.read("word/_rels/document.xml.rels")
