"""Preserve lists for statutes, courts, and user-supplied literals."""

from __future__ import annotations

import re
from pathlib import Path

# Seed entries also shipped as references/allowlist.default.txt
_DEFAULT_ENTRIES: tuple[str, ...] = (
    # Courts / procuratorates / arbitration
    "最高人民法院",
    "最高人民检察院",
    "北京互联网法院",
    "杭州互联网法院",
    "广州互联网法院",
    "人民法院",
    "人民检察院",
    "仲裁委员会",
    "中国国际经济贸易仲裁委员会",
    "北京仲裁委员会",
    "上海国际经济贸易仲裁委员会",
    # Core statutes (book-title form)
    "《中华人民共和国宪法》",
    "《中华人民共和国民法典》",
    "《中华人民共和国刑法》",
    "《中华人民共和国公司法》",
    "《中华人民共和国著作权法》",
    "《中华人民共和国商标法》",
    "《中华人民共和国专利法》",
    "《中华人民共和国反不正当竞争法》",
    "《中华人民共和国劳动合同法》",
    "《中华人民共和国劳动法》",
    "《中华人民共和国民事诉讼法》",
    "《中华人民共和国刑事诉讼法》",
    "《中华人民共和国行政诉讼法》",
    "《中华人民共和国个人信息保护法》",
    "《中华人民共和国网络安全法》",
    "《中华人民共和国数据安全法》",
    "《中华人民共和国电子商务法》",
    "《中华人民共和国合同法》",
    "《中华人民共和国侵权责任法》",
    "《中华人民共和国物权法》",
    "《中华人民共和国担保法》",
    "《中华人民共和国仲裁法》",
    "《中华人民共和国律师法》",
    "《中华人民共和国公证法》",
    "《中华人民共和国企业破产法》",
    "《中华人民共和国证券法》",
    "《中华人民共和国保险法》",
    "《中华人民共和国票据法》",
    "《中华人民共和国广告法》",
    "《中华人民共和国消费者权益保护法》",
    "《中华人民共和国反垄断法》",
    "《中华人民共和国行政处罚法》",
    "《中华人民共和国行政许可法》",
    "《中华人民共和国行政复议法》",
    "《中华人民共和国国家赔偿法》",
    "《中华人民共和国涉外民事关系法律适用法》",
    "《中华人民共和国涉外民事关系法律适用法》",
    # Common short / judicial-interpretation titles
    "《最高人民法院关于适用〈中华人民共和国民法典〉时间效力的若干规定》",
    "《最高人民法院关于审理著作权民事纠纷案件适用法律若干问题的解释》",
    "《最高人民法院关于审理侵害信息网络传播权民事纠纷案件适用法律若干问题的规定》",
    "《最高人民法院关于人民法院在互联网公布裁判文书的规定》",
)

_STATUTE_TITLE = re.compile(
    r"^《(?:中华人民共和国)?[^》]{1,40}?(?:法|条例|规定|解释|办法|决定|意见|批复|公约)》$"
)
_ARTICLE_AFTER = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+条")


def is_statute_title(book_title: str, *, following: str = "") -> bool:
    """True if 《…》 looks like a law/regulation, or is followed by 第X条."""
    text = book_title.strip()
    if _STATUTE_TITLE.match(text):
        return True
    if following and _ARTICLE_AFTER.match(following.lstrip()):
        return True
    return False


def _read_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s)
    return out


def default_allowlist_path() -> Path | None:
    """Skill reference file when running from a source checkout."""
    here = Path(__file__).resolve()
    # src/legal_redactor/allowlist.py → repo root
    candidate = (
        here.parents[2]
        / "skills"
        / "legal-document-redactor"
        / "references"
        / "allowlist.default.txt"
    )
    return candidate if candidate.is_file() else None


def load_allowlist(
    *,
    extra_files: list[Path] | None = None,
    search_dirs: list[Path] | None = None,
    include_defaults: bool = True,
) -> set[str]:
    """Merge seed + default file + .redactor/allow.txt (cwd/input dir) + --allowlist files."""
    entries: set[str] = set()
    if include_defaults:
        entries |= set(_DEFAULT_ENTRIES)
        default_path = default_allowlist_path()
        if default_path is not None:
            entries |= _read_lines(default_path)
    dirs = list(search_dirs) if search_dirs is not None else [Path.cwd()]
    seen: set[Path] = set()
    for base in dirs:
        try:
            key = Path(base).resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        entries |= _read_lines(key / ".redactor" / "allow.txt")
    for path in extra_files or []:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"allowlist file not found: {path}")
        entries |= _read_lines(path)
    return entries
