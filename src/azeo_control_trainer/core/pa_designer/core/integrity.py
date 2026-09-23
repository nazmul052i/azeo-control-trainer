from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import secrets
from typing import Any


_KEY_ENV = "PA_DESIGNER_INTEGRITY_KEY"
_KEY_FILE = ".pa_designer_integrity.key"
_LEGACY_KEY_ENV = "PROCEDURE_PILOT_INTEGRITY_KEY"
_LEGACY_KEY_FILE = ".procedure_pilot_integrity.key"


def _urlsafe_decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def integrity_key_path(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir).resolve() / _KEY_FILE


def _windows_dpapi(data: bytes, *, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    description = "PA Designer integrity key" if protect else None
    if protect:
        function.argtypes = [
            ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
    else:
        function.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
    function.restype = wintypes.BOOL
    if not function(
        ctypes.byref(source), description, None, None, None, 0x1, ctypes.byref(destination)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(destination.data, ctypes.c_void_p))


def _encode_local_key(key: bytes) -> str:
    if os.name == "nt":
        protected = _windows_dpapi(key, protect=True)
        return "dpapi:" + base64.urlsafe_b64encode(protected).decode("ascii")
    return "plain:" + base64.urlsafe_b64encode(key).decode("ascii")


def _decode_local_key(encoded: str) -> tuple[bytes, bool]:
    """Decode a key and report whether a legacy file should be upgraded."""

    if encoded.startswith("dpapi:"):
        if os.name != "nt":
            raise RuntimeError("A Windows DPAPI integrity key cannot be opened on this platform")
        protected = _urlsafe_decode(encoded.removeprefix("dpapi:"))
        return _windows_dpapi(protected, protect=False), False
    if encoded.startswith("plain:"):
        key = _urlsafe_decode(encoded.removeprefix("plain:"))
        return key, os.name == "nt"
    # Earlier releases stored raw base64. Read once and rewrite in the current format.
    return _urlsafe_decode(encoded), True


def _write_local_key(path: Path, key: bytes, *, replace: bool = True) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(_encode_local_key(key) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_or_create_integrity_key(runtime_dir: str | Path) -> bytes:
    """Return the installation audit key without storing it in signed records.

    Production installs should provision ``PA_DESIGNER_INTEGRITY_KEY`` from
    their secret manager. A protected local key is generated for standalone
    workstations so development and offline training remain self-contained.
    """

    configured_name = _KEY_ENV
    configured = os.environ.get(configured_name, "").strip()
    if not configured:
        configured_name = _LEGACY_KEY_ENV
        configured = os.environ.get(configured_name, "").strip()
    if configured:
        try:
            key = _urlsafe_decode(configured)
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError(f"{configured_name} must be URL-safe base64") from exc
        if len(key) < 32:
            raise RuntimeError(f"{configured_name} must decode to at least 32 bytes")
        return key

    path = integrity_key_path(runtime_dir)
    legacy_path = Path(runtime_dir).resolve() / _LEGACY_KEY_FILE
    if not path.exists() and legacy_path.is_file():
        try:
            legacy_encoded = legacy_path.read_text(encoding="ascii").strip()
            legacy_key, _ = _decode_local_key(legacy_encoded)
            if len(legacy_key) < 32:
                raise ValueError("key is too short")
            if _write_local_key(path, legacy_key, replace=False):
                return legacy_key
        except (ValueError, UnicodeError, binascii.Error, OSError) as exc:
            raise RuntimeError(f"Invalid PA Designer integrity key: {legacy_path}") from exc
    try:
        encoded = path.read_text(encoding="ascii").strip()
        key, upgrade = _decode_local_key(encoded)
        if len(key) < 32:
            raise ValueError("key is too short")
        if upgrade:
            _write_local_key(path, key)
        return key
    except FileNotFoundError:
        pass
    except (ValueError, UnicodeError, binascii.Error, OSError) as exc:
        raise RuntimeError(f"Invalid PA Designer integrity key: {path}") from exc

    key = secrets.token_bytes(32)
    if _write_local_key(path, key, replace=False):
        return key
    # Another process won the atomic creation race. Its key is authoritative.
    return load_or_create_integrity_key(runtime_dir)


def sign_payload(key: bytes, payload: Any) -> str:
    return hmac.new(key, canonical_json(payload), sha256).hexdigest()


def verify_signature(key: bytes, payload: Any, signature: str) -> bool:
    return bool(signature) and hmac.compare_digest(sign_payload(key, payload), signature)
