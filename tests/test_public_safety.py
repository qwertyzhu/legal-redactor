from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Public tree must not ship live ledgers, secrets, or client dumps by filename.
FORBIDDEN_NAME_PARTS = (
    "clients.md",
    "credentials",
    ".env",
    "ledger.json",
)

# Content-level residual patterns that must not appear outside allowed fixtures.
# Examples/tests intentionally use fictional structural values; those paths are skipped.
_SKIP_CONTENT_PARTS = {
    ".git",
    "demo-output",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "examples",
    "tests",
}

# Pattern source / detector modules intentionally embed shape examples.
_SKIP_CONTENT_FILES = {
    "src/legal_redactor/patterns.py",
}

_TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".ps1",
    ".sh",
}

# Real-looking structural PII shapes that must not leak into docs/scripts.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID18 = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
_CASE_NO = re.compile(
    r"[（(]\d{4}[）)][^（）()\n]{0,20}?"
    r"(?:民|刑|行|执|知|商|赔|清|破|辖|认)"
    r"[^（）()\n]{0,12}?\d+号"
)
_WIN_USER = re.compile(r"(?i)C:\\Users\\(?!zhusi\b|Public\b|user\b|<)[^\\\s\"']+")
_UNIX_USER = re.compile(r"(?i)/Users/(?!shared\b|user\b|<)[^/\s\"']+")
_HOME_UNIX = re.compile(r"(?i)/home/(?!user\b|runner\b|<)[a-z0-9._-]+")

# Allowlist exact strings that are documentation placeholders, not live data.
_EMAIL_ALLOW = {
    "zhang@example.com",
    "noreply@anthropic.com",
    "example@example.com",
    "user@example.com",
    "name@example.com",
    "haoceyi@example-fictional.test",
    "shenlieer@example-fictional.test",
}

# In-repo fictional sample tokens. README before/after may show these;
# they are the same values as examples/fictional, not live client data.
_FICTIONAL_DEMO_TOKENS = {
    "13900001111",
    "13800002222",
    "110101199001011234",
    "（2024）京0491民初1234号",
    "(2024)京0491民初1234号",
    "（2025）京73民终678号",
    "330106198503121234",
    "138 0000 2222",
    "zhang@example.com",
}
_PATH_ALLOW_FRAGMENTS = (
    "C:\\Users\\<you>",
    "C:\\Users\\user",
    "/Users/<you>",
    "/Users/user",
    "/home/user",
    "$HOME",
    "%USERPROFILE%",
    "~/",
)


def _iter_public_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_CONTENT_PARTS for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in _SKIP_CONTENT_FILES:
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {
            "LICENSE",
            "NOTICE",
            "Dockerfile",
            "Makefile",
        }:
            continue
        yield path


def test_no_forbidden_filenames():
    bad = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or "demo-output" in path.parts or "dist" in path.parts:
            continue
        name = path.name.lower()
        for part in FORBIDDEN_NAME_PARTS:
            if part in name:
                bad.append(path)
    assert not bad, f"forbidden paths: {bad}"


def test_skill_md_exists():
    skill = ROOT / "skills" / "legal-document-redactor" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert "name: legal-document-redactor" in text
    assert "ai" in text and "production" in text
    assert "脱敏" in text


