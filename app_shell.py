#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native WebView shell with Edge app-mode and browser fallbacks."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import applog
import config
from product_version import USER_AGENT
from ui_server import LocalAppServer

log = applog.get_logger(__name__)


def _edge_path() -> str | None:
    candidates = (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    )
    return str(next((path for path in candidates if path.is_file()), "")) or None


def _run_webview(url: str, app_config: dict) -> bool:
    try:
        import webview
    except Exception:
        return False
    storage = Path(os.environ.get("LOCALAPPDATA", config.HERE)) / "StarModeFeed" / "WebViewProfile"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.create_window(
            "StarPlayer Community Simulator",
            url,
            width=int(app_config.get("window_width", 1180)),
            height=int(app_config.get("window_height", 820)),
            min_size=(760, 620),
            resizable=True,
            background_color="#0b0e13",
            text_select=True,
            zoomable=True,
        )
        webview.start(
            debug=False,
            private_mode=False,
            storage_path=str(storage),
            user_agent=USER_AGENT,
        )
        return True
    except Exception:
        return False


def _run_edge(url: str) -> bool:
    edge = _edge_path()
    if not edge:
        return False
    profile = Path(os.environ.get("LOCALAPPDATA", config.HERE)) / "StarModeFeed" / "EdgeAppProfile"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            [
                edge,
                f"--app={url}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process.wait()
        return True
    except OSError:
        return False


def _run_browser(url: str) -> None:
    webbrowser.open(url)
    print(f"스타모드 피드 로컬 앱: {url}")
    print("브라우저 탭을 닫은 뒤 이 창에서 Ctrl+C를 눌러 종료하세요.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    try:
        _main()
    except Exception as exc:
        # The packaged app has no console; without this a startup failure
        # showed nothing at all. Log it, tell the user, then re-raise.
        log.exception("fatal startup error: %s", exc)
        applog.show_fatal_dialog(
            "StarModeFeed",
            f"앱을 시작하지 못했습니다.\n{type(exc).__name__}: {exc}\n\n로그: {applog.LOG_PATH}",
        )
        raise


def _main() -> None:
    app_config = config.load()
    server = LocalAppServer()
    url = server.start()
    log.info("local server started at %s", url)
    server.start_capture_hotkeys()
    force_browser = "--browser" in sys.argv[1:]
    try:
        if not force_browser and _run_webview(url, app_config):
            return
        if not force_browser and _run_edge(url):
            return
        _run_browser(url)
    finally:
        server.stop()
        log.info("local server stopped")


if __name__ == "__main__":
    main()
