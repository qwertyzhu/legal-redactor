"""Ledger storage under ~/.legal-redactor, optional encryption, and restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import warnings
from pathlib import Path
from typing import Any

LEDGER_FILENAME = "ledger.json"
ENCRYPTED_FILENAME = "ledger.json.enc"
POINTER_FILENAME_SUFFIX = ".ledger-path.txt"


def ledger_root() -> Path:
    override = os.environ.get("LEGAL_REDACTOR_LEDGER_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".legal-redactor" / "ledgers"


def _chmod_private(path: Path) -> None:
    try:
        if path.is_dir():
            os.chmod(path, 0o700)
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def default_ledger_dir(input_path: Path, *, mode: str, output_stem: str) -> Path:
    """Stable per-input hash directory under ~/.legal-redactor/ledgers/."""
    key = f"{Path(input_path).resolve()}|{mode}|{output_stem}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return ledger_root() / digest


def resolve_ledger_path(
    input_path: Path,
    *,
    mode: str,
    output_path: Path,
    ledger: Path | None = None,
    legacy_beside_output: bool = False,
) -> Path:
    """Return the path where ledger.json should be written."""
    if ledger is not None:
        path = Path(ledger)
        if path.suffix.lower() != ".json":
            path = path / LEDGER_FILENAME
        return path
    if legacy_beside_output:
        return Path(output_path).parent / f"{Path(output_path).stem}.ledger.json"
    return default_ledger_dir(input_path, mode=mode, output_stem=Path(output_path).stem) / LEDGER_FILENAME


def write_ledger_payload(path: Path, payload: dict[str, Any], *, encrypt: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_private(path.parent)
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if encrypt:
        enc_path = path.with_suffix(path.suffix + ".enc") if path.suffix else path.parent / ENCRYPTED_FILENAME
        if path.name == LEDGER_FILENAME:
            enc_path = path.parent / ENCRYPTED_FILENAME
        _write_encrypted(enc_path, raw)
        _chmod_private(enc_path)
        # Do not leave plaintext beside encrypted copy
        if path.exists() and path != enc_path:
            try:
                path.unlink()
            except OSError:
                pass
        return enc_path
    path.write_bytes(raw)
    _chmod_private(path)
    return path


def write_ledger_pointer(work_dir: Path, output_stem: str, ledger_path: Path) -> Path:
    """Small pointer next to other sidecars so humans can find the home ledger."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    pointer = work_dir / f"{output_stem}{POINTER_FILENAME_SUFFIX}"
    pointer.write_text(str(Path(ledger_path).resolve()) + "\n", encoding="utf-8")
    return pointer


def load_ledger(path: Path, *, password: str | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        # allow directory
        if path.is_dir():
            enc = path / ENCRYPTED_FILENAME
            plain = path / LEDGER_FILENAME
            if enc.is_file():
                path = enc
            elif plain.is_file():
                path = plain
            else:
                raise FileNotFoundError(path)
        else:
            raise FileNotFoundError(path)
    data = path.read_bytes()
    if path.name.endswith(".enc") or data[:8] == b"LRLEDGER":
        data = _read_encrypted(path, password=password)
    return json.loads(data.decode("utf-8"))


def _derive_fernet(password: str, salt: bytes):
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ledger encryption requires the 'cryptography' package. "
            "Install with: pip install 'cryptography>=42'"
        ) from exc
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390_000)
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


def _password_from_env_or_arg(password: str | None) -> str:
    if password:
        return password
    env = os.environ.get("LEGAL_REDACTOR_LEDGER_PASS")
    if env:
        return env
    raise RuntimeError(
        "ledger encryption needs a password: set LEGAL_REDACTOR_LEDGER_PASS "
        "or pass --ledger-password"
    )


def _write_encrypted(path: Path, plaintext: bytes, password: str | None = None) -> None:
    password = _password_from_env_or_arg(password)
    salt = os.urandom(16)
    fernet = _derive_fernet(password, salt)
    token = fernet.encrypt(plaintext)
    path.write_bytes(b"LRLEDGER1" + salt + token)


def _read_encrypted(path: Path, password: str | None = None) -> bytes:
    password = _password_from_env_or_arg(password)
    blob = path.read_bytes()
    if not blob.startswith(b"LRLEDGER1"):
        raise ValueError(f"not an encrypted legal-redactor ledger: {path}")
    salt = blob[9:25]
    token = blob[25:]
    fernet = _derive_fernet(password, salt)
    return fernet.decrypt(token)


def reverse_mapping(entities: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], list[str]]:
    """replacement→original, longest first. Warn when one token maps to many originals."""
    warnings_out: list[str] = []
    by_repl: dict[str, list[str]] = {}
    for row in entities:
        original = str(row.get("original") or "").strip()
        replacement = str(row.get("replacement") or "").strip()
        if not original or not replacement or original == replacement:
            continue
        by_repl.setdefault(replacement, []).append(original)
    pairs: list[tuple[str, str]] = []
    for repl, originals in by_repl.items():
        uniq = list(dict.fromkeys(originals))
        if len(uniq) > 1:
            warnings_out.append(
                f"ambiguous restoration token {repl!r} → {uniq!r}; using first"
            )
        pairs.append((repl, uniq[0]))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs, warnings_out


def restore_text(text: str, ledger: dict[str, Any]) -> tuple[str, list[str]]:
    entities = ledger.get("entities") or []
    if isinstance(ledger.get("plan"), dict):
        entities = ledger["plan"].get("entities") or entities
    pairs, warns = reverse_mapping(list(entities))
    if not pairs:
        return text, warns + ["ledger has no reversible entities"]
    # sentinel pass to avoid chain replacement
    out = text
    sentinels: list[tuple[str, str]] = []
    for i, (repl, original) in enumerate(pairs):
        if not repl or repl not in out:
            continue
        token = f"⟦S{i}⟧"
        out = out.replace(repl, token)
        sentinels.append((token, original))
    for token, original in sentinels:
        out = out.replace(token, original)
    # surface leftover placeholders that look like ours
    leftovers = sorted(
        set(re.findall(r"某[甲乙丙丁戊己庚辛壬癸]\d*|某单位[A-Z]\d*|\[(?:身份证号|手机号|邮箱|账号|统一社会信用代码|车牌号|护照号|IP地址|出生日期|社交账号|不动产权证号|电话|已脱敏)\d*\]", out))
    )
    for item in leftovers[:10]:
        warns.append(f"possible unrestored placeholder remains: {item}")
    return out, warns


def restore_file(
    input_path: Path,
    ledger_path: Path,
    output_path: Path,
    *,
    password: str | None = None,
) -> tuple[str, list[str]]:
    from .formats import docx_io, pdf_io, text_io

    ledger = load_ledger(ledger_path, password=password)
    pairs, warns = reverse_mapping(
        list((ledger.get("entities") or ledger.get("plan", {}).get("entities") or []))
    )
    mapping = pairs  # already replacement→original as (repl, orig)
    suffix = Path(input_path).suffix.lower()
    output_path = Path(output_path)
    if suffix == ".docx":
        text = docx_io.redact_docx(input_path, output_path, mapping)
    elif suffix == ".pdf":
        text = pdf_io.redact_pdf(input_path, output_path, mapping)
    else:
        text = text_io.redact_text(input_path, output_path, mapping)
    # Extra text-level pass for anything mapping missed in structured formats
    restored, more = restore_text(text, ledger)
    if restored != text and suffix in {".txt", ".md", ".markdown", ".csv", ".json", ""}:
        output_path.write_text(restored, encoding="utf-8")
        text = restored
    return text, warns + more
