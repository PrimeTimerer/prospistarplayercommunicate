#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
import subprocess
import sys
from pathlib import Path

import app_shell
import product_version
import service


class ProductVersionTests(unittest.TestCase):
    def test_runtime_badge_and_user_agent_share_one_version(self):
        self.assertRegex(product_version.APP_VERSION, r"^\d+\.\d+\.\d+-preview$")
        self.assertEqual(product_version.APP_VERSION, service.APP_VERSION)
        self.assertEqual(
            f"StarModeFeed/{product_version.APP_VERSION} LocalDesktop",
            app_shell.USER_AGENT,
        )

    def test_windows_tuple_and_build_resource_match_the_runtime_version(self):
        core = tuple(int(value) for value in product_version.APP_VERSION.split("-")[0].split("."))
        self.assertEqual((*core, 0), product_version.WINDOWS_VERSION)
        spec = (Path(__file__).resolve().parents[1] / "StarModeFeed.spec").read_text(encoding="utf-8")
        self.assertIn("version=version_info", spec)
        self.assertIn("from product_version import APP_VERSION, WINDOWS_VERSION", spec)

    def test_release_audit_requires_runtime_and_windows_version_parity(self):
        audit = (Path(__file__).resolve().parents[1] / "scripts" / "verify_packaged_release.py").read_text(encoding="utf-8")
        self.assertIn('"product_version"', audit)
        self.assertIn('"provider_feed"', audit)
        self.assertIn('and version["ok"]', audit)
        self.assertIn("read_version_info_from_executable", audit)

    def test_release_audit_entry_resolves_the_project_version_module(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "verify_packaged_release.py"), "--help"],
            cwd=root.parent,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
