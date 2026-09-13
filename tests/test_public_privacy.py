"""Publication regressions use constructed markers, never real credentials."""
import json
from pathlib import Path
import unittest

from scripts.build_test_fixture import payloads
from scripts.verify_public_tree import path_allowed, text_findings

ROOT = Path(__file__).resolve().parents[1]


class PublicPrivacyTests(unittest.TestCase):
    def test_runtime_and_save_paths_are_excluded(self):
        for name in ("config.json","nested/secrets.json","00/StarPlayer.dat","output/day.json",
                     "backups/a.txt","model.gguf",".env.local","HANDOFF.md","avatar.png"):
            with self.subTest(name=name):
                self.assertFalse(path_allowed(name))

    def test_source_and_only_the_named_binary_are_allowed(self):
        for name in ("service.py","data/packs/core-ko/scenes.json","tests/fixtures/starplayer-plain.bin"):
            self.assertTrue(path_allowed(name))
        self.assertFalse(path_allowed("tests/fixtures/another.bin"))

    def test_all_fixtures_match_from_zero_generator(self):
        for name,data in payloads().items():
            with self.subTest(name=name):
                self.assertEqual(data,(ROOT/"tests/fixtures"/name).read_bytes())

    def test_constructed_keys_are_detected_without_echoing_values(self):
        key = "AI" + "za" + "A"*35
        findings = text_findings(key)
        self.assertEqual("google_api_key",findings[0]["kind"])
        self.assertNotIn(key,json.dumps(findings))

    def test_private_paths_and_shared_conversations_are_detected(self):
        samples = ["C:"+"/Users/"+"ExampleUser/file.txt",
                   "https://chatgpt.com/"+"share/"+"a"*20,
                   "https://gemini.google.com/"+"gem/"+"b"*20]
        self.assertTrue(all(text_findings(text) for text in samples))

    def test_reference_pack_is_generic_not_a_personal_conversation(self):
        pack = json.loads((ROOT/"data/story/skenes-reference.json").read_text(encoding="utf-8"))
        self.assertEqual(12,len(pack["items"]))
        self.assertTrue(all(item["basis"] == "fictional_reference" for item in pack["items"]))
        self.assertFalse(text_findings(json.dumps(pack)))
