#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit, app-owned lifecycle control for an optional local model server.

Configuration is never consent. Merely constructing this class or asking for
status performs no process launch. A process can be created only by ``start``
with the literal boolean ``confirmed=True``; ``stop`` can terminate only the
exact process tree started by this manager instance.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ALLOWED_LAUNCHER_SUFFIXES = {".bat", ".cmd", ".ps1", ".exe"}


class LocalModelControlError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LocalModelManager:
    """Own at most one launcher process without claiming external servers."""

    def __init__(
        self,
        *,
        log_path: str | os.PathLike,
        popen_factory: Callable = subprocess.Popen,
        run_factory: Callable = subprocess.run,
    ):
        self.log_path = Path(log_path).resolve()
        self._popen = popen_factory
        self._run = run_factory
        self._lock = threading.RLock()
        self._process = None
        self._log_handle = None
        self._started_at: str | None = None
        self._last_error: str | None = None

    @staticmethod
    def _launcher(config: dict | None) -> Path | None:
        value = (config or {}).get("llm_launcher")
        if not isinstance(value, str) or not value.strip():
            return None
        return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(value))))

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _close_log(self) -> None:
        handle, self._log_handle = self._log_handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def status(self, config: dict | None, *, reachable: bool) -> dict:
        with self._lock:
            launcher = self._launcher(config)
            alive = self._alive()
            if self._process is not None and not alive:
                return_code = self._process.poll()
                if not reachable and self._last_error is None:
                    self._last_error = f"로컬 모델 실행기가 연결 전에 종료되었습니다. (종료 코드 {return_code})"
                self._process = None
                self._close_log()
            if alive and reachable:
                state = "owned"
                message = "이 앱에서 시작한 로컬 LLM이 연결되어 있습니다."
            elif alive:
                state = "starting"
                message = "로컬 모델을 불러오는 중입니다. 모델 크기에 따라 시간이 걸릴 수 있습니다."
            elif reachable:
                state = "external"
                message = "앱 밖에서 실행한 로컬 LLM에 연결되어 있습니다. 이 앱에서는 종료할 수 없습니다."
            elif self._last_error:
                state = "failed"
                message = self._last_error
            else:
                state = "off"
                message = "로컬 LLM이 꺼져 있습니다. 저장된 경로만으로는 자동 실행되지 않습니다."
            launcher_exists = bool(launcher and launcher.is_file())
            return {
                "state": state,
                "reachable": bool(reachable),
                "owned": bool(alive),
                "pid": int(self._process.pid) if alive else None,
                "can_start": bool(not alive and not reachable and launcher_exists),
                "can_stop": bool(alive),
                "launcher_configured": bool(launcher),
                "launcher_exists": launcher_exists,
                "launcher_name": launcher.name if launcher else None,
                "started_at": self._started_at if alive else None,
                "last_error": self._last_error,
                "log_path": str(self.log_path),
                "message": message,
                "automatic_start": False,
            }

    @staticmethod
    def _command(launcher: Path) -> list[str]:
        suffix = launcher.suffix.lower()
        if suffix not in ALLOWED_LAUNCHER_SUFFIXES:
            raise LocalModelControlError(
                "LOCAL_MODEL_LAUNCHER_TYPE",
                "로컬 모델 실행 경로는 .bat, .cmd, .ps1 또는 .exe 파일이어야 합니다.",
            )
        if suffix in {".bat", ".cmd"}:
            command_processor = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
            return [command_processor, "/d", "/c", "call", str(launcher)]
        if suffix == ".ps1":
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if not powershell:
                raise LocalModelControlError(
                    "LOCAL_MODEL_POWERSHELL_MISSING",
                    "PowerShell 실행 파일을 찾지 못했습니다.",
                )
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher)]
        return [str(launcher)]

    def start(self, config: dict | None, *, confirmed: bool, reachable: bool) -> dict:
        with self._lock:
            if confirmed is not True:
                raise LocalModelControlError(
                    "LOCAL_MODEL_CONFIRMATION_REQUIRED",
                    "이번 실행에 대한 확인이 필요합니다. 저장된 설정은 실행 동의가 아닙니다.",
                )
            if self._alive() or reachable:
                return self.status(config, reachable=reachable)
            launcher = self._launcher(config)
            if launcher is None:
                raise LocalModelControlError(
                    "LOCAL_MODEL_LAUNCHER_MISSING",
                    "설정에서 로컬 모델 실행 파일 경로를 먼저 지정해 주세요.",
                )
            if not launcher.is_absolute() or not launcher.is_file():
                raise LocalModelControlError(
                    "LOCAL_MODEL_LAUNCHER_NOT_FOUND",
                    "지정한 로컬 모델 실행 파일을 찾지 못했습니다.",
                )
            command = self._command(launcher)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = self.log_path.open("ab", buffering=0)
            log_handle.write(f"\r\n[{_now()}] explicit local model start: {launcher.name}\r\n".encode("utf-8"))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            try:
                process = self._popen(
                    command,
                    cwd=str(launcher.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=flags,
                )
            except Exception as exc:
                log_handle.close()
                self._last_error = f"로컬 모델 실행기를 시작하지 못했습니다: {exc}"
                raise LocalModelControlError("LOCAL_MODEL_START_FAILED", self._last_error) from None
            self._process = process
            self._log_handle = log_handle
            self._started_at = _now()
            self._last_error = None
            return self.status(config, reachable=False)

    def _terminate_owned(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            self._close_log()
            return
        pid = int(process.pid)
        try:
            if os.name == "nt":
                result = self._run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if result.returncode != 0:
                        raise LocalModelControlError(
                            "LOCAL_MODEL_STOP_FAILED",
                            "이 앱에서 시작한 로컬 모델 프로세스 트리를 종료하지 못했습니다.",
                        )
                    process.terminate()
                    process.wait(timeout=5)
            else:  # pragma: no cover - the shipped application is Windows-only
                process.terminate()
                process.wait(timeout=10)
        finally:
            if process.poll() is not None:
                self._process = None
                self._started_at = None
                self._close_log()

    def stop(self, config: dict | None, *, confirmed: bool, reachable: bool) -> dict:
        with self._lock:
            if confirmed is not True:
                raise LocalModelControlError(
                    "LOCAL_MODEL_CONFIRMATION_REQUIRED",
                    "이번 종료에 대한 확인이 필요합니다.",
                )
            if not self._alive():
                if reachable:
                    raise LocalModelControlError(
                        "LOCAL_MODEL_EXTERNAL_PROCESS",
                        "이 로컬 LLM은 앱 밖에서 실행되어 StarPlayer가 종료할 수 없습니다.",
                    )
                return self.status(config, reachable=False)
            self._terminate_owned()
            self._last_error = None
            return self.status(config, reachable=False)

    def shutdown(self) -> None:
        """Close only an app-owned process tree; external servers are untouched."""

        with self._lock:
            if self._alive():
                self._terminate_owned()
