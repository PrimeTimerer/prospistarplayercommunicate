#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ledger_v2 import Ledger
import spotlight_engine
import story_engine
import value_engine


def _spot(snap):
    return spotlight_engine.evaluate(snap, {}, None, {"mode": "standard", "heat": 7})


def snapshot(day=1, *, content_hash=None, stats=None):
    return {
        "schema_version": 2,
        "player": {"id": 99, "name": "폴 스킨스", "team": "TEST", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": day, "career_year": 2},
        "stats": stats
        or {
            "bat_AB": 100,
            "bat_H": 40,
            "bat_HR": 10,
            "bat_RBI": 35,
            "bat_R": 30,
            "bat_SO": 20,
            "bat_SB": 8,
            "bat_AVG": 0.4,
            "pit_IP": 20,
            "pit_TBF": 70,
            "pit_H": 10,
            "pit_K": 30,
            "pit_W": 4,
        },
        "content_hash": content_hash or f"hash-{day}",
        "profile_fingerprint": "profile",
    }


class StoryLedgerTests(unittest.TestCase):
    def test_schema_three_migrates_to_append_only_story_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.json"
            path.write_text('{"schema_version":3,"history":[],"daily_archive":[]}', encoding="utf-8")
            ledger = Ledger(str(path), "world")
            self.assertEqual(5, ledger.state["schema_version"])
            self.assertEqual({}, ledger.state["story_sessions"])
            self.assertEqual({}, ledger.state["day_contexts"])
            self.assertEqual({}, ledger.state["day_snapshots"])

    def test_same_day_turns_append_and_are_sealed_only_when_date_advances(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            day_one = snapshot(1)
            ledger.commit(ledger.classify(day_one))
            first = ledger.append_story_event(
                {"category": "fans", "situation": "fan_message", "target": "fans"},
                day_one, spotlight=_spot(day_one)
            )
            second = ledger.append_story_event(
                {"category": "media", "situation": "record_pressure", "target": "reporter"},
                day_one, spotlight=_spot(day_one)
            )
            session = ledger.state["story_sessions"]["2027-07-01"]
            self.assertEqual([1, 2], [first["sequence"], second["sequence"]])
            self.assertEqual(2, len(session["turns"]))
            self.assertEqual("open", session["status"])

            day_two = snapshot(2)
            ledger.commit(ledger.classify(day_two))
            self.assertEqual("sealed", session["status"])
            with self.assertRaisesRegex(ValueError, "마감"):
                ledger.append_story_event(
                    {"category": "fans", "situation": "fan_message", "target": "fans"},
                    day_one, spotlight=_spot(day_one)
                )

    def test_button_and_llm_events_share_shape_but_keep_provenance(self):
        snap = snapshot()
        selection = story_engine.normalize(
            {
                "category": "career",
                "situation": "meet_agent",
                "target": "agent",
                "tone": "honest",
                "visibility": "private",
                "user_text": "지금 가치가 어느 정도인지 알고 싶다.",
            },
            chat=True,
        )
        button = story_engine.build_event(snap, 1, selection, spotlight=_spot(snap))
        llm = story_engine.build_event(
            snap, 1, selection, spotlight=_spot(snap), source="llm", llm_text="에이전트가 자료를 펼쳤다.", model="local"
        )
        self.assertEqual(button.keys(), llm.keys())
        self.assertEqual("fictional_intervention", button["provenance"])
        self.assertEqual("llm_generated_fiction", llm["provenance"])
        self.assertEqual("에이전트가 자료를 펼쳤다.", llm["scene"]["response"])

    def test_story_overlay_reaches_community_and_press_without_changing_archive(self):
        snap = snapshot()
        session = story_engine.new_session(snap)
        selection = story_engine.normalize(
            {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
        )
        story_engine.append_event(
            session, story_engine.build_event(snap, 1, selection, spotlight=_spot(snap))
        )
        session["turns"][0]["reactions"]["social"] = [{"id": "story-social"}]
        original = {"boards": [{"code": "base"}], "media": [], "social": [{"id": "base-social"}]}
        overlaid = story_engine.overlay_feed(original, session)
        self.assertEqual("base", original["boards"][0]["code"])
        self.assertEqual(2, len(overlaid["boards"]))
        self.assertEqual(1, len(overlaid["media"]))
        self.assertEqual(["story-social", "base-social"], [row["id"] for row in overlaid["social"]])


class ValueEngineTests(unittest.TestCase):
    def _context(self):
        snap = snapshot()
        payload = {
            "standings": [
                {"league": "센트럴", "rank": 1, "team": "TEST", "wins": 60, "losses": 30, "ties": 2, "games_back": 0},
                {"league": "센트럴", "rank": 2, "team": "RIVAL", "wins": 55, "losses": 35, "ties": 2, "games_back": 5},
            ],
            "leaderboard": {
                "stat_key": "pit_K",
                "label": "탈삼진",
                "league": "센트럴",
                "entries": [
                    {"rank": 1, "name": "폴 스킨스", "team": "TEST", "value": "300", "is_player": True},
                    {"rank": 2, "name": "A", "team": "RIVAL", "value": "180"},
                ],
            },
            "saber_inputs": {
                "bat_BB": 10,
                "bat_HBP": 2,
                "bat_2B": 8,
                "bat_3B": 2,
                "bat_SF": 3,
                "pit_ER": 5,
                "pit_BB": 4,
                "pit_HBP": 1,
                "pit_HR": 2,
                "fip_constant": 3.1,
                "manual_war": 8,
                "yen_per_war": 100_000_000,
            },
            "salary": {
                "current_yen": 100_000_000,
                "mission_successes": 5,
                "manager_eval": 90,
                "club_eval": 85,
                "star_level": 95,
            },
        }
        return snap, value_engine.merge_context(None, payload, snap)

    def test_exact_supported_sabermetrics_and_missing_values_are_separate(self):
        snap, context = self._context()
        view = value_engine.build_view(context, snap)
        batting = {row["id"]: row for row in view["sabermetrics"]["batting"]}
        pitching = {row["id"]: row for row in view["sabermetrics"]["pitching"]}
        self.assertAlmostEqual(52 / 115, batting["obp"]["value"], places=4)
        self.assertAlmostEqual(0.82, batting["slg"]["value"], places=4)
        self.assertAlmostEqual(2.15, pitching["fip"]["value"], places=4)
        self.assertTrue(view["sabermetrics"]["war"]["available"])

        missing = value_engine.build_view(None, snap)
        rows = {row["id"]: row for row in missing["sabermetrics"]["batting"]}
        self.assertFalse(rows["obp"]["available"])
        self.assertIn("BB", rows["obp"]["missing"])
        self.assertFalse(missing["sabermetrics"]["war"]["available"])

    def test_salary_projection_keeps_game_offer_and_saber_market_value_distinct(self):
        snap, context = self._context()
        projection = value_engine.build_view(context, snap)["salary_projection"]
        self.assertTrue(projection["available"])
        self.assertEqual(800_000_000, projection["saber_market_value_yen"])
        self.assertNotEqual(projection["projected_yen"], projection["saber_market_value_yen"])
        self.assertEqual(3, len(projection["negotiation_rounds"]))
        self.assertLessEqual(projection["low_yen"], projection["projected_yen"])
        self.assertGreaterEqual(projection["high_yen"], projection["projected_yen"])

    def test_leaderboard_requires_player_in_top_five(self):
        snap = snapshot()
        with self.assertRaisesRegex(ValueError, "내 선수"):
            value_engine.merge_context(
                None,
                {
                    "leaderboard": {
                        "stat_key": "hr",
                        "label": "홈런",
                        "entries": [{"rank": 1, "name": "다른 선수", "value": "20"}],
                    }
                },
                snap,
            )

    def test_history_capsule_keeps_frozen_snapshot_and_all_daily_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            day_one = snapshot(1)
            ledger.commit(ledger.classify(day_one))
            ledger.remember_daily_archive({"id": "a", "game_date": "2027-07-01"})
            ledger.remember_daily_archive({"id": "b", "game_date": "2027-07-01"})
            ledger.append_story_event(
                {"category": "fans", "situation": "fan_message", "target": "fans"}, day_one, spotlight=_spot(day_one)
            )
            day_two = snapshot(2, stats={**day_one["stats"], "pit_K": 40})
            ledger.commit(ledger.classify(day_two))
            capsule = ledger.history_capsule("2027-07-01", day_two)
            self.assertEqual(30, capsule["verified_snapshot"]["stats"]["pit_K"])
            self.assertEqual(2, len(capsule["reaction_archives"]))
            self.assertEqual("sealed", capsule["story"]["status"])
            self.assertFalse(capsule["editable"])


if __name__ == "__main__":
    unittest.main()
