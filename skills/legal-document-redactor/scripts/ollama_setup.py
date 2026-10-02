#!/usr/bin/env python3
"""ollama_setup.py — 本机 Ollama 识别路径的环境检测与模型推荐（只读，不改动系统）。

用法：
    python3 ollama_setup.py            # 检测并给出推荐
    python3 ollama_setup.py --json     # 机器可读输出（供 Agent 判断）

逻辑：
- 未装 Ollama → 打印安装命令 + 按设备内存推荐首个要 pull 的模型；
- 已装 → 列出已安装模型，标出实体识别首选；没有合适的就推荐 pull 哪个。

推荐表（Apple Silicon 统一内存；Intel Mac 或 <16GB 不建议用本机模型，召回太差）：
    ≥32GB → 12-14B 级（如 gemma4:12b / qwen3:14b）
    16-31GB → 7-8B 级（如 qwen3.5:latest / qwen3:8b）——实测实体识别够用
    <16GB  → 4B 级（如 qwen3:4b）勉强可用，必须人工兜底；更建议走在线路径
模型名随 Ollama 官方库更新，以 `ollama search` / ollama.com 为准。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request

# (优先级, 关键词)：已安装模型按此表挑实体识别首选，中文模型优先
PREFER = ["qwen3.5", "qwen3", "qwen2.5", "gemma4", "gemma3", "glm", "minicpm", "deepseek", "llama"]


def device_info() -> dict:
    mem_bytes = 0
    chip = "unknown"
    try:
        mem_bytes = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                       capture_output=True, text=True).stdout.strip())
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        pass
    return {"ram_gb": round(mem_bytes / 1024**3), "chip": chip}


def recommend_model(ram_gb: int) -> dict:
    if ram_gb >= 32:
        return {"tier": "12-14B", "model": "gemma4:12b", "note": "内存充裕，实体识别更稳"}
    if ram_gb >= 16:
        return {"tier": "7-8B", "model": "qwen3.5:latest", "note": "中文实体识别实测够用，性价比最高"}
    if ram_gb >= 8:
        return {"tier": "4B", "model": "qwen3:4b", "note": "勉强可用，召回偏低，verify 后必须人工兜底；更建议在线路径"}
    return {"tier": "不推荐", "model": None, "note": "内存不足，请走在线路径（Agent 识别）"}


def installed_models() -> list[str]:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            return [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except Exception:
        return []


def pick_installed(models: list[str]) -> str | None:
    for kw in PREFER:
        for m in models:
            if m.startswith(kw):
                return m
    return models[0] if models else None


def main() -> None:
    ap = argparse.ArgumentParser(description="本机 Ollama 识别环境检测与模型推荐（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dev = device_info()
    rec = recommend_model(dev["ram_gb"])
    has_ollama = shutil.which("ollama") is not None
    models = installed_models() if has_ollama else []
    best = pick_installed(models)

    report = {
        "ollama_installed": has_ollama,
        "ollama_running": bool(models),
        "chip": dev["chip"],
        "ram_gb": dev["ram_gb"],
        "installed_models": models,
        "installed_pick": best,
        "recommendation": rec,
        "ready": bool(best),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"设备：{dev['chip']}，内存 {dev['ram_gb']} GB")
    if not has_ollama:
        print("Ollama：未安装。安装：brew install --cask ollama && ollama serve")
        if rec["model"]:
            print(f"装好后拉取推荐模型：ollama pull {rec['model']}  # {rec['tier']} 级，{rec['note']}")
        else:
            print(f"注意：{rec['note']}")
        return
    if not models:
        print("Ollama：已安装但未运行或无模型。先 ollama serve。")
        if rec["model"]:
            print(f"推荐拉取：ollama pull {rec['model']}  # {rec['tier']} 级，{rec['note']}")
        return
    print(f"已安装模型：{', '.join(models)}")
    print(f"实体识别首选：{best}（用 --model {best}）")
    if rec["model"] and not any(m.startswith(rec["model"].split(":")[0]) for m in models):
        print(f"可选升级：ollama pull {rec['model']}  # 按你 {dev['ram_gb']}GB 内存推荐，{rec['note']}")
    print("就绪：可以用本机 Ollama 识别。")


if __name__ == "__main__":
    main()
