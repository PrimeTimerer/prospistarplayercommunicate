#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import unittest

import community_style_db
import provider_feed


class CommunityStyleDatabaseTests(unittest.TestCase):
    def test_database_is_derived_only_and_covers_every_runtime_surface(self):
        value = community_style_db.load()
        manifest = community_style_db.manifest()
        self.assertEqual("derived_features_only", manifest["storage_mode"])
        self.assertGreaterEqual(manifest["source_count"], 10)
        self.assertEqual(len(community_style_db.REQUIRED_PROFILES), manifest["profile_count"])
        self.assertEqual(64, len(manifest["manifest_sha256"]))
        self.assertFalse(value["storage_policy"]["raw_posts_stored"])
        self.assertFalse(value["storage_policy"]["usernames_stored"])
        for code in community_style_db.REQUIRED_PROFILES:
            profile = community_style_db.prompt_profile(code, selector="fixture")
            with self.subTest(code=code):
                self.assertEqual(code, profile["code"])
                self.assertGreaterEqual(len(profile["register"]), 5)
                self.assertTrue(profile["ui_cues"])

    def test_source_ledger_keeps_evidence_urls_without_copied_content(self):
        sources = community_style_db.source_ledger()
        self.assertTrue(all(row["url"].startswith("https://") for row in sources))
        forbidden = {"raw_posts", "raw_comments", "raw_text", "usernames", "images"}
        self.assertTrue(all(not forbidden.intersection(row) for row in sources))
        self.assertTrue(any(row["platform"] == "ptt" for row in sources))
        self.assertTrue(any(row["locale"] == "es" for row in sources))

    def test_translated_profile_selects_one_origin_register_not_a_blend(self):
        profile = community_style_db.prompt_profile("global-translation", selector="post-id")
        selected = profile["selected_origin_register"]
        self.assertIn(selected["id"], {"reddit-nested", "ptt-directional", "spanish-live"})
        self.assertIn("rhythm", selected)

    def test_language_policy_is_clamped_and_keeps_platform_registers_distinct(self):
        dc = community_style_db.language_policy(99, "dc")
        mlb = community_style_db.language_policy(5, "mlb")
        fallback = community_style_db.language_policy("broken", "unknown")

        self.assertEqual(5, dc["level"])
        self.assertEqual("극한 커뮤니티", dc["label"])
        self.assertIn("날것의 욕설", dc["platform_rule"])
        self.assertIn("존댓말", mlb["platform_rule"])
        self.assertEqual(2, fallback["level"])
        self.assertEqual("community_and_social_only", dc["applies_to"])
        self.assertIn("보호대상 혐오", dc["never_allowed"])

    def test_provider_neutral_prompt_contains_the_same_versioned_profiles(self):
        feed = {
            "media": [],
            "boards": [{
                "id": "dc-thread", "board": "DCInside", "code": "dc",
                "title": "선수 오늘 기록 뭐냐 ㅋㅋ", "comments": [],
            }],
            "social": [{
                "id": "global-post", "platform": "글로벌 야구 번역",
                "code": "global-translation", "author": "해외 야구 반응 번역",
                "text": "선수의 다음 경기를 기다린다는 반응이다.", "replies": [],
            }],
            "reaction_bundle": {"focus": {"protagonist_name": "선수"}},
            "editorial": {"facts": []},
        }
        _system, user, _allowed = provider_feed.build_prompt(
            feed,
            spotlight={"reaction_budget": {"expression_heat": 8, "mode": "standard"}},
        )
        packet = json.loads(user)
        self.assertEqual(
            community_style_db.manifest()["dataset_version"],
            packet["locked_surface_plan"]["boards"][0]["style_profile"]["database_version"],
        )
        self.assertEqual(
            "global-translation",
            packet["locked_surface_plan"]["social"][0]["style_profile"]["code"],
        )

    def test_provider_prompt_receives_the_selected_language_ceiling(self):
        source = {
            "media": [],
            "boards": [{
                "id": "dc-thread", "board": "DCInside", "code": "dc",
                "title": "선수 오늘 기록 뭐냐 ㅋㅋ", "comments": [],
            }],
            "social": [],
            "reaction_bundle": {"focus": {"protagonist_name": "선수"}},
            "editorial": {"facts": []},
        }
        _system, user, _allowed = provider_feed.build_prompt(
            source,
            spotlight={"reaction_budget": {"expression_heat": 4, "mode": "standard"}},
            language_level=5,
        )
        packet = json.loads(user)
        profile = packet["locked_surface_plan"]["boards"][0]["style_profile"]

        self.assertEqual(5, packet["style_controls"]["community_language_level"])
        self.assertEqual(5, profile["language_policy"]["level"])
        self.assertEqual("극한 커뮤니티", profile["language_policy"]["label"])
        self.assertEqual(4, packet["style_controls"]["expression_heat"])


if __name__ == "__main__":
    unittest.main()
