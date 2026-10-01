#!/usr/bin/env python3
"""ollama_entities.py — 用本地 Ollama 模型识别法律文书中的自然语言实体，产出 entities 草稿。

设计原则：本地模型只负责「找」，替换永远由 legal-redactor CLI 确定性执行。
产出是**草稿**（source=ollama-draft），替换前必须人工或 Agent 确认角色与替身。

用法：
    python3 ollama_entities.py INPUT.docx -o entities.ollama.draft.json
    python3 ollama_entities.py INPUT.pdf  -o entities.ollama.draft.json --model qwen3.5:latest
    python3 ollama_entities.py ocr.normalized.md -o entities.json --yes   # 已确认可直接用

支持：.docx / 文字层 .pdf / .txt / .md。扫描件 PDF 请先 `legal-redactor ocr`。
依赖：本机 Ollama（http://localhost:11434）+ 已 pull 的模型；docx/pdf 需 python-docx / pymupdf。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

VALID_CATEGORIES = {
    "person", "organization", "address", "work_title", "amount", "other",
    "id_card", "mobile", "landline", "email", "bank_account", "case_number", "uscc",
}
VALID_ROLES = {"party", "third_party", "counsel", "other", "unknown"}
# 结构性字段由 CLI 自动检测，本地模型不必再报
SKIP_CATEGORIES = {"id_card", "mobile", "landline", "email", "bank_account", "case_number", "uscc"}

PROMPT = """你是法律文书实体抽取器。从下面的中国法律文书片段中逐字抽取需要脱敏的实体。

必须逐类检查，一类都不能漏：
- person：每个自然人姓名（原告、被告、证人、律师、法定代表人等，不含「某甲」类已脱敏替身）
- organization：公司、机构全称或简称（不含法院名称）
- address：每个具体地址（住址、经营场所，含「同上xx」类指代）
- work_title：《》书名号内的作品名、文件名
- amount：每个精确金额数字（如 1280000）
- other：出生日期（如 1985年3月12日出生）、车牌号（如 浙A12345）、不动产权证号、健康状况等

示例：「原告张测三，男，1985年3月12日出生，住杭州市西湖区文三路100号，车牌号浙A12345」
应抽出：张测三(person)、1985年3月12日出生(other)、杭州市西湖区文三路100号(address)、浙A12345(other)

角色 role：party=当事人，third_party=第三人/证人，counsel=代理人/律师，other=其他，unknown=拿不准。
不要抽取：身份证号、手机号、邮箱、银行账号、统一社会信用代码、案号（这些由确定性扫描处理）；不要抽取法院名称、法条引用、通用法律术语。
original 必须是原文中**逐字出现**的字符串，不要改写、不要合并、不要加标点。

只输出 JSON：{"entities":[{"original":"...","category":"...","role":"...","notes":"可选"}]}，没有实体就输出 {"entities":[]}。

文书片段：
---
%s
---"""


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs if p.text.strip())
    if suffix == ".pdf":
        import pymupdf
        text = "\n".join(page.get_text() for page in pymupdf.open(str(path)))
        if not text.strip():
            sys.exit("错误：该 PDF 没有文字层。请先 legal-redactor ocr 后对生成的 md 运行本脚本。")
        return text
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    sys.exit(f"错误：不支持的格式 {suffix}（支持 .docx/.pdf/.txt/.md）")


def chunk_text(text: str, size: int = 2500) -> list[str]:
    paras, chunks, cur = text.split("\n"), [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            chunks.append(cur)
            cur = ""
        cur += p + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def find_verbatim(original: str, chunk: str) -> str | None:
    """原文逐字校验。容忍模型在字符间插入空格：命中则返回 chunk 中的真实字符串。"""
    if original in chunk:
        return original
    compact = re.sub(r"\s+", "", original)
    if not compact:
        return None
    pattern = r"\s*".join(re.escape(ch) for ch in compact)
    m = re.search(pattern, chunk)
    return m.group(0) if m else None


def call_ollama(base_url: str, model: str, prompt: str, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return json.loads(data["message"]["content"])


def main() -> None:
    ap = argparse.ArgumentParser(description="本地 Ollama 实体识别 → entities 草稿")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--model", default="qwen3.5:latest", help="Ollama 模型（默认 qwen3.5:latest）")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--yes", action="store_true", help="不再提醒「草稿需确认」（确认后直接用）")
    args = ap.parse_args()

    text = extract_text(args.input)
    chunks = chunk_text(text)
    merged: dict[str, dict] = {}
    for i, chunk in enumerate(chunks, 1):
        print(f"[{i}/{len(chunks)}] 识别中（{len(chunk)} 字）...", file=sys.stderr)
        try:
            result = call_ollama(args.base_url, args.model, PROMPT % chunk, args.timeout)
        except urllib.error.URLError as e:
            sys.exit(f"错误：连不上 Ollama（{args.base_url}）：{e}\n先运行 ollama serve，并 ollama pull {args.model}")
        except (json.JSONDecodeError, KeyError):
            print(f"  警告：第 {i} 块返回了非法 JSON，已跳过该块", file=sys.stderr)
            continue
        for ent in result.get("entities", []):
            original_raw = (ent.get("original") or "").strip()
            category = ent.get("category") or ""
            if not original_raw or category not in VALID_CATEGORIES or category in SKIP_CATEGORIES:
                continue
            original = find_verbatim(original_raw, chunk)  # 模型改写/加空格的丢弃或还原
            if original is None:
                continue
            role = ent.get("role") if ent.get("role") in VALID_ROLES else "unknown"
            merged.setdefault(original, {
                "original": original, "category": category, "role": role,
                "notes": ent.get("notes", ""), "source": "ollama-draft",
            })

    out = {"entities": sorted(merged.values(), key=lambda e: e["original"])}
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"识别 {len(out['entities'])} 个实体 → {args.output}", file=sys.stderr)
    if not args.yes:
        print("注意：这是草稿。请确认角色与替身后，再 legal-redactor redact --entities 使用。", file=sys.stderr)


if __name__ == "__main__":
    main()
