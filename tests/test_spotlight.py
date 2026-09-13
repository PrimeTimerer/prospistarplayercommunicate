#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

import community
import spotlight_engine
import stat_engine
import story_engine
from service import StarModeService


def snapshot(stats=None, *, content_hash="spotlight"):
    return {
        "player": {"id": 99, "name": "Paul Skenes", "team": "Yokohama DeNA", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
        "stats": stats or {},
        "content_hash": content_hash,
        "profile_fingerprint": "skenes-profile",
    }


def event_for(snap, kind="NO_CHANGE"):
    return {
        "kind": kind,
        "snapshot": snap,
        "delta": {},
        "milestones": [],
        "assess": stat_engine.assess(snap.get("stats") or {}),
        "role": "no_appearance",
        "baseline_only": kind == "WORLD_INIT",
    }


def global_career():
    rows = [
        {"label": "득점", "status": "broken"},
        {"label": "홈런", "status": "broken"},
        {"label": "타점", "status": "broken"},
        {"label": "도루", "status": "broken"},
    ]
    return {
        "records": {"season": rows},
        "honor_summary": [],
        "timeline": [],
        "player_grade": {"code": "S+", "label": "MVP·슈퍼스타", "score": 86},
    }


def skenes_stats():
    return {
        "pit_IP": 153,
        "pit_TBF": 465,
        "pit_H": 4,
        "pit_K": 388,
        "pit_W": 17,
        "bat_HR": 105,
        "bat_RBI": 223,
        "bat_R": 174,
        "bat_SO": 11,
        "bat_SB": 129,
        "bat_AB": 277,
        "bat_H": 201,
        "bat_AVG": 0.726,
    }


class SpotlightScaleTests(unittest.TestCase):
    def test_unknown_rookie_stays_in_personal_world(self):
        snap = snapshot()
        value = spotlight_engine.evaluate(
            snap, {}, event_for(snap), {"mode": "standard", "heat": 7}
        )
        self.assertEqual("private", value["tier"])
        self.assertEqual(1, value["reaction_budget"]["boards"])
        self.assertEqual(0, value["reaction_budget"]["media"])
        self.assertEqual(["inner"], [row["id"] for row in value["waves"]])

        selection = story_engine.normalize(
            {
                "category": "personal",
                "situation": "quiet_evening",
                "target": "self",
                "visibility": "private",
            }
        )
        story = story_engine.build_event(snap, 1, selection, spotlight=value)
        self.assertEqual([], story["reactions"]["boards"])
        self.assertEqual([], story["reactions"]["media"])
        self.assertEqual(1, len(story["reactions"]["waves"]))

    def test_confirmed_honors_keep_attention_alive_on_a_quiet_day(self):
        snap = snapshot()
        career = {
            "honor_summary": [
                {"kind": "season_mvp", "label": "시즌 MVP", "count": 1},
                {"kind": "japan_series_mvp", "label": "일본시리즈 MVP", "count": 1},
                {"kind": "japan_series_champion", "label": "일본시리즈 우승", "count": 1},
                {"kind": "all_star_selection", "label": "올스타 선정", "count": 4},
            ],
            "records": {},
            "timeline": [],
        }
        value = spotlight_engine.evaluate(
            snap, career, event_for(snap), {"mode": "standard", "heat": 7}
        )
        self.assertGreaterEqual(value["tier_index"], 3)
        self.assertTrue(value["ambient_active"])
        self.assertEqual(0, value["components"]["current_event"])
        self.assertGreaterEqual(value["components"]["honors"], 45)

    def test_career_grade_keeps_established_star_reactive_without_today_event(self):
        snap = snapshot()
        career = {
            "honor_summary": [],
            "records": {},
            "timeline": [],
            "player_grade": {"code": "S", "label": "리그 스타", "score": 68},
        }
        value = spotlight_engine.evaluate(
            snap, career, event_for(snap), {"mode": "standard", "heat": 7}
        )
        self.assertGreater(value["components"]["career_stature"], 0)
        self.assertTrue(value["ambient_active"])
        self.assertTrue(any(row["key"] == "career_stature" for row in value["drivers"]))

    def test_current_skenes_profile_is_global_even_without_a_new_event(self):
        snap = snapshot(skenes_stats())
        value = spotlight_engine.evaluate(
            snap,
            global_career(),
            event_for(snap),
            {"mode": "standard", "heat": 7},
        )
        self.assertEqual("global", value["tier"])
        self.assertGreaterEqual(value["score"], 80)
        self.assertTrue(value["quiet_current_event"])
        self.assertEqual(7, value["reaction_budget"]["boards"])
        self.assertEqual(6, value["reaction_budget"]["media"])
        self.assertEqual(8, len(value["waves"]))

    def test_global_private_event_creates_indirect_world_reactions_without_leak_claim(self):
        snap = snapshot(skenes_stats())
        value = spotlight_engine.evaluate(
            snap, global_career(), event_for(snap), {"mode": "standard", "heat": 7}
        )
        selection = story_engine.normalize(
            {
                "category": "personal",
                "situation": "call_family",
                "target": "family",
                "visibility": "private",
            }
        )
        story = story_engine.build_event(snap, 1, selection, spotlight=value)
        self.assertEqual(3, len(story["reactions"]["boards"]))
        self.assertEqual(2, len(story["reactions"]["media"]))
        self.assertEqual(6, len(story["reactions"]["waves"]))
        article_text = " ".join(story["reactions"]["media"][0]["body"])
        # Privacy is enforced by the input boundary, not a repeated disclaimer
        # inside every article. Keep the established indirect-reaction budget.
        self.assertIn("388", article_text)
        for row in story["reactions"]["media"] + story["reactions"]["boards"]:
            self.assertEqual("public_record_background", row["context_visibility"])
        for leaked in ("가족", "통화", "비공개 행동", "세계선"):
            self.assertNotIn(leaked, article_text)

    def test_global_feed_meets_deterministic_reaction_budget(self):
        snap = snapshot(skenes_stats())
        event = event_for(snap)
        config = {"mode": "standard", "heat": 7, "platforms": ["dc", "fmk", "mlb"]}
        value = spotlight_engine.evaluate(snap, global_career(), event, config)
        first = community.build_feed(event, config, {}, spotlight=value)
        second = community.build_feed(event, config, {}, spotlight=value)
        self.assertEqual(first, second)
        self.assertEqual(7, len(first["boards"]))
        self.assertEqual(6, len(first["media"]))
        self.assertEqual(38, sum(len(row["comments"]) for row in first["boards"]))
        self.assertEqual("global", first["spotlight"]["tier"])

    def test_heat_changes_expression_metadata_but_never_reaction_volume(self):
        snap = snapshot(skenes_stats())
        event = event_for(snap)
        low = spotlight_engine.evaluate(snap, global_career(), event, {"mode": "standard", "heat": 1})
        high = spotlight_engine.evaluate(snap, global_career(), event, {"mode": "standard", "heat": 10})
        low_budget = dict(low["reaction_budget"])
        high_budget = dict(high["reaction_budget"])
        self.assertEqual(1, low_budget.pop("expression_heat"))
        self.assertEqual(10, high_budget.pop("expression_heat"))
        self.assertEqual(low_budget, high_budget)

    def test_last_verified_event_keeps_its_pressure_after_same_day_reopen(self):
        snap = snapshot(skenes_stats())

        class FakeLedger:
            def career_view(self, _snapshot):
                return global_career()

            def recent_events(self, _count):
                return [
                    {
                        "kind": "NEW_GAME",
                        "date": snap["date"],
                        "delta": {"bat_AB": 5, "bat_H": 5, "bat_HR": 5, "bat_RBI": 7},
                        "milestones": [("홈런", "시즌 110홈런 돌파")],
                        "baseline_only": False,
                    }
                ]

        value = StarModeService._spotlight(
            {"mode": "explosion", "heat": 10}, FakeLedger(), event_for(snap)
        )
        self.assertEqual(100, value["score"])
        self.assertTrue(value["same_day_echo_active"])
        self.assertEqual(18, value["components"]["current_event"])
        self.assertEqual("stored_same_day_save_delta", next(
            row["provenance"] for row in value["drivers"] if row["key"] == "current_event"
        ))


if __name__ == "__main__":
    unittest.main()
