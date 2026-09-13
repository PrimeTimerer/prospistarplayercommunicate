#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Current-user secret storage for optional remote narrative providers.

Secrets are never written to the ordinary application configuration. On
Windows they are protected with DPAPI for the current user. Environment
variables remain a read-only alternative and take precedence, matching the
Gemini documentation's recommended deployment pattern.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from atomic_io import JsonStateError, atomic_write_json, load_json


SCHEMA_VERSION = 1
GEMINI_SECRET_ID = "gemini_api_key"
_ENTROPY = b"StarModeFeed/Gemini/v1"


class SecretStoreError(RuntimeError):
    """Raised when a secret cannot be protected or recovered safely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data or b"\0")
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _protect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("Windows 계정 암호화는 Windows에서만 사용할 수 있습니다.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    out_blob = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "StarModeFeed Gemini API key",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    )
    # Keep the backing buffers alive through the native call.
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError(f"Windows 계정 암호화에 실패했습니다 ({ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))


def _unprotect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("Windows 계정 암호화는 Windows에서만 사용할 수 있습니다.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    out_blob = _DataBlob()
    description = wintypes.LPWSTR()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError(f"저장된 API 키를 현재 Windows 계정으로 열지 못했습니다 ({ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))


def _clean_key(value: object) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("Gemini API 키를 입력해 주세요.")
    if len(key) > 4096 or any(character.isspace() for character in key):
        raise ValueError("Gemini API 키 형식이 올바르지 않습니다.")
    return key


class SecretStore:
    def __init__(
        self,
        path: str | os.PathLike,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ):
        self.path = Path(path)
        self._protect = protect or _protect_windows
        self._unprotect = unprotect or _unprotect_windows

    def _read(self, *, quarantine_corrupt: bool = True) -> dict:
        value, _quarantined = load_json(
            self.path,
            {"schema_version": SCHEMA_VERSION, "secrets": {}},
            quarantine_corrupt=quarantine_corrupt,
        )
        if not isinstance(value, dict):
            value = {}
        secrets = value.get("secrets")
        if not isinstance(secrets, dict):
            secrets = {}
        return {"schema_version": SCHEMA_VERSION, "secrets": secrets}

    def set_gemini_key(self, value: object) -> None:
        key = _clean_key(value)
        protected = self._protect(key.encode("utf-8"))
        state = self._read()
        state["secrets"][GEMINI_SECRET_ID] = {
            "scheme": "dpapi-current-user",
            "blob": base64.b64encode(protected).decode("ascii"),
        }
        atomic_write_json(self.path, state)

    def clear_gemini_key(self) -> bool:
        state = self._read()
        removed = state["secrets"].pop(GEMINI_SECRET_ID, None) is not None
        if removed:
            atomic_write_json(self.path, state)
        return removed

    def _environment_key(self) -> tuple[str | None, str | None]:
        # Google's documented precedence is GOOGLE_API_KEY over GEMINI_API_KEY.
        for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
            value = os.environ.get(name)
            if value:
                return _clean_key(value), name
        return None, None

    def get_gemini_key(self) -> tuple[str | None, str | None]:
        key, source = self._environment_key()
        if key:
            return key, f"environment:{source}"
        state = self._read()
        record = state["secrets"].get(GEMINI_SECRET_ID)
        if not isinstance(record, dict) or record.get("scheme") != "dpapi-current-user":
            return None, None
        try:
            protected = base64.b64decode(str(record.get("blob") or ""), validate=True)
            key = _clean_key(self._unprotect(protected).decode("utf-8"))
        except (ValueError, UnicodeError, SecretStoreError) as exc:
            raise SecretStoreError("저장된 Gemini API 키를 읽지 못했습니다. 키를 다시 저장해 주세요.") from exc
        return key, "windows_account"

    def public_status(self) -> dict:
        key, source = self._environment_key()
        if key:
            return {"configured": True, "source": f"environment:{source}"}
        # Dashboard/status reads are observational and must never rename even
        # a corrupt user secret file. The caller reports an unavailable key.
        try:
            state = self._read(quarantine_corrupt=False)
        except JsonStateError:
            return {"configured": False, "source": "unreadable"}
        record = state["secrets"].get(GEMINI_SECRET_ID)
        configured = bool(
            isinstance(record, dict)
            and record.get("scheme") == "dpapi-current-user"
            and record.get("blob")
        )
        return {"configured": configured, "source": "windows_account" if configured else None}
