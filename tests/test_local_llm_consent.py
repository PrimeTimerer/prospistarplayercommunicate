#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consent boundary for local model process ownership."""

from __future__ import annotations

import inspect
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import config_v2
import service as service_module


class LocalLlmConsentTests(unittest.TestCase):
    def test_configured_legacy_launcher_is_never_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            launcher = Path(temp) / "legacy-launcher.bat"
            launcher.write_text("@exit /b 0\n", encoding="utf-8")
            update = Mock()

            with patch.object(service_module, "_probe_llm_ports", return_value=False), patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("StarModeFeed must not start a local model process"),
            ) as popen:
                available = service_module.StarModeService._ensure_local_llm(
                    update,
                    {"llm_launcher": str(launcher)},
                )

            self.assertFalse(available)
            update.assert_not_called()
            popen.assert_not_called()

    def test_already_running_loopback_model_remains_available(self):
        update = Mock()
        with patch.object(service_module, "_probe_llm_ports", return_value=True):
            self.assertTrue(
                service_module.StarModeService._ensure_local_llm(
                    update,
                    {"llm_launcher": r"C:\ignored\legacy.bat"},
                )
            )
        update.assert_not_called()

    def test_availability_boundary_contains_no_process_launcher(self):
        source = inspect.getsource(service_module.StarModeService._ensure_local_llm)
        self.assertNotIn("Popen", source)
        self.assertNotIn("startfile", source)
        self.assertNotIn("cmd", source)
        self.assertIsNone(config_v2.DEFAULTS["llm_launcher"])


if __name__ == "__main__":
    unittest.main()
