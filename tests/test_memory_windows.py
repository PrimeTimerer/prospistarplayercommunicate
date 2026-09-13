#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from ledger_v2 import Ledger
import memory_windows
import narrative_props
import narrative_state
import spotlight_engine
import story_engine


def _state() -> dict:
    return {
        "world_id": "world-one",
        "player": {"id": "player-one"},
        "reaction_instances": [],
        "history": [],
        "story_sessions": {},
        "conversation_turns": [],
        "world_events": [],
        "daily_archive": [],
        "narrative_props": {},
        "narrative_prop_events": [],
        "narrative_lifetime_events": [],
        "milestone_ledger": [],
        "memory_pins": [],
        "career_profile": {"honors": []},
    }


def _bundle(day: int, *, instance: str | None = None, **extra) -> dict:
    return {
        "kind": "save_delta",
        "instance_id": instance or f"instance-{day}",
        "source_hash": f"hash-{day}",
        "game_date": f"2027-07-{day:02d}",
        "universe_id": "world-one",
        "protagonist_id": "player-one",
        "event_ids": [f"event-{day}"],
        **extra,
    }


def _snapshot(day: int = 1) -> dict:
    return {
        "schema_version": 2,
        "player": {"id": "player-one", "name": "Paul Skenes", "team": "DB", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": day, "career_year": 2},
        "stats": {
            "bat_AB": 100, "bat_H": 40, "bat_HR": 10, "bat_RBI": 35,
            "bat_R": 30, "bat_SO": 20, "bat_SB": 8, "bat_AVG": 0.4,
            "pit_IP": 20, "pit_TBF": 70, "pit_H": 10, "pit_K": 30, "pit_W": 4,
        },
        "content_hash": f"snapshot-{day}",
        "profile_fingerprint": "profile-one",
    }


class LifetimePolicyTests(unittest.TestCase):
    def test_text_and_field_lifetimes_are_bounded(self):
        self.assertIsNone(memory_windows.normalize_lifetime_games())
        self.assertEqual(5, memory_windows.normalize_lifetime_games(text="이 소재는 5경기 동안 이어가자"))
        self.assertEqual(20, memory_windows.normalize_lifetime_games("20"))
        for value in (True, 0, 21, "반년"):
            with self.assertRaisesRegex(ValueError, "1~20"):
                memory_windows.normalize_lifetime_games(value)

    def test_legacy_records_keep_automatic_retrieval(self):
        self.assertTrue(memory_windows.is_automatic_retrieval_active({"name": "기존 소재"}))
        normalized = story_engine.normalize({"category": "fans", "situation": "fan_message"})
        self.assertNotIn("issue_lifetime_games", normalized)

    def test_one_unit_per_unique_committed_game_and_expiry_keeps_source(self):
        state = _state()
        prop = {
            "prop_id": "prop-one", "name": "햄버거 약속", "universe_id": "world-one",
            "protagonist_id": "player-one", "origin_date": "2027-07-01", "visibility": "clubhouse",
        }
        state["narrative_props"]["prop-one"] = prop
        memory_windows.attach_lifetime(
            state, prop, 2, record_kind="narrative_prop", record_id="prop-one",
            label=prop["name"], game_date="2027-07-01", universe_id="world-one",
            protagonist_id="player-one",
        )

        first = memory_windows.consume_verified_game(state, _bundle(2))
        duplicate = memory_windows.consume_verified_game(state, _bundle(2, instance="corrected-instance"))
        final = memory_windows.consume_verified_game(state, _bundle(3))

        self.assertEqual(1, first[0]["remaining_games"])
        self.assertEqual([], duplicate)
        self.assertEqual("expired", final[0]["status"])
        self.assertIn("prop-one", state["narrative_props"])
        self.assertFalse(memory_windows.is_automatic_retrieval_active(prop))
        self.assertEqual(["created", "consumed", "expired"], [row["action"] for row in state["narrative_lifetime_events"]])

    def test_preview_and_retrospective_inputs_do_not_consume(self):
        state = _state()
        prop = {"prop_id": "p", "name": "긴장", "universe_id": "world-one", "protagonist_id": "player-one"}
        state["narrative_props"]["p"] = prop
        memory_windows.attach_lifetime(
            state, prop, 3, record_kind="narrative_prop", record_id="p", label="긴장",
            game_date="2027-07-01", universe_id="world-one", protagonist_id="player-one",
        )
        before = copy.deepcopy(prop["retrieval_lifetime"])
        self.assertEqual([], memory_windows.consume_verified_game(state, {"kind": "preview"}))
        self.assertEqual([], memory_windows.consume_verified_game(state, _bundle(2, retrospective_import=True)))
        self.assertEqual(before, prop["retrieval_lifetime"])

    def test_historical_issue_view_has_no_future_expiry(self):
        state = _state()
        prop = {"prop_id": "p", "name": "라이벌 긴장", "universe_id": "world-one", "protagonist_id": "player-one", "visibility": "public"}
        state["narrative_props"]["p"] = prop
        memory_windows.attach_lifetime(
            state, prop, 2, record_kind="narrative_prop", record_id="p", label=prop["name"],
            game_date="2027-07-01", universe_id="world-one", protagonist_id="player-one",
        )
        memory_windows.consume_verified_game(state, _bundle(2))
        memory_windows.consume_verified_game(state, _bundle(3))
        past = memory_windows.build_view(
            state, as_of_date="2027-07-02", universe_id="world-one", protagonist_id="player-one"
        )["issues"][0]
        current = memory_windows.build_view(
            state, as_of_date="2027-07-03", universe_id="world-one", protagonist_id="player-one"
        )["issues"][0]
        self.assertEqual(("active", 1), (past["status"], past["remaining_games"]))
        self.assertEqual(("expired", 0), (current["status"], current["remaining_games"]))


class LayeredMemoryTests(unittest.TestCase):
    def test_recent_medium_and_season_windows_are_deterministic(self):
        state = _state()
        for day in range(1, 13):
            state["reaction_instances"].append(_bundle(day))
            state["history"].append(
                {"kind": "NEW_GAME", "date": f"2027-07-{day:02d}", "source_hash": f"hash-{day}", "game_lines": [f"{day}번째 경기"]}
            )
        state["career_profile"]["honors"].append(
            {"id": "mvp", "title": "월간 MVP", "occurred_on": "2027-07-02"}
        )
        state["milestone_ledger"].append(
            {"id": "k300", "title": "시즌 300탈삼진", "occurred_on": "2027-07-04"}
        )

        view = memory_windows.build_view(
            state, as_of_date="2027-07-12", universe_id="world-one", protagonist_id="player-one"
        )
        windows = {row["id"]: row for row in view["windows"]}
        short_games = {row["game_date"] for row in windows["short_recent_3"]["items"] if row["kind"] == "verified_game"}
        medium_games = {row["game_date"] for row in windows["medium_recent_4_10"]["items"] if row["kind"] == "verified_game"}
        season_games = {row["game_date"] for row in windows["long_season"]["items"] if row["kind"] == "verified_game"}
        self.assertEqual({"2027-07-10", "2027-07-11", "2027-07-12"}, short_games)
        self.assertEqual({f"2027-07-{day:02d}" for day in range(3, 10)}, medium_games)
        self.assertEqual({f"2027-07-{day:02d}" for day in range(1, 13)}, season_games)
        self.assertEqual({"월간 MVP", "시즌 300탈삼진"}, {row["label"] for row in view["anchors"]})

        historical = memory_windows.build_view(
            state, as_of_date="2027-07-08", universe_id="world-one", protagonist_id="player-one"
        )
        self.assertFalse(any(row["game_date"] > "2027-07-08" for window in historical["windows"] for row in window["items"]))

    def test_ledger_views_and_archive_reads_never_spend_lifetime(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world-one")
            snap = _snapshot()
            item = ledger.append_story_event(
                {"category": "fans", "situation": "fan_message", "target": "fans", "issue_lifetime_games": 3},
                snap,
                spotlight=spotlight_engine.evaluate(snap, {}, None, {"mode": "standard", "heat": 7}),
            )
            policy = item["retrieval_lifetime"]
            self.assertEqual(3, policy["remaining_games"])
            self.assertEqual(3, ledger.story_view(snap)["memory_views"]["issues"][0]["remaining_games"])
            ledger.history_capsule("2027-07-01", snap)
            narrative_state.conversation_state(ledger.state, "2027-07-01")
            self.assertEqual([], policy["consumed_game_ids"])
            ledger.save()
            reloaded = Ledger(str(Path(temp) / "ledger.json"), "world-one")
            self.assertEqual(3, reloaded.state["story_sessions"]["2027-07-01"]["turns"][0]["retrieval_lifetime"]["remaining_games"])

    def test_expired_prop_stops_automatic_callback_but_stays_resolvable(self):
        state = _state()
        result = narrative_props.handle_prop_act(
            state,
            act="prop_create",
            text="햄버거 얘기를 1경기 동안 내부 농담으로 만들자",
            understanding={"target": "teammate"},
            game_date="2027-07-01",
            universe_id="world-one",
            protagonist_id="player-one",
            lifetime_games=1,
        )
        prop = result["prop"]
        prop["recurrence_rules"]["trigger_events"].append(
            {"event_type": "SP.GAME.BAT.HOME_RUN", "scheduled_on": "2027-07-02", "fired": False}
        )
        memory_windows.consume_verified_game(state, _bundle(2))
        self.assertEqual([], narrative_props.due_callbacks(state, "SP.GAME.BAT.HOME_RUN", game_date="2027-07-02"))
        self.assertEqual(prop["prop_id"], narrative_props.resolve_prop(state, "햄버거 얘기")["prop_id"])
        view = memory_windows.build_view(
            state, as_of_date="2027-07-02", universe_id="world-one", protagonist_id="player-one"
        )
        self.assertNotIn("햄버거", memory_windows.prompt_section(view))
        self.assertIn("햄버거", memory_windows.prompt_section(view, query="햄버거 기억"))


class MemoryUiContractTests(unittest.TestCase):
    def test_memory_controls_are_wired_to_payload_and_renderer(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "ui" / "index.html").read_text(encoding="utf-8")
        app = (root / "ui" / "js" / "app.js").read_text(encoding="utf-8")
        renderer = (root / "ui" / "js" / "render.js").read_text(encoding="utf-8")
        css = (root / "ui" / "css" / "app.css").read_text(encoding="utf-8")
        for marker in ("storyLifetimeGames", "storyMemoryWindows", "storyIssueList", "storyMemoryAnchors"):
            self.assertIn(marker, html)
        self.assertIn("issue_lifetime_games", app)
        self.assertIn("function renderMemoryViews", renderer)
        self.assertIn(".story-memory-window-grid", css)
        self.assertIn("overflow: auto", css)


if __name__ == "__main__":
    unittest.main()
