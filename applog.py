#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rotating file logging for the packaged desktop app.

The executable runs without a console, so failures used to vanish. Every
module obtains a logger through ``get_logger`` and the first call installs a
rotating handler under ``%LOCALAPPDATA%\\StarModeFeed\\logs``. Logging must
never break the application: when the directory cannot be created the
logger silently falls back to a null handler.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

if getattr(sys, "frozen", False):
    _HERE = os.path.dirname(sys.executable)
else:
    _HERE = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", _HERE), "StarModeFeed", "logs")
LOG_PATH = os.path.join(LOG_DIR, "starmodefeed.log")
ROOT_NAME = "starmodefeed"

_setup_lock = threading.Lock()
_configured = False


def setup() -> logging.Logger:
    """Install the rotating file handler once and return the root app logger."""
    global _configured
    root = logging.getLogger(ROOT_NAME)
    with _setup_lock:
        if _configured:
            return root
        _configured = True
        root.setLevel(logging.INFO)
        root.propagate = False
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            handler: logging.Handler = logging.handlers.RotatingFileHandler(
                LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
        except OSError:
            handler = logging.NullHandler()
        root.addHandler(handler)
    return root


def get_logger(name: str) -> logging.Logger:
    setup()
    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"{ROOT_NAME}.{short}")


def show_fatal_dialog(title: str, message: str) -> None:
    """Best-effort Windows message box for a startup failure (no console)."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
    except Exception:
        pass
