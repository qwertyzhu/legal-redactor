# 本机 Ollama 实体识别

工具全程在本机运行；替换由 CLI 确定性执行。可选的只是**自然语言实体交给谁识别**。

默认用在线 Agent。仅当用户说「用本机模型 / Ollama / 不出设备 / 不要联网」、或文书明显高敏时，才询问一次。

```bash
python3 scripts/ollama_setup.py          # 只读检测；--json 供 Agent
python3 scripts/ollama_entities.py INPUT.docx -o entities.ollama.draft.json
legal-redactor redact INPUT.docx --mode ai --entities entities.ollama.draft.json -o OUTPUT.docx
```

| 输入 | 链路 |
|---|---|
| DOCX / 文字层 PDF / txt / md | 文本模型识别 → CLI 替换 |
| 扫描件 → 给 AI | Tesseract `ocr` → md → 文本模型 → CLI |
| 扫描件 → 交法院涂黑 | `redact-scan`（Tesseract 词坐标）；视觉模型不参与 |

确认 Ollama 仅监听本机（`127.0.0.1:11434`）。ledger、草稿、OCR 中间产物全部留本地。用本机模型 ≠ 免人工；verify + 反推三问仍强制。
