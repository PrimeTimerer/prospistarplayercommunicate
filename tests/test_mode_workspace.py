"""Static preservation contracts complemented by real browser/loopback tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModeWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = (ROOT / "ui/js/workspace.js").read_text(encoding="utf-8")
        cls.activity = (ROOT / "ui/js/activity-console.js").read_text(encoding="utf-8")
        cls.api = (ROOT / "ui/js/api.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "ui/css/workspace.css").read_text(encoding="utf-8")

    def test_original_controls_move_without_duplicate_input_elements(self):
        for control in ("legacyStoryWorkspace", "cinematicWorkspace", "directorDesk",
                        "storyWorldbook", "storyMemory", "directorMemoryLibrary",
                        "opponentContextTools", "saveStatusTools", "captureTools"):
            self.assertIn(control, self.workspace)
        self.assertNotIn("cloneNode", self.workspace)
        self.assertNotIn("<textarea", self.workspace)

    def test_shared_tools_do_not_switch_active_mode(self):
        body = self.workspace.split("export function openSharedTools", 1)[1].split("function closeSharedTools", 1)[0]
        self.assertNotIn("switchView", body)
        self.assertIn("returnFocus", body)

    def test_console_observes_both_provider_dialogue_and_image_routes(self):
        for route in ("/api/v1/story/chat", "/api/v1/story/event",
                      "/api/v1/attachments/analyze-llm", "/api/v1/attachments/analyze-local",
                      "/api/v1/providers/gemini/test", "/api/v1/providers/local/start"):
            self.assertIn(route, self.activity)
        self.assertIn("beginRequest(path, body)", self.api)
        self.assertIn("finishRequest(activity, result)", self.api)
        self.assertIn("finishRequest(activity, null, error)", self.api)

    def test_console_keeps_request_bodies_and_secrets_out_of_storage(self):
        for forbidden in ("localStorage", "sessionStorage", "JSON.stringify(body)", "body.user_text",
                          "body.text", "body.images", "body.gemini_api_key"):
            self.assertNotIn(forbidden, self.activity)
        self.assertIn("entries.size > 24", self.activity)

    def test_console_projects_metadata_without_caching_dashboard_results(self):
        self.assertNotIn("remember({...job", self.activity)
        self.assertIn("remember({id:job.id,kind:job.kind", self.activity)
        self.assertNotIn("result:job.result", self.activity)

    def test_console_is_fixed_outside_modes_and_dialogs_have_mirrors(self):
        self.assertIn("document.body.append(card)", self.activity)
        self.assertIn("position: fixed", self.css)
        self.assertIn("--activity-height", self.css)
        self.assertIn("body.after(mirror)", self.activity)

    def test_no_fake_percent_or_cancellation_for_synchronous_inference(self):
        self.assertIn('removeAttribute("value")', self.activity)
        self.assertIn('!running || row.direct', self.activity)
        self.assertIn("진행률 미제공", self.activity)

    def test_mode_navigation_does_not_grant_consent_or_launch_models(self):
        for forbidden in ("api.post", "checked = true", "local/start", "local-model/start"):
            self.assertNotIn(forbidden, self.workspace)


if __name__ == "__main__":
    unittest.main()
