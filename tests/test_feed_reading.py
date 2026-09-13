"""Provider-neutral reading and evidence contracts; no live calls."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

import community_style_db

ROOT = Path(__file__).resolve().parents[1]


class FeedReadingTests(unittest.TestCase):
    def test_pure_renderer_and_filter_behaviour(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the real JavaScript renderer gate")
        result = subprocess.run([node, str(ROOT / "scripts/test_feed_reading.cjs")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["passed"], 12)

    def test_dc_register_defers_profanity_to_selected_language_policy(self):
        profile = community_style_db.prompt_profile("dc", language_level=5)
        self.assertIn("language_policy", profile["register"]["heat_rule"])
        self.assertNotIn("욕설과 집단 비하는 쓰지 않는다", profile["register"]["heat_rule"])
        self.assertEqual(profile["language_policy"]["level"], 5)
        low = community_style_db.prompt_profile("dc", language_level=1)
        self.assertIn("욕설·비속어 없이", low["language_policy"]["instruction"])

    def test_private_gem_records_are_not_published_as_prompt_evidence(self):
        ledger = community_style_db.source_ledger()
        blocked = {row["id"] for row in ledger if row["access"].startswith("redirected_to_logged_out")}
        self.assertEqual(len(blocked), 0)
        self.assertFalse(any(row["platform"] == "gemini" for row in ledger))
        for profile in community_style_db.load()["profiles"].values():
            self.assertFalse(blocked.intersection(profile["evidence_source_ids"]))

    def test_reference_screenshots_are_identified_as_simulation_examples(self):
        rows = {row["id"]: row for row in community_style_db.source_ledger()}
        reference = rows["dc-prospi-18120-browser-20260905"]
        self.assertIn("simulator_examples_not_live_platform_output", reference["scope"])
        self.assertEqual(community_style_db.manifest()["dataset_version"], "2026.09.13.public.1")

    def test_reading_controls_have_labels_and_live_counts(self):
        from tests.test_ui_contracts import ShellElements
        shell = ShellElements((ROOT / "ui/index.html").read_text(encoding="utf-8"))
        nodes = {attrs.get("id"): attrs for _, attrs in shell.elements}
        for kind in ("community", "media"):
            self.assertIn(kind + "Filter", nodes)
            self.assertEqual(nodes[kind + "Search"]["type"], "search")
            self.assertEqual(nodes[kind + "ReadingCount"]["aria-live"], "polite")
            self.assertIn("hidden", nodes[kind + "ReadingEmpty"])

    def test_preview_and_reading_never_start_generation(self):
        app = (ROOT / "ui/js/app.js").read_text(encoding="utf-8")
        preview = app.split("function renderLanguagePreview(level) {", 1)[1].split("\nfunction toast", 1)[0]
        self.assertNotIn("api.", preview)
        self.assertNotIn("fetch(", preview)
        render = (ROOT / "ui/js/render.js").read_text(encoding="utf-8")
        filtering = render.split("export function applyReadingFilters(kind) {", 1)[1].split("\nfunction renderBoardCard", 1)[0]
        self.assertNotIn("api.", filtering)
        self.assertNotIn("fetch(", filtering)
        self.assertIn("readingWorldKey !== nextWorldKey", render)
