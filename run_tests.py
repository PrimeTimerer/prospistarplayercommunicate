#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the dependency-free StarModeFeed regression suite."""

from __future__ import annotations

import os
import logging
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


if __name__ == "__main__":
    # Import product modules only after redirecting every default profile root.
    # No developer save, API key, browser profile or model process is required.
    with tempfile.TemporaryDirectory(prefix="StarModeFeed-tests-") as temp:
        isolated = dict(os.environ)
        isolated.update(LOCALAPPDATA=temp, APPDATA=temp, USERPROFILE=temp, HOME=temp)
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "STARMODEFEED_REAL_SAVE"):
            isolated.pop(name, None)
        with patch.dict(os.environ, isolated, clear=True):
            try:
                suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "tests"))
                result = unittest.TextTestRunner(verbosity=2).run(suite)
            finally:
                # Windows cannot remove an open rotating log file.
                logging.shutdown()
    raise SystemExit(0 if result.wasSuccessful() else 1)
