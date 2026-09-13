#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Startup activation contracts for the optional Gemini provider."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import service as service_module
from provider_usage import ProviderUsageStore


class ProviderActivationTests(unittest.TestCase):
    def test_successful_probe_selects_gemini_and_recent_result_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {
                "output_dir": str(Path(temp) / "output"),
                "ai_provider": "local_auto",
                "gemini_consent": True,
                "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
            }
            key_store = Mock()
            key_store.get_gemini_key.return_value = ("saved-secret", "windows_account")

            def load():
                return copy.deepcopy(state)

            def save(value):
                state.clear()
                state.update(copy.deepcopy(value))

            def public(value):
                return {
                    **copy.deepcopy(value),
                    "gemini_key_present": True,
                    "gemini_key_source": "windows_account",
                    "gemini_usage": ProviderUsageStore(Path(temp) / "provider-usage.json").summary(),
                }

            app = service_module.StarModeService()
            with patch.object(app, "_secret_store", return_value=key_store), patch.object(
                service_module.config_module, "load", side_effect=load
            ), patch.object(
                service_module.config_module, "save", side_effect=save
            ), patch.object(
                service_module.config_module, "public_config", side_effect=public
            ), patch.object(
                service_module.config_module,
                "provider_usage_path",
                return_value=str(Path(temp) / "provider-usage.json"),
            ), patch.object(
                service_module.gemini_provider,
                "test_connection",
                return_value={"connected": True, "model": state["gemini_model"], "remote_name": "models/test"},
            ) as probe:
                first = app.auto_activate_gemini()
                second = app.auto_activate_gemini()

            self.assertTrue(first["connected"])
            self.assertTrue(first["attempted"])
            self.assertEqual("gemini", state["ai_provider"])
            self.assertTrue(second["connected"])
            self.assertFalse(second["attempted"])
            self.assertTrue(second["cached"])
            probe.assert_called_once_with(api_key="saved-secret", model=state["gemini_model"])

    def test_explicit_deterministic_setting_is_never_overridden(self):
        config = {
            "ai_provider": "deterministic",
            "gemini_consent": True,
            "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
        }
        app = service_module.StarModeService()
        secret = Mock()
        secret.get_gemini_key.return_value = ("saved-secret", "windows_account")
        with patch.object(app, "_secret_store", return_value=secret), patch.object(
            service_module.config_module, "load", return_value=config
        ), patch.object(
            service_module.config_module, "public_config", side_effect=lambda value: copy.deepcopy(value)
        ), patch.object(service_module.gemini_provider, "test_connection") as probe:
            result = app.auto_activate_gemini()
        self.assertEqual("explicit_offline", result["status"])
        self.assertEqual("deterministic", config["ai_provider"])
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
