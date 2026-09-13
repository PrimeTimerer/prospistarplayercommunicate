#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows global capture hotkeys with an isolated message-loop owner."""

from __future__ import annotations

import ctypes
import os
import threading
from datetime import datetime, timezone
from typing import Callable


HOTKEY_BINDINGS = (
    {"id": 0x5F01, "action": "single", "keys": "F8", "vk": 0x77, "count": 1},
    {"id": 0x5F02, "action": "burst", "keys": "F9", "vk": 0x78, "count": 3},
)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
MOD_NOREPEAT = 0x4000


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GlobalCaptureHotkeys:
    """Own RegisterHotKey registrations and dispatch capture work off-loop."""

    def __init__(self, callback: Callable[[int, str], dict]):
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._registered: dict[int, bool] = {}
        self._last_event: dict | None = None
        self._start_error: str | None = None

    def status(self) -> dict:
        with self._state_lock:
            bindings = [
                {
                    "action": row["action"],
                    "keys": row["keys"],
                    "count": row["count"],
                    "registered": bool(self._registered.get(row["id"])),
                }
                for row in HOTKEY_BINDINGS
            ]
            return {
                "supported": os.name == "nt",
                "active": bool(bindings) and all(row["registered"] for row in bindings),
                "bindings": bindings,
                "last_event": dict(self._last_event) if self._last_event else None,
                "error": self._start_error,
            }

    def start(self) -> dict:
        if self._thread and self._thread.is_alive():
            return self.status()
        if os.name != "nt":
            with self._state_lock:
                self._start_error = "Global capture hotkeys require Windows."
            return self.status()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._message_loop,
            name="StarModeFeed-Hotkeys",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=3)
        return self.status()

    def stop(self) -> None:
        thread = self._thread
        thread_id = self._thread_id
        if thread and thread.is_alive() and thread_id:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
            thread.join(timeout=3)
        self._thread = None
        self._thread_id = None

    def _set_event(self, **values) -> None:
        with self._state_lock:
            self._last_event = {"at": _now(), **values}

    def _dispatch(self, binding: dict) -> None:
        if not self._capture_lock.acquire(blocking=False):
            self._set_event(
                ok=False,
                action=binding["action"],
                count=0,
                message="이전 캡처가 아직 진행 중입니다.",
            )
            return

        def worker() -> None:
            try:
                result = self.callback(int(binding["count"]), "hotkey")
                self._set_event(
                    ok=bool(result.get("ok")),
                    action=binding["action"],
                    count=int(result.get("count") or 0),
                    message=str(result.get("message") or ""),
                )
                try:
                    import winsound

                    winsound.MessageBeep(
                        winsound.MB_OK if result.get("ok") else winsound.MB_ICONHAND
                    )
                except Exception:
                    pass
            except Exception as exc:
                self._set_event(
                    ok=False,
                    action=binding["action"],
                    count=0,
                    message=str(exc),
                )
            finally:
                self._capture_lock.release()

        threading.Thread(
            target=worker,
            name="StarModeFeed-HotkeyCapture",
            daemon=True,
        ).start()

    def _message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        from ctypes import wintypes

        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        msg = wintypes.MSG()
        self._thread_id = int(kernel32.GetCurrentThreadId())
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        modifiers = MOD_NOREPEAT
        try:
            with self._state_lock:
                self._registered = {
                    row["id"]: bool(user32.RegisterHotKey(None, row["id"], modifiers, row["vk"]))
                    for row in HOTKEY_BINDINGS
                }
                failed = [row["keys"] for row in HOTKEY_BINDINGS if not self._registered[row["id"]]]
                self._start_error = (
                    f"Could not register: {', '.join(failed)}" if failed else None
                )
            self._ready.set()
            by_id = {row["id"]: row for row in HOTKEY_BINDINGS}
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) in by_id:
                    self._dispatch(by_id[int(msg.wParam)])
        finally:
            for row in HOTKEY_BINDINGS:
                if self._registered.get(row["id"]):
                    user32.UnregisterHotKey(None, row["id"])
            with self._state_lock:
                self._registered = {}
            self._ready.set()