def test_public_tree_has_no_live_structural_pii():
    offenders: list[str] = []
    for path in _iter_public_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()

        for match in _EMAIL.finditer(text):
            val = match.group(0)
            if val.lower() in {e.lower() for e in _EMAIL_ALLOW}:
                continue
            # Skip regex source lines that define the pattern itself
            if "A-Za-z0-9._%+-" in text[max(0, match.start() - 40) : match.end() + 40]:
                continue
            offenders.append(f"{rel}: email {val}")

        for match in _MOBILE.finditer(text):
            val = match.group(0)
            if val in _FICTIONAL_DEMO_TOKENS:
                continue
            # Skip pattern definitions and documentation masks like 1[3-9]
            window = text[max(0, match.start() - 30) : match.end() + 30]
            if "1[3-9]" in window or r"\d{9}" in window:
                continue
            offenders.append(f"{rel}: mobile {val}")

        for match in _ID18.finditer(text):
            val = match.group(0)
            if val in _FICTIONAL_DEMO_TOKENS:
                continue
            window = text[max(0, match.start() - 40) : match.end() + 40]
            if r"\d{5}" in window or "19|20" in window:
                continue
            offenders.append(f"{rel}: id_card {val}")

        for match in _CASE_NO.finditer(text):
            val = match.group(0)
            if val in _FICTIONAL_DEMO_TOKENS:
                continue
            # Allow documentation masks with XX placeholders
            if "XX" in val or "20XX" in val:
                continue
            window = text[max(0, match.start() - 50) : match.end() + 20]
            if r"\d{4}" in window or "民|刑" in window:
                continue
            offenders.append(f"{rel}: case_number {val}")

        for match in _WIN_USER.finditer(text):
            val = match.group(0)
            if any(frag.lower() in val.lower() or frag in text for frag in _PATH_ALLOW_FRAGMENTS):
                # still flag concrete usernames not in allow fragments
                if re.search(r"(?i)C:\\Users\\(zhusi|Public|user|<)", val):
                    continue
            offenders.append(f"{rel}: win_user_path {val}")

        for match in _UNIX_USER.finditer(text):
            val = match.group(0)
            if re.search(r"(?i)/Users/(shared|user|<)", val):
                continue
            offenders.append(f"{rel}: unix_user_path {val}")

        for match in _HOME_UNIX.finditer(text):
            val = match.group(0)
            if re.search(r"(?i)/home/(user|runner|<)", val):
                continue
            offenders.append(f"{rel}: home_path {val}")

    assert not offenders, "public tree structural PII suspects:\n" + "\n".join(offenders[:40])


# Every organisation name in the public tree must be visibly fictional. A real
# client / opposing party name must never be shipped, not even in a test fixture
# or a CHANGELOG sentence — that is how one leaked in 0.18.1.
_SYNTHETIC_MARKERS = (
    "测",  # 北测 / 星测 / 杭州云测
    "例",  # 南例 / 东例
    "某某",  # 杭州某某网络科技有限公司
    "合成",
    "云帆",
    "星澜",
    "云海",
    "朗岳",
)
_GENERIC_ORG_ALLOW = {
    "股份有限公司",
    "集团有限公司",
    "有限责任公司",
    "银行股份有限公司",
    "文化有限公司",
    "律师事务所",
    "合作的律师事务所",
    "本所",
}
_ORG_TOKEN = re.compile(
    r"[·一-鿿A-Za-z0-9]{2,32}?"
    r"(?:股份有限公司|有限责任公司|有限公司|律师事务所|会计事务所)"
)
# The match above starts at the sentence start (role words included), so reduce
# every token to its last clean segment before judging it.
_ORG_TOKEN_NOISE = re.compile(
    r"(?:原告|被告|第三人|上诉人|被上诉人|申请人|被执行人|甲方|乙方|丙方|"
    r"赔偿|判令|本合同由|本公司|该公司|与|及|和|向|由|在|诉|、|，|。|：|“|”|（|）|\(|\))"
)
# Placeholder shapes that are obviously not a live party name.
_PLACEHOLDER_MARKERS = ("X", "Y", "Z", "x", "y", "某某", "甲", "乙", "丙")
# Names deliberately split across a line to exercise wrapped-name redaction.
_ORG_FRAGMENT_ALLOW = {
    "字娱乐发行股份有限公司",  # tail half of the wrapped synthetic name
}


def _org_name_is_fictional(token: str) -> bool:
    parts = [p for p in _ORG_TOKEN_NOISE.split(token) if p]
    bare = parts[-1] if parts else token
    if bare in _GENERIC_ORG_ALLOW or bare in _ORG_FRAGMENT_ALLOW:
        return True
    if any(marker in bare for marker in _SYNTHETIC_MARKERS):
        return True
    return any(marker in bare for marker in _PLACEHOLDER_MARKERS)


def test_public_org_names_are_fictional():
    offenders: list[str] = []
    for path in _iter_public_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for match in _ORG_TOKEN.finditer(text):
            token = match.group(0)
            bare = re.sub(r"^[（(“\"']+", "", token)
            if _org_name_is_fictional(bare):
                continue
            offenders.append(f"{rel}: {bare}")
    assert not offenders, (
        "public tree contains non-fictional organisation names "
        "(replace with 北测/星测/南例/某某 style):\n" + "\n".join(sorted(set(offenders))[:40])
    )
