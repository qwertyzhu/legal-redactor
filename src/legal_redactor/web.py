"""Local-first Web UI for legal-redactor.

Runs on http://127.0.0.1:8765 with zero external dependencies.
All processing is done locally with no cloud or external network requests.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .image_scan import ocr_available, seal_detection_available
from .ledger_store import load_ledger, restore_text
from .ner import ner_status
from .pipeline import _parse_category_list, redact_file
from .profiles_loader import list_profiles, load_profile


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "document")
    clean = re.sub(r'[^\w\u4e00-\u9fff\.\-\(\)（）\s]', '_', base)
    return clean.strip() or "document"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def parse_multipart(content_type: str, body_bytes: bytes) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return {}, {}
    boundary = m.group(1).strip("\"'").encode("utf-8")
    delimiter = b"--" + boundary
    parts = body_bytes.split(delimiter)
    form: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}

    for part in parts:
        if not part or part.strip() == b"--":
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        if b"\r\n\r\n" in part:
            header_part, content = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_part, content = part.split(b"\n\n", 1)
        else:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        elif content.endswith(b"\n"):
            content = content[:-1]

        header_text = header_part.decode("utf-8", errors="replace")
        cd_match = re.search(
            r'Content-Disposition:\s*form-data;\s*name="([^"]+)"(?:;\s*filename="([^"]+)")?',
            header_text,
            re.I,
        )
        if not cd_match:
            continue
        field_name = cd_match.group(1)
        filename = cd_match.group(2)
        if filename:
            ct_match = re.search(r"Content-Type:\s*([^\r\n;]+)", header_text, re.I)
            ct = ct_match.group(1).strip() if ct_match else "application/octet-stream"
            files[field_name] = {
                "filename": filename,
                "content": content,
                "content_type": ct,
            }
        else:
            form[field_name] = content.decode("utf-8", errors="replace")
    return form, files


WEB_TASKS_DIR = Path(tempfile.gettempdir()) / "legal_redactor_web_tasks"
WEB_TASKS_DIR.mkdir(parents=True, exist_ok=True)


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = f"legal-redactor/{__version__}"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep console output clean
        pass

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, download_name: str, content_type: str | None = None) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        if not content_type:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        encoded_name = urllib.parse.quote(download_name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(file_bytes)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{encoded_name}\"; filename*=UTF-8''{encoded_name}",
        )
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(file_bytes)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("", "/", "/index.html"):
            body = HTML_CONTENT.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/doctor":
            info = {
                "version": __version__,
                "tesseract": ocr_available(),
                "opencv": seal_detection_available(),
                "ner": ner_status(),
                "profiles": [load_profile(n).to_dict() for n in list_profiles()],
            }
            self._send_json(info)
            return

        if path.startswith("/api/download/"):
            parts = [p for p in path.split("/") if p]
            # /api/download/<task_id>/<kind>
            if len(parts) >= 4:
                task_id = parts[2]
                kind = parts[3]
                if not re.match(r"^[a-f0-9]{12}$", task_id):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid task ID")
                    return
                task_dir = WEB_TASKS_DIR / task_id
                meta_file = task_dir / "meta.json"
                if not meta_file.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND, "Task expired or not found")
                    return
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Invalid task meta")
                    return

                if kind == "file":
                    out_path = task_dir / meta.get("output_filename", "output")
                    dl_name = meta.get("download_filename", out_path.name)
                    self._send_file(out_path, dl_name)
                    return
                elif kind == "audit":
                    audit_path = task_dir / meta.get("audit_filename", "audit.md")
                    dl_name = meta.get("audit_download_name", "audit.md")
                    self._send_file(audit_path, dl_name, content_type="text/markdown; charset=utf-8")
                    return
                elif kind == "ledger":
                    ledger_path = Path(meta.get("ledger_path", ""))
                    if not ledger_path.is_file():
                        ledger_path = task_dir / meta.get("ledger_filename", "ledger.json")
                    dl_name = meta.get("ledger_download_name", "ledger.json")
                    self._send_file(ledger_path, dl_name, content_type="application/json; charset=utf-8")
                    return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        if content_length > 150 * 1024 * 1024:  # 150 MB safety limit
            self._send_json({"ok": False, "error": "文件过大，单次处理上限 150MB"}, status=413)
            return

        body_bytes = self.rfile.read(content_length)

        if path == "/api/inspect":
            form, files = parse_multipart(content_type, body_bytes)
            file_item = files.get("file")
            if not file_item:
                self._send_json({"ok": False, "error": "未接收到上传文件"}, status=400)
                return

            filename = _safe_filename(file_item["filename"])
            suffix = Path(filename).suffix.lower()
            content = file_item["content"]
            size = len(content)

            is_pdf = suffix == ".pdf"
            is_docx = suffix == ".docx"
            page_count = 0
            has_text_layer = True
            preview = ""

            try:
                if is_pdf:
                    import pymupdf as fitz
                    from .ocr_engine import pdf_has_text_layer

                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = Path(tmp.name)
                    try:
                        doc = fitz.open(str(tmp_path))
                        page_count = len(doc)
                        has_text_layer = pdf_has_text_layer(tmp_path, min_chars=1)
                        # Extract first 500 chars preview
                        parts: list[str] = []
                        for page in doc:
                            t = page.get_text("text").strip()
                            if t:
                                parts.append(t)
                            if sum(len(p) for p in parts) >= 400:
                                break
                        preview = "\n".join(parts)[:400]
                        doc.close()
                    finally:
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                elif is_docx:
                    from .formats import docx_io

                    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = Path(tmp.name)
                    try:
                        text = docx_io.extract_text(tmp_path)
                        preview = text.strip()[:400]
                        page_count = 1  # docx doesn't expose fixed page count without Word engine
                    finally:
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                else:
                    text = content.decode("utf-8", errors="replace")
                    preview = text.strip()[:400]
            except Exception as exc:
                self._send_json({"ok": False, "error": f"文档预检解析失败: {exc}"}, status=400)
                return

            self._send_json({
                "ok": True,
                "filename": filename,
                "suffix": suffix,
                "size": size,
                "size_formatted": _format_size(size),
                "is_pdf": is_pdf,
                "is_docx": is_docx,
                "page_count": page_count,
                "has_text_layer": has_text_layer,
                "preview": preview,
            })
            return

        if path == "/api/redact-file":
            form, files = parse_multipart(content_type, body_bytes)
            file_item = files.get("file")
            if not file_item:
                self._send_json({"ok": False, "error": "未接收到上传文件"}, status=400)
                return

            raw_name = file_item["filename"]
            safe_name = _safe_filename(raw_name)
            content = file_item["content"]

            mode = form.get("mode", "ai")
            profile = form.get("profile") or None
            auto_confident = form.get("auto_confident", "true").lower() == "true"
            is_scanned_mode = form.get("is_scanned_mode", "false").lower() == "true"
            redact_court_staff = (
                True if form.get("redact_court_staff") == "true"
                else (False if form.get("redact_court_staff") == "false" else None)
            )
            ocr_images = form.get("ocr_images", "auto")

            keep_categories_raw = form.get("keep_categories", "")
            extra_categories_raw = form.get("extra_categories", "")
            preserve_raw = form.get("preserve", "")
            entities_raw = form.get("entities", "")
            allowlist_raw = form.get("allowlist", "")

            keep_cats = [c.strip() for c in keep_categories_raw.split(",") if c.strip()]
            extra_cats = [c.strip() for c in extra_categories_raw.split(",") if c.strip()]
            preserve_list = [p.strip() for p in preserve_raw.splitlines() if p.strip()]

            task_id = uuid.uuid4().hex[:12]
            task_dir = WEB_TASKS_DIR / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            in_path = task_dir / safe_name
            in_path.write_bytes(content)

            entities_path: Path | None = None
            if entities_raw.strip():
                try:
                    parsed_entities = json.loads(entities_raw)
                    entities_path = task_dir / "entities.json"
                    entities_path.write_text(json.dumps(parsed_entities, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"自定义实体 JSON 格式有误: {exc}"}, status=400)
                    return

            allowlist_files: list[Path] | None = None
            if allowlist_raw.strip():
                allow_file = task_dir / "allow.txt"
                allow_file.write_text(allowlist_raw, encoding="utf-8")
                allowlist_files = [allow_file]

            suffix = in_path.suffix.lower()
            stem = in_path.stem
            out_name = f"{stem}.redacted{suffix}"
            out_path = task_dir / out_name

            # Special case: scanned PDF visual cover-up (redact-scan)
            if is_scanned_mode and suffix == ".pdf":
                from .scan_pdf import redact_scanned_pdf

                try:
                    scan_res = redact_scanned_pdf(
                        input_path=in_path,
                        output_path=out_path,
                        mode=mode,
                        keep_categories=_parse_category_list(keep_cats),
                        extra_categories=_parse_category_list(extra_cats),
                        work_dir=task_dir,
                    )
                    meta_info = {
                        "task_id": task_id,
                        "output_filename": out_name,
                        "download_filename": out_name,
                    }
                    (task_dir / "meta.json").write_text(json.dumps(meta_info, ensure_ascii=False), encoding="utf-8")

                    self._send_json({
                        "ok": True,
                        "task_id": task_id,
                        "status": "PASS",
                        "is_scan": True,
                        "hits_count": len(scan_res.hits),
                        "out_filename": out_name,
                        "download_url": f"/api/download/{task_id}/file",
                        "warnings": [
                            "这是基于 OCR 坐标的扫描件视觉涂黑产物。",
                            "⚠️ 法律文书交法院或对方前，请务必人工逐页翻页复核涂黑区域！",
                        ],
                    })
                    return
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"扫描件涂黑失败: {exc}"}, status=500)
                    return

            # Standard redaction pipeline
            try:
                res = redact_file(
                    input_path=in_path,
                    mode=mode,
                    output_path=out_path,
                    entities_path=entities_path,
                    preserve=preserve_list,
                    work_dir=task_dir,
                    keep_categories=keep_cats,
                    extra_categories=extra_cats,
                    auto_confident=auto_confident,
                    allowlist_files=allowlist_files,
                    profile=profile,
                    redact_court_staff=redact_court_staff,
                    ocr_images=ocr_images,
                )

                audit_text = ""
                audit_filename = f"{stem}.audit.md"
                if res.audit_md_path and res.audit_md_path.is_file():
                    audit_filename = res.audit_md_path.name
                    audit_text = res.audit_md_path.read_text(encoding="utf-8", errors="replace")

                ledger_filename = f"{stem}.ledger.json"
                if res.ledger_path and res.ledger_path.is_file():
                    ledger_filename = res.ledger_path.name

                meta_info = {
                    "task_id": task_id,
                    "output_filename": out_name,
                    "download_filename": out_name,
                    "audit_filename": audit_filename,
                    "audit_download_name": f"{stem}.audit.md",
                    "ledger_path": str(res.ledger_path) if res.ledger_path else "",
                    "ledger_filename": ledger_filename,
                    "ledger_download_name": f"{stem}.ledger.json",
                }
                (task_dir / "meta.json").write_text(json.dumps(meta_info, ensure_ascii=False), encoding="utf-8")

                replacements = [
                    {
                        "original": e.original,
                        "replacement": e.replacement,
                        "category": e.category,
                        "role": e.role,
                        "source": e.source,
                    }
                    for e in res.plan.entities
                ]
                suspects = [
                    {
                        "text": s.text,
                        "category": s.category,
                        "reason": s.reason,
                    }
                    for s in res.suspects
                ]

                self._send_json({
                    "ok": res.ok,
                    "task_id": task_id,
                    "status": res.status,
                    "is_scan": False,
                    "out_filename": out_name,
                    "replacements_count": len(replacements),
                    "replacements": replacements,
                    "suspects": suspects,
                    "warnings": res.warnings,
                    "audit_text": audit_text,
                    "download_file_url": f"/api/download/{task_id}/file",
                    "download_audit_url": f"/api/download/{task_id}/audit",
                    "download_ledger_url": f"/api/download/{task_id}/ledger",
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": f"脱敏处理失败: {exc}"}, status=500)
            return

        if path == "/api/redact-text":
            try:
                data = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, status=400)
                return

            text = data.get("text", "")
            if not text.strip():
                self._send_json({"ok": False, "error": "脱敏文本内容不能为空"}, status=400)
                return

            mode = data.get("mode", "ai")
            profile = data.get("profile") or None
            auto_confident = bool(data.get("auto_confident", True))
            keep_cats = data.get("keep_categories", [])
            extra_cats = data.get("extra_categories", [])
            preserve = data.get("preserve", [])

            with tempfile.TemporaryDirectory(prefix="legal-redactor-web-text-") as tmp:
                tmp_dir = Path(tmp)
                in_file = tmp_dir / "input.txt"
                out_file = tmp_dir / "output.txt"
                in_file.write_text(text, encoding="utf-8")

                try:
                    res = redact_file(
                        input_path=in_file,
                        mode=mode,
                        output_path=out_file,
                        work_dir=tmp_dir,
                        keep_categories=keep_cats,
                        extra_categories=extra_cats,
                        auto_confident=auto_confident,
                        preserve=preserve,
                        profile=profile,
                    )
                    redacted_text = out_file.read_text(encoding="utf-8")
                    ledger_data = {}
                    if res.ledger_path and res.ledger_path.is_file():
                        ledger_data = json.loads(res.ledger_path.read_text(encoding="utf-8"))

                    replacements = [
                        {
                            "original": e.original,
                            "replacement": e.replacement,
                            "category": e.category,
                            "role": e.role,
                        }
                        for e in res.plan.entities
                    ]
                    suspects = [
                        {"text": s.text, "category": s.category, "reason": s.reason}
                        for s in res.suspects
                    ]

                    self._send_json({
                        "ok": res.ok,
                        "status": res.status,
                        "redacted_text": redacted_text,
                        "replacements": replacements,
                        "suspects": suspects,
                        "warnings": res.warnings,
                        "ledger": ledger_data,
                    })
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"文本脱敏失败: {exc}"}, status=500)
            return

        if path == "/api/restore-text":
            try:
                data = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, status=400)
                return

            text = data.get("text", "")
            ledger = data.get("ledger")
            if not isinstance(ledger, dict):
                self._send_json({"ok": False, "error": "请提供有效的 ledger 账本对象"}, status=400)
                return

            try:
                restored, warns = restore_text(text, ledger)
                self._send_json({
                    "ok": True,
                    "restored_text": restored,
                    "warnings": warns,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": f"还原失败: {exc}"}, status=500)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def _find_available_port(start_port: int, max_attempts: int = 10) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def run_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    actual_port = _find_available_port(port)
    server_address = (host, actual_port)

    try:
        httpd = ThreadingHTTPServer(server_address, WebRequestHandler)
    except OSError as exc:
        print(f"ERROR: 无法绑定本地地址 {host}:{actual_port} ({exc})", file=sys.stderr)
        return 1

    url = f"http://{host}:{actual_port}"
    print("=" * 66)
    print("  中国法律文书本地脱敏助手 · 网页版服务已就绪")
    print("  Local-first Chinese Legal Document Redaction Web UI")
    print("=" * 66)
    print(f"  > 本地访问地址: {url}")
    print(f"  > 运行模式:     100% 本地运算 (127.0.0.1) · 零网络上传")
    print("  > 支持格式:     PDF (矢量文字层 / 扫描件)、Word (.docx)、文本")
    print("  按 Ctrl+C 停止服务")
    print("=" * 66)

    if open_browser:
        def _open():
            time.sleep(0.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止本地 Web 服务...")
    finally:
        httpd.server_close()
        print("本地 Web 服务已关闭。")
    return 0


HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>中国法律文书本地脱敏助手 · By lawyer朱思浩</title>
<style>
:root {
  --primary: #1e3a8a;
  --primary-hover: #172554;
  --primary-light: #eff6ff;
  --accent: #2563eb;
  --success: #16a34a;
  --success-bg: #f0fdf4;
  --warning: #d97706;
  --warning-bg: #fffbeb;
  --danger: #dc2626;
  --danger-bg: #fef2f2;
  --text-main: #0f172a;
  --text-muted: #64748b;
  --bg-main: #f8fafc;
  --card-bg: #ffffff;
  --border: #e2e8f0;
  --radius: 10px;
  --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.08), 0 2px 4px -2px rgb(0 0 0 / 0.06);
}

@media (prefers-color-scheme: dark) {
  :root {
    --primary: #3b82f6;
    --primary-hover: #60a5fa;
    --primary-light: #1e293b;
    --accent: #60a5fa;
    --text-main: #f1f5f9;
    --text-muted: #94a3b8;
    --bg-main: #090d16;
    --card-bg: #111827;
    --border: #1f2937;
    --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.4);
  }
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background-color: var(--bg-main);
  color: var(--text-main);
  line-height: 1.5;
  padding: 24px;
}

.container {
  max-width: 1080px;
  margin: 0 auto;
}

header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-badge {
  background: var(--primary);
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  padding: 6px 12px;
  border-radius: 8px;
  letter-spacing: 0.5px;
}

.title h1 {
  font-size: 20px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.author-pill {
  font-size: 11.5px;
  font-weight: 600;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  padding: 2px 10px;
  border-radius: 9999px;
  letter-spacing: 0.2px;
}

.title p {
  font-size: 13px;
  color: var(--text-muted);
}

.security-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--success-bg);
  color: var(--success);
  border: 1px solid #bbf7d0;
  padding: 6px 14px;
  border-radius: 9999px;
  font-size: 13px;
  font-weight: 600;
}

.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
}

.tab-btn {
  background: none;
  border: none;
  padding: 10px 18px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s;
}

.tab-btn:hover {
  color: var(--text-main);
  background: var(--primary-light);
}

.tab-btn.active {
  color: #fff;
  background: var(--primary);
}

.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: var(--shadow);
  margin-bottom: 24px;
}

.dropzone {
  border: 2px dashed var(--accent);
  border-radius: var(--radius);
  background: var(--primary-light);
  padding: 36px 20px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.dropzone.dragover {
  border-color: var(--primary);
  background: #dbeafe;
}

.dropzone-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

.dropzone-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 6px;
}

.dropzone-sub {
  font-size: 13px;
  color: var(--text-muted);
}

.file-info-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-main);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 16px;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}

.badge {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.badge-pdf { background: #fee2e2; color: #dc2626; }
.badge-docx { background: #dbeafe; color: #2563eb; }
.badge-txt { background: #f3f4f6; color: #4b5563; }
.badge-success { background: var(--success-bg); color: var(--success); }
.badge-warning { background: var(--warning-bg); color: var(--warning); }
.badge-danger { background: var(--danger-bg); color: var(--danger); }

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}

.form-group {
  margin-top: 16px;
}

.form-group label {
  display: block;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 6px;
}

.form-control, select, textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card-bg);
  color: var(--text-main);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.form-control:focus, select:focus, textarea:focus {
  border-color: var(--accent);
}

.checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 12px;
}

.checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: pointer;
}

.collapsible-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
  cursor: pointer;
  padding: 10px 0;
  margin-top: 12px;
  border-top: 1px dashed var(--border);
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 11px 22px;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
  text-decoration: none;
}

.btn-primary {
  background: var(--primary);
  color: #fff;
}
.btn-primary:hover {
  background: var(--primary-hover);
}

.btn-secondary {
  background: var(--bg-main);
  border: 1px solid var(--border);
  color: var(--text-main);
}
.btn-secondary:hover {
  background: var(--border);
}

.btn-success {
  background: var(--success);
  color: #fff;
}

.btn-block {
  width: 100%;
  margin-top: 20px;
}

.alert {
  padding: 14px 16px;
  border-radius: 8px;
  font-size: 14px;
  margin-top: 16px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.alert-warning { background: var(--warning-bg); color: #92400e; border: 1px solid #fde68a; }
.alert-danger { background: var(--danger-bg); color: #991b1b; border: 1px solid #fecaca; }
.alert-success { background: var(--success-bg); color: #166534; border: 1px solid #bbf7d0; }

.table-wrap {
  overflow-x: auto;
  margin-top: 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

th, td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

th {
  background: var(--bg-main);
  font-weight: 600;
  color: var(--text-muted);
}

.diff-box {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.diff-pane {
  display: flex;
  flex-direction: column;
}

.diff-pane h4 {
  font-size: 14px;
  margin-bottom: 8px;
  color: var(--text-muted);
}

.diff-pane textarea {
  height: 380px;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
}

.spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-radius: 50%;
  border-top-color: #fff;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Footer */
footer {
  margin-top: 48px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.footer-author {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-main);
}

.footer-author a {
  color: var(--accent);
  text-decoration: none;
}
.footer-author a:hover {
  text-decoration: underline;
}

.hidden { display: none !important; }
</style>
</head>
<body>

<div class="container">
  <header>
    <div class="brand">
      <div class="logo-badge">LEGAL-REDACTOR</div>
      <div class="title">
        <h1>
          中国法律文书本地脱敏助手
          <span class="author-pill">Designed by lawyer朱思浩</span>
        </h1>
        <p>PDF (文字层 / 扫描件) · Word (.docx) · 文本 · 100% 离线脱敏与反查防护</p>
      </div>
    </div>
    <div class="security-badge">
      🔒 100% 本地运算 (127.0.0.1) · 案卷数据绝不上传
    </div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('file-tab')">📄 文档脱敏 (PDF & Word)</button>
    <button class="tab-btn" onclick="switchTab('text-tab')">📝 纯文本即时洗稿</button>
    <button class="tab-btn" onclick="switchTab('restore-tab')">🔄 AI 回复当事人还原</button>
    <button class="tab-btn" onclick="switchTab('doctor-tab')">🩺 本机组件体检</button>
  </div>

  <!-- TAB 1: FILE REDACT -->
  <div id="file-tab" class="tab-content">
    <div class="card">
      <div class="dropzone" id="fileDropzone" onclick="document.getElementById('fileInput').click()">
        <div class="dropzone-icon">📑</div>
        <div class="dropzone-title">拖拽 PDF 或 Word 文档到此处，或点击浏览选择</div>
        <div class="dropzone-sub">支持 .pdf（矢量文字层或扫描件）、.docx、.txt、.md 格式，单文件上限 150MB</div>
      </div>
      <input type="file" id="fileInput" class="hidden" accept=".pdf,.docx,.txt,.md" onchange="handleFileSelect(this.files[0])">

      <div id="fileInfoBar" class="file-info-bar hidden">
        <div class="file-meta">
          <span id="fileBadge" class="badge badge-pdf">PDF</span>
          <strong id="fileName">文档.pdf</strong>
          <span id="fileSize" style="color:var(--text-muted);">(0 KB)</span>
          <span id="fileTextLayerBadge" class="badge badge-success hidden">✅ 矢量文字层已就绪</span>
          <span id="fileScanBadge" class="badge badge-warning hidden">⚠️ 扫描件图片 (无可提取文字层)</span>
        </div>
        <button class="btn btn-secondary" style="padding:4px 10px;font-size:12px;" onclick="resetFile()">重新选择</button>
      </div>

      <div id="scannedAlert" class="alert alert-warning hidden">
        <div>
          <strong>检测到该 PDF 为扫描件图片（无可提取矢量文字层）：</strong><br>
          已为你默认开启【扫描件视觉涂黑】模式：系统将调用本机 Tesseract OCR 并在图片层将身份证、手机、银行卡、税号等坐标直接涂黑保全。
          <div style="margin-top:6px;font-size:12px;color:#b45309;">
            * 注：视觉涂黑为物理坐标图层遮盖，交付法院或对方前<strong>必须人工逐页翻页复核</strong>。
          </div>
        </div>
      </div>

      <div class="grid-2">
        <div class="form-group">
          <label>脱敏目标与等级 (Mode / Profile)</label>
          <select id="fileModeSelect" onchange="handleModeChange()">
            <option value="ai" selected>🤖 ai 模式：给在线大模型 (激进去标识，当事人/案号/联系方式全面替身化)</option>
            <option value="production">⚖️ production 模式：交法院 / 对方 (保留当事人和案号，去证件/手机/邮箱/账号)</option>
            <option value="publish">📢 publish 模式：对外案例发布 / 宣传 (全体张某，住址到县区，金额模糊)</option>
            <option value="judgment-online">🏛️ judgment-online 模式：裁判文书上网 (法释〔2016〕19号标准)</option>
            <option value="minimal">🔒 minimal 模式：极简结构脱敏 (仅去身份证/手机/邮箱/银行账号)</option>
          </select>
        </div>
        <div class="form-group">
          <label>高敏保留微调</label>
          <div class="checkbox-group" style="padding-top:6px;">
            <label class="checkbox-label">
              <input type="checkbox" id="fileAutoConfident" checked>
              自动识别高置信人名/单位 (--auto-confident)
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="fileKeepUscc">
              保留统一社会信用代码 (立案常用)
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="fileRedactCourtStaff">
              隐去审判人员姓名 (ai 默认已隐)
            </label>
          </div>
        </div>
      </div>

      <div class="collapsible-header" onclick="toggleAdvanced('fileAdvanced')">
        <span>⚙️ 自定义实体补录与白名单 (展开高级选项)</span>
        <span id="fileAdvancedArrow">▼</span>
      </div>

      <div id="fileAdvanced" class="hidden" style="margin-top:12px;">
        <div class="grid-2">
          <div class="form-group">
            <label>自定义实体补录 (JSON 格式，可选)</label>
            <textarea id="fileEntitiesJson" rows="4" placeholder='{"entities": [{"original": "张三", "replacement": "某甲", "category": "person"}]}'></textarea>
            <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">未被自动识别的特定当事人、品牌、简称可在此补齐。</div>
          </div>
          <div class="form-group">
            <label>原样保留白名单 (每行一个词，可选)</label>
            <textarea id="fileAllowlist" rows="4" placeholder="浙江省高级人民法院\n最高人民法院\n著作权法"></textarea>
            <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">白名单中的词项在任何模式下均原样保留不被脱敏。</div>
          </div>
        </div>
      </div>

      <button id="fileStartBtn" class="btn btn-primary btn-block" onclick="startFileRedact()" disabled>
        <span>立即开始脱敏处理</span>
      </button>

      <!-- RESULTS PANEL -->
      <div id="fileResultPanel" class="hidden" style="margin-top:24px;border-top:1px solid var(--border);padding-top:20px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span id="resultStatusBadge" class="badge badge-success">PASS</span>
            <strong id="resultSummaryText" style="font-size:16px;">脱敏完成</strong>
          </div>
          <div style="display:flex;gap:8px;">
            <a id="btnDownloadFile" class="btn btn-primary" href="#" download>📥 下载脱敏后文档</a>
            <a id="btnDownloadAudit" class="btn btn-secondary" href="#" download>📄 下载审计报告 (.md)</a>
            <a id="btnDownloadLedger" class="btn btn-secondary" href="#" download>🔐 导出还原账本 (.json)</a>
          </div>
        </div>

        <div id="fileResultAlerts"></div>

        <div id="replacementsSection" style="margin-top:16px;">
          <h4 style="font-size:14px;margin-bottom:8px;">🎯 实体替换明细 (<span id="replacementsCount">0</span> 处)</h4>
          <div class="table-wrap">
            <table id="replacementsTable">
              <thead>
                <tr>
                  <th>原文</th>
                  <th>替身 / 占位符</th>
                  <th>类别</th>
                  <th>角色/来源</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>

        <div id="suspectsSection" class="hidden" style="margin-top:16px;">
          <h4 style="font-size:14px;margin-bottom:8px;color:var(--warning);">⚠️ 疑似自然语言残留提示 (<span id="suspectsCount">0</span> 处需核对)</h4>
          <div class="table-wrap">
            <table id="suspectsTable">
              <thead>
                <tr>
                  <th>疑似词</th>
                  <th>类别</th>
                  <th>判定原因</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- TAB 2: TEXT REDACT -->
  <div id="text-tab" class="tab-content hidden">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <div style="display:flex;align-items:center;gap:12px;">
          <label style="font-weight:600;font-size:14px;">脱敏模式：</label>
          <select id="textModeSelect" style="width:auto;">
            <option value="ai" selected>🤖 ai 模式 (全面替身化，适合发给大模型)</option>
            <option value="production">⚖️ production 模式 (保留当事人/案号)</option>
            <option value="publish">📢 publish 模式 (公开改写/张某)</option>
          </select>
          <label class="checkbox-label">
            <input type="checkbox" id="textAutoConfident" checked> 自动识别高置信人名/单位
          </label>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-primary" onclick="startTextRedact()">⚡ 立即脱敏文本</button>
          <button class="btn btn-secondary" onclick="copyRedactedText()">📋 复制脱敏结果</button>
        </div>
      </div>

      <div class="diff-box">
        <div class="diff-pane">
          <h4>原始法律文书文本 (粘贴在此)</h4>
          <textarea id="textSourceInput" placeholder="在此粘贴起诉状、答辩状、合同协议或判决书片段...
例如：
原告李四，男，1985年3月12日出生，汉族，住杭州市西湖区文三路100号，身份证号330106198503121234，电话13900001111。
被告杭州某某网络科技有限公司，统一社会信用代码91330100MA2BXXXXXX，住所地杭州市余杭区文一西路999号。"></textarea>
        </div>
        <div class="diff-pane">
          <h4>脱敏后替身文本 (可直接发给 AI)</h4>
          <textarea id="textOutput" readonly placeholder="点击上方「立即脱敏文本」后在此输出结果..."></textarea>
        </div>
      </div>

      <div id="textResultBar" class="hidden" style="margin-top:16px;display:flex;justify-content:space-between;align-items:center;background:var(--bg-main);padding:10px 16px;border-radius:8px;">
        <div>
          状态: <span id="textStatusBadge" class="badge badge-success">PASS</span>
          <span id="textHitsCount" style="margin-left:12px;font-size:13px;color:var(--text-muted);">替换 0 处</span>
        </div>
        <div>
          <button class="btn btn-secondary" style="font-size:13px;padding:6px 12px;" onclick="exportCurrentLedger()">🔐 导出本次 Ledger 账本</button>
        </div>
      </div>
    </div>
  </div>

  <!-- TAB 3: RESTORE -->
  <div id="restore-tab" class="tab-content hidden">
    <div class="card">
      <h3 style="font-size:16px;margin-bottom:8px;">大模型回复反向还原</h3>
      <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">
        在线 AI（ChatGPT / Claude / DeepSeek 等）对脱敏文书进行研判回复后，将带有「某甲」「某乙」等替身的 AI 回复粘贴在下方，配合本地账本 (Ledger)，一键恢复为真实当事人姓名。
      </p>

      <div class="form-group">
        <label>1. 粘贴本地 Ledger 账本 JSON (或从上方脱敏任务自动继承)</label>
        <textarea id="restoreLedgerInput" rows="4" placeholder='{"entities": [{"original": "张三", "replacement": "某甲"}]}'></textarea>
      </div>

      <div class="diff-box" style="margin-top:16px;">
        <div class="diff-pane">
          <h4>2. 粘贴 AI 生成的答复文本 (含替身)</h4>
          <textarea id="restoreSourceInput" placeholder="例如：根据案件材料，原告某甲与被告某单位A之间的买卖合同合法有效..."></textarea>
        </div>
        <div class="diff-pane">
          <h4>3. 还原后真实文本</h4>
          <textarea id="restoreOutput" readonly placeholder="点击下方「一键还原真实当事人」后在此输出..."></textarea>
        </div>
      </div>

      <div style="margin-top:16px;display:flex;gap:10px;">
        <button class="btn btn-primary" onclick="startRestore()">🔄 一键还原真实当事人名称</button>
        <button class="btn btn-secondary" onclick="copyRestoredText()">📋 复制还原文本</button>
      </div>

      <div id="restoreAlerts" style="margin-top:12px;"></div>
    </div>
  </div>

  <!-- TAB 4: DOCTOR -->
  <div id="doctor-tab" class="tab-content hidden">
    <div class="card">
      <h3 style="font-size:16px;margin-bottom:8px;">本地脱敏组件与环境体检</h3>
      <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">
        所有检测完全在本地完成，确保脱敏能力与保密基线已就绪。
      </p>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>组件项目</th>
              <th>当前状态</th>
              <th>说明与功能影响</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>PyMuPDF (fitz)</strong></td>
              <td><span class="badge badge-success">✅ 已就绪</span></td>
              <td>PDF 矢量文字层脱敏、元数据清除、书签清理</td>
            </tr>
            <tr>
              <td><strong>python-docx</strong></td>
              <td><span class="badge badge-success">✅ 已就绪</span></td>
              <td>Word (.docx) run 级原位精确替换、修订与批注处理</td>
            </tr>
            <tr>
              <td><strong>Tesseract OCR (chi_sim)</strong></td>
              <td id="docTesseract"><span class="spinner"></span></td>
              <td>纯扫描件 PDF / 内嵌图片敏感文字识别与视觉涂黑</td>
            </tr>
            <tr>
              <td><strong>OpenCV (Vision)</strong></td>
              <td id="docOpenCV"><span class="spinner"></span></td>
              <td>印章与公章红色检测草稿 (seal-draft)</td>
            </tr>
            <tr>
              <td><strong>Chinese NER (LTP)</strong></td>
              <td id="docNER"><span class="spinner"></span></td>
              <td>自然语言无锚点人名辅助检测 (可选)</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Footer with lawyer signature -->
  <footer>
    <div class="footer-author">
      <span>中国法律文书本地脱敏助手</span>
      <span>·</span>
      <span>架构与设计：<a href="https://github.com/qwertyzhu/legal-redactor" target="_blank">lawyer朱思浩</a></span>
    </div>
    <div>
      <span>100% Local-First（数据绝不离机）</span>
      <span>·</span>
      <span>Apache-2.0 License</span>
    </div>
  </footer>

</div>

<script>
let currentFile = null;
let currentLedgerData = null;

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
  
  const targetBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick').includes(tabId));
  if (targetBtn) targetBtn.classList.add('active');
  
  const target = document.getElementById(tabId);
  if (target) target.classList.remove('hidden');

  if (tabId === 'doctor-tab') {
    fetchDoctorInfo();
  }
}

// Drag & drop
const dropzone = document.getElementById('fileDropzone');
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    handleFileSelect(e.dataTransfer.files[0]);
  }
});

function toggleAdvanced(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById(id + 'Arrow');
  if (el.classList.contains('hidden')) {
    el.classList.remove('hidden');
    if (arrow) arrow.textContent = '▲';
  } else {
    el.classList.add('hidden');
    if (arrow) arrow.textContent = '▼';
  }
}

function handleModeChange() {
  const mode = document.getElementById('fileModeSelect').value;
  const redactStaff = document.getElementById('fileRedactCourtStaff');
  if (mode === 'ai') {
    redactStaff.checked = true;
  } else {
    redactStaff.checked = false;
  }
}

async function handleFileSelect(file) {
  if (!file) return;
  currentFile = file;
  
  document.getElementById('fileName').textContent = file.name;
  document.getElementById('fileSize').textContent = `(${formatSize(file.size)})`;
  
  const ext = file.name.split('.').pop().toLowerCase();
  const badge = document.getElementById('fileBadge');
  badge.className = 'badge';
  if (ext === 'pdf') {
    badge.classList.add('badge-pdf');
    badge.textContent = 'PDF';
  } else if (ext === 'docx') {
    badge.classList.add('badge-docx');
    badge.textContent = 'DOCX';
  } else {
    badge.classList.add('badge-txt');
    badge.textContent = ext.toUpperCase();
  }

  document.getElementById('fileInfoBar').classList.remove('hidden');
  document.getElementById('fileDropzone').classList.add('hidden');
  document.getElementById('fileStartBtn').disabled = true;
  document.getElementById('fileStartBtn').innerHTML = '<span class="spinner"></span> 正在预检文档...';

  // Perform inspect
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/api/inspect', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.ok) {
      if (data.is_pdf) {
        if (data.has_text_layer) {
          document.getElementById('fileTextLayerBadge').classList.remove('hidden');
          document.getElementById('fileScanBadge').classList.add('hidden');
          document.getElementById('scannedAlert').classList.add('hidden');
        } else {
          document.getElementById('fileTextLayerBadge').classList.add('hidden');
          document.getElementById('fileScanBadge').classList.remove('hidden');
          document.getElementById('scannedAlert').classList.remove('hidden');
        }
      } else {
        document.getElementById('fileTextLayerBadge').classList.add('hidden');
        document.getElementById('fileScanBadge').classList.add('hidden');
        document.getElementById('scannedAlert').classList.add('hidden');
      }
      document.getElementById('fileStartBtn').disabled = false;
      document.getElementById('fileStartBtn').innerHTML = '<span>立即开始脱敏处理</span>';
    } else {
      alert('预检失败: ' + (data.error || '未知错误'));
      resetFile();
    }
  } catch (err) {
    alert('网络或服务异常: ' + err);
    resetFile();
  }
}

function resetFile() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('fileInfoBar').classList.add('hidden');
  document.getElementById('fileDropzone').classList.remove('hidden');
  document.getElementById('scannedAlert').classList.add('hidden');
  document.getElementById('fileResultPanel').classList.add('hidden');
  document.getElementById('fileStartBtn').disabled = true;
  document.getElementById('fileStartBtn').innerHTML = '<span>立即开始脱敏处理</span>';
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function startFileRedact() {
  if (!currentFile) return;
  const startBtn = document.getElementById('fileStartBtn');
  startBtn.disabled = true;
  startBtn.innerHTML = '<span class="spinner"></span> 正在执行本地高保真脱敏...';

  const isScanned = !document.getElementById('fileScanBadge').classList.contains('hidden');
  const formData = new FormData();
  formData.append('file', currentFile);
  formData.append('mode', document.getElementById('fileModeSelect').value);
  formData.append('auto_confident', document.getElementById('fileAutoConfident').checked ? 'true' : 'false');
  formData.append('is_scanned_mode', isScanned ? 'true' : 'false');

  const keepCats = [];
  if (document.getElementById('fileKeepUscc').checked) keepCats.push('uscc');
  formData.append('keep_categories', keepCats.join(','));

  const redactStaff = document.getElementById('fileRedactCourtStaff').checked;
  formData.append('redact_court_staff', redactStaff ? 'true' : 'false');

  const entities = document.getElementById('fileEntitiesJson').value.trim();
  if (entities) formData.append('entities', entities);

  const allowlist = document.getElementById('fileAllowlist').value.trim();
  if (allowlist) formData.append('allowlist', allowlist);

  try {
    const res = await fetch('/api/redact-file', { method: 'POST', body: formData });
    const data = await res.json();
    startBtn.disabled = false;
    startBtn.innerHTML = '<span>立即开始脱敏处理</span>';

    if (!data.ok && !data.out_filename) {
      alert('处理失败: ' + (data.error || '未知错误'));
      return;
    }

    renderFileResult(data);
  } catch (err) {
    startBtn.disabled = false;
    startBtn.innerHTML = '<span>立即开始脱敏处理</span>';
    alert('请求失败: ' + err);
  }
}

function renderFileResult(data) {
  const panel = document.getElementById('fileResultPanel');
  panel.classList.remove('hidden');

  const statusBadge = document.getElementById('resultStatusBadge');
  statusBadge.className = 'badge';
  if (data.status === 'PASS') {
    statusBadge.classList.add('badge-success');
    statusBadge.textContent = 'PASS 通过';
  } else if (data.status === 'WARN') {
    statusBadge.classList.add('badge-warning');
    statusBadge.textContent = 'WARN 需复核';
  } else {
    statusBadge.classList.add('badge-danger');
    statusBadge.textContent = 'FAIL 存在风险';
  }

  document.getElementById('resultSummaryText').textContent = 
    `已成功脱敏生成: ${data.out_filename}`;

  // Download buttons
  const btnFile = document.getElementById('btnDownloadFile');
  btnFile.href = data.download_file_url || data.download_url;
  btnFile.download = data.out_filename;

  const btnAudit = document.getElementById('btnDownloadAudit');
  if (data.download_audit_url) {
    btnAudit.href = data.download_audit_url;
    btnAudit.classList.remove('hidden');
  } else {
    btnAudit.classList.add('hidden');
  }

  const btnLedger = document.getElementById('btnDownloadLedger');
  if (data.download_ledger_url) {
    btnLedger.href = data.download_ledger_url;
    btnLedger.classList.remove('hidden');
  } else {
    btnLedger.classList.add('hidden');
  }

  // Alerts
  const alertsDiv = document.getElementById('fileResultAlerts');
  alertsDiv.innerHTML = '';
  if (data.warnings && data.warnings.length > 0) {
    const alertBox = document.createElement('div');
    alertBox.className = 'alert alert-warning';
    alertBox.innerHTML = '<div>' + data.warnings.map(w => '• ' + escapeHtml(w)).join('<br>') + '</div>';
    alertsDiv.appendChild(alertBox);
  }

  // Replacements table
  const repCount = data.replacements_count || (data.replacements ? data.replacements.length : 0);
  document.getElementById('replacementsCount').textContent = repCount;
  const tbody = document.querySelector('#replacementsTable tbody');
  tbody.innerHTML = '';
  (data.replacements || []).forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${escapeHtml(r.original)}</code></td>
      <td><strong>${escapeHtml(r.replacement)}</strong></td>
      <td><span class="badge badge-txt">${escapeHtml(r.category || '')}</span></td>
      <td style="color:var(--text-muted);">${escapeHtml(r.role || r.source || '')}</td>
    `;
    tbody.appendChild(tr);
  });

  // Suspects table
  const suspects = data.suspects || [];
  document.getElementById('suspectsCount').textContent = suspects.length;
  const suspSection = document.getElementById('suspectsSection');
  if (suspects.length > 0) {
    suspSection.classList.remove('hidden');
    const stbody = document.querySelector('#suspectsTable tbody');
    stbody.innerHTML = '';
    suspects.forEach(s => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><code style="color:var(--danger);">${escapeHtml(s.text)}</code></td>
        <td>${escapeHtml(s.category)}</td>
        <td style="color:var(--text-muted);">${escapeHtml(s.reason)}</td>
      `;
      stbody.appendChild(tr);
    });
  } else {
    suspSection.classList.add('hidden');
  }

  panel.scrollIntoView({ behavior: 'smooth' });
}

// TEXT REDACTION
async function startTextRedact() {
  const text = document.getElementById('textSourceInput').value.trim();
  if (!text) {
    alert('请先粘贴需要脱敏的文本内容');
    return;
  }
  const mode = document.getElementById('textModeSelect').value;
  const autoConf = document.getElementById('textAutoConfident').checked;

  try {
    const res = await fetch('/api/redact-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, mode, auto_confident: autoConf })
    });
    const data = await res.json();
    if (!data.ok) {
      alert('脱敏失败: ' + (data.error || '未知错误'));
      return;
    }
    document.getElementById('textOutput').value = data.redacted_text;
    currentLedgerData = data.ledger;

    document.getElementById('textResultBar').classList.remove('hidden');
    const badge = document.getElementById('textStatusBadge');
    badge.className = 'badge ' + (data.status === 'PASS' ? 'badge-success' : 'badge-warning');
    badge.textContent = data.status;
    document.getElementById('textHitsCount').textContent = `替换 ${data.replacements.length} 处敏感实体`;

    // Also populate into restore tab for convenience
    if (data.ledger && Object.keys(data.ledger).length > 0) {
      document.getElementById('restoreLedgerInput').value = JSON.stringify(data.ledger, null, 2);
    }
  } catch (err) {
    alert('请求异常: ' + err);
  }
}

function copyRedactedText() {
  const out = document.getElementById('textOutput').value;
  if (!out) return;
  navigator.clipboard.writeText(out).then(() => {
    alert('已复制脱敏文本到剪贴板，可直接粘贴给大模型！');
  });
}

function exportCurrentLedger() {
  if (!currentLedgerData) {
    alert('当前没有可导出的账本');
    return;
  }
  const blob = new Blob([JSON.stringify(currentLedgerData, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'text_redaction.ledger.json';
  a.click();
}

// RESTORE
async function startRestore() {
  const text = document.getElementById('restoreSourceInput').value.trim();
  const ledgerStr = document.getElementById('restoreLedgerInput').value.trim();
  if (!text) {
    alert('请粘贴大模型的答复文本');
    return;
  }
  if (!ledgerStr) {
    alert('请提供对应的 Ledger 账本 JSON');
    return;
  }

  let ledger;
  try {
    ledger = JSON.parse(ledgerStr);
  } catch (e) {
    alert('Ledger 账本 JSON 解析失败: ' + e);
    return;
  }

  try {
    const res = await fetch('/api/restore-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, ledger })
    });
    const data = await res.json();
    if (!data.ok) {
      alert('还原失败: ' + (data.error || '未知错误'));
      return;
    }
    document.getElementById('restoreOutput').value = data.restored_text;

    const alertBox = document.getElementById('restoreAlerts');
    alertBox.innerHTML = '';
    if (data.warnings && data.warnings.length > 0) {
      alertBox.innerHTML = '<div class="alert alert-warning">' + 
        data.warnings.map(w => '• ' + escapeHtml(w)).join('<br>') + '</div>';
    } else {
      alertBox.innerHTML = '<div class="alert alert-success">✅ 真实当事人还原完成，无未还原占位符。</div>';
    }
  } catch (err) {
    alert('还原请求失败: ' + err);
  }
}

function copyRestoredText() {
  const out = document.getElementById('restoreOutput').value;
  if (!out) return;
  navigator.clipboard.writeText(out).then(() => {
    alert('已复制还原后的真实文本到剪贴板！');
  });
}

// DOCTOR
async function fetchDoctorInfo() {
  try {
    const res = await fetch('/api/doctor');
    const data = await res.json();

    document.getElementById('docTesseract').innerHTML = data.tesseract 
      ? '<span class="badge badge-success">✅ 已就绪 (chi_sim)</span>' 
      : '<span class="badge badge-warning">未安装 (扫描件仅能转文本)</span>';

    document.getElementById('docOpenCV').innerHTML = data.opencv 
      ? '<span class="badge badge-success">✅ 已就绪</span>' 
      : '<span class="badge badge-txt">未安装 (可选: pip install .[vision])</span>';

    document.getElementById('docNER').innerHTML = (data.ner && data.ner.startsWith('active')) 
      ? '<span class="badge badge-success">✅ 已就绪</span>' 
      : `<span class="badge badge-txt" style="font-size:11px;">${escapeHtml(data.ner || '未安装')}</span>`;
  } catch (e) {
    console.error(e);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
</script>

</body>
</html>
"""
