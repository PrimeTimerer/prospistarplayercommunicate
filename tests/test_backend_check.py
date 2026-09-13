"""The optional package diagnostic must never enter the desktop/profile path."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BackendCheckTests(unittest.TestCase):
    def test_isolated_entry_exercises_http_and_retains_caller_profile(self):
        with tempfile.TemporaryDirectory(prefix="feed-check-entry-") as temp:
            root = Path(temp)
            sentinel = root / "StarModeFeed" / "config.json"
            sentinel.parent.mkdir()
            sentinel.write_text('{"sentinel":"must not read or rewrite"}', encoding="utf-8")
            before = sentinel.read_bytes()
            report = root / "report.json"
            entry = Path(__file__).resolve().parents[1] / "app_native.py"
            result = subprocess.run([sys.executable, str(entry), "--verify-backend", str(report)],
                                    capture_output=True, timeout=40, env={**os.environ, "LOCALAPPDATA": temp, "APPDATA": temp})
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(0, result.returncode, payload.get("error") or result.stderr.decode(errors="replace"))
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["normal_shutdown"])
            self.assertGreaterEqual(len(payload["checks"]), 15)
            self.assertIn("data/interactions/core-ko.json", payload["assets"])
            self.assertEqual(before, sentinel.read_bytes())
            self.assertEqual(["config.json"], [path.name for path in sentinel.parent.iterdir()])

    def test_existing_report_or_malformed_diagnostic_args_never_open_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "existing.json"
            report.write_text("keep this report", encoding="utf-8")
            entry = Path(__file__).resolve().parents[1] / "app_native.py"
            for args in (["--verify-backend", str(report)], ["--verify-backend"], ["--verify-backend", "--browser", str(report)]):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(entry), *args], capture_output=True, timeout=15)
                    self.assertEqual(64, result.returncode)
                    self.assertEqual("keep this report", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
