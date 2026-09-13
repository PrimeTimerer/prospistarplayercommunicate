#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import community
import spotlight_engine
import stat_engine
from atomic_io import atomic_write_json, load_json
from job_manager import JobCancelled, JobConflictError, JobManager
from ledger_v2 import Ledger
from world_store import WorldStore, _legacy_base_key


def snapshot(
    *,
    date=(2027, 7, 24, 2),
    stats=None,
    content_hash="one",
    player_id=100866,
    profile="profile-a",
):
    return {
        "schema_version": 2,
        "player": {
            "id": player_id,
            "name": "Test Player",
            "team": "TEST TEAM",
            "pos": "Test Player(투수)",
        },
        "date": {
            "year": date[0],
            "month": date[1],
            "day": date[2],
            "career_year": date[3],
        },
        "stats": stats or {},
        "profile_fingerprint": profile,
        "source": {"file": "StarPlayer.dat", "slot": "00"},
        "provenance": {"season_stats": "save_verified"},
        "validation": {"container": "verified"},
        "content_hash": content_hash,
    }


class StatTruthTests(unittest.TestCase):
    def test_wins_never_invent_losses_starts_or_undefeated_streak(self):
        assessment = stat_engine.assess(
            {"pit_IP": 153, "pit_TBF": 465, "pit_K": 388, "pit_H": 4, "pit_W": 17}
        )
        combined = " ".join(assessment["facts"] + [assessment["line_pit"]])
        self.assertIn("시즌 17승", combined)
        self.assertNotIn("0패", combined)
        self.assertNotIn("무패", combined)
        self.assertNotIn("17경기", combined)

    def test_home_run_milestone_is_season_not_career(self):
        milestones = stat_engine.new_milestones({"bat_HR": 99}, {"bat_HR": 100})
        self.assertIn(("홈런", "시즌 100홈런 돌파"), milestones)
        self.assertNotIn("통산", " ".join(value for _, value in milestones))

    def test_all_batters_struck_out_is_not_promoted_to_perfect_game(self):
        lines = stat_engine.describe_game({"pit_IP": 9, "pit_TBF": 27, "pit_K": 27, "pit_H": 0})
        self.assertTrue(any("상대한 타자 전원 삼진" in line for line in lines))
        self.assertNotIn("퍼펙트", " ".join(lines))


class InningsArithmeticTests(unittest.TestCase):
    def test_innings_are_subtracted_in_outs_not_decimals(self):
        self.assertEqual(0.2, stat_engine.innings_delta(6.1, 5.2))
        self.assertEqual(1, stat_engine.innings_delta(154, 153))
        self.assertEqual(0.1, stat_engine.innings_delta(153.1, 153))
        self.assertEqual(7, stat_engine.innings_delta("160.2", "153.2"))
        self.assertEqual(-0.2, stat_engine.innings_delta(5.2, 6.1))
        self.assertEqual(0, stat_engine.innings_delta(None, None))

    def test_rate_stats_use_real_innings(self):
        self.assertAlmostEqual(153 + 2 / 3, stat_engine.innings_float("153.2"))
        self.assertEqual(153.0, stat_engine.innings_float(153))
        one_third = stat_engine.assess({"pit_IP": 9.1, "pit_K": 28})
        self.assertEqual(27.0, one_third["k9"])
        self.assertIn("9.1이닝", one_third["line_pit"])

    def test_ledger_game_delta_reports_partial_innings_as_outs(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            first = snapshot(
                date=(2027, 7, 24, 2),
                stats={"pit_IP": 5.2, "pit_TBF": 20, "pit_K": 8, "pit_H": 2},
                content_hash="first",
            )
            ledger.commit(ledger.classify(first))
            second = snapshot(
                date=(2027, 7, 25, 2),
                stats={"pit_IP": 6.1, "pit_TBF": 22, "pit_K": 9, "pit_H": 2},
                content_hash="second",
            )
            event = ledger.classify(second)
            self.assertEqual("NEW_GAME", event["kind"])
            self.assertEqual(0.2, event["delta"]["pit_IP"])
            self.assertEqual("pitching", event["role"])
            self.assertTrue(any("0.2이닝" in line for line in stat_engine.describe_game(event["delta"])))


class LedgerTests(unittest.TestCase):
    def test_no_change_is_not_appended_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            first = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="same")
            event = ledger.classify(first)
            self.assertEqual("WORLD_INIT", event["kind"])
            ledger.commit(event)
            second = ledger.classify(first)
            self.assertEqual("NO_CHANGE", second["kind"])
            ledger.commit(second)
            self.assertEqual(1, len(ledger.state["history"]))

    def test_legacy_hash_change_with_equal_facts_is_not_a_new_game(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            old = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="legacy-hash")
            ledger.commit(ledger.classify(old))
            migrated = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="v2-hash")
            event = ledger.classify(migrated)
            self.assertEqual("NO_CHANGE", event["kind"])
            ledger.commit(event)
            self.assertEqual(1, len(ledger.state["history"]))

    def test_season_accumulator_reset_becomes_baseline_not_fake_game(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            old = snapshot(
                date=(2027, 10, 20, 2),
                stats={"bat_AB": 500, "bat_H": 200, "bat_HR": 60, "pit_IP": 180, "pit_K": 250},
                content_hash="old",
            )
            ledger.commit(ledger.classify(old))
            new = snapshot(
                date=(2028, 3, 5, 3),
                stats={"bat_AB": 2, "bat_H": 1, "bat_HR": 0, "pit_IP": 0, "pit_K": 0},
                content_hash="new",
            )
            event = ledger.classify(new)
            self.assertEqual("SEASON_ROLLOVER", event["kind"])
            self.assertTrue(event["baseline_only"])
            ledger.commit(event)
            self.assertEqual([], ledger.state["history"][-1]["game_lines"])

    def test_corrupt_json_is_preserved_before_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            value, quarantined = load_json(path, {"fresh": True})
            self.assertEqual({"fresh": True}, value)
            self.assertIsNotNone(quarantined)
            self.assertTrue(Path(quarantined).exists())
            self.assertFalse(path.exists())


class WorldIsolationTests(unittest.TestCase):
    def test_same_slot_regressed_career_branches_without_deleting_old_world(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WorldStore(temp)
            save_path = str(Path(temp) / "00" / "StarPlayer.dat")
            first = snapshot(
                date=(2028, 9, 10, 3),
                stats={"bat_AB": 400, "bat_H": 160},
                content_hash="first",
            )
            ledger_one, world_one = store.resolve(first, save_path)
            ledger_one.commit(ledger_one.classify(first))
            restarted = snapshot(
                date=(2026, 3, 1, 1),
                stats={"bat_AB": 0, "bat_H": 0},
                content_hash="restart",
            )
            ledger_two, world_two = store.resolve(restarted, save_path)
            self.assertNotEqual(world_one["world_id"], world_two["world_id"])
            self.assertEqual(2, world_two["generation"])
            self.assertEqual("career-year-regression", world_two["branch_reason"])
            self.assertTrue(Path(ledger_one.path).exists())
            self.assertNotEqual(ledger_one.path, ledger_two.path)

    def test_profile_fingerprint_change_keeps_the_same_world(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WorldStore(temp)
            save_path = str(Path(temp) / "00" / "StarPlayer.dat")
            first = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="a", profile="copy-a")
            ledger_one, world_one = store.resolve(first, save_path)
            ledger_one.commit(ledger_one.classify(first))
            grown = snapshot(
                date=(2027, 7, 25, 2),
                stats={"bat_AB": 14, "bat_H": 5},
                content_hash="b",
                profile="copy-b-after-growth",
            )
            ledger_two, world_two = store.resolve(grown, save_path)
            self.assertEqual(world_one["world_id"], world_two["world_id"])
            self.assertEqual(ledger_one.path, ledger_two.path)
            self.assertFalse(world_two["created"])
            self.assertEqual("NEW_GAME", ledger_two.classify(grown)["kind"])

    def test_world_indexed_under_legacy_fingerprint_key_is_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WorldStore(temp)
            save_path = str(Path(temp) / "00" / "StarPlayer.dat")
            snap = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="a", profile="old-profile")
            ledger, world = store.resolve(snap, save_path)
            ledger.commit(ledger.classify(snap))
            # Rewrite the index the way the previous release stored it: only the
            # fingerprint-bound key points at the world.
            index, _ = load_json(store.index_path, {})
            legacy_key = _legacy_base_key(snap, save_path)
            index["active"] = {legacy_key: world["world_id"]}
            index["worlds"][world["world_id"]]["base_key"] = legacy_key
            atomic_write_json(store.index_path, index)

            again = snapshot(stats={"bat_AB": 10, "bat_H": 3}, content_hash="a", profile="old-profile")
            ledger_again, world_again = store.resolve(again, save_path)
            self.assertEqual(world["world_id"], world_again["world_id"])
            self.assertFalse(world_again["created"])
            self.assertEqual("NO_CHANGE", ledger_again.classify(again)["kind"])
            migrated, _ = load_json(store.index_path, {})
            self.assertEqual(legacy_key, migrated["worlds"][world["world_id"]]["base_key_legacy"])
            self.assertIn(legacy_key, migrated["active"])

    def test_invalid_legacy_ledger_is_preserved_and_does_not_block_world_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = Path(temp) / "legacy.json"
            legacy.write_text("{broken", encoding="utf-8")
            store = WorldStore(str(Path(temp) / "data"), str(legacy))
            ledger, world = store.resolve(snapshot(), str(Path(temp) / "00" / "StarPlayer.dat"))
            self.assertTrue(legacy.exists())
            self.assertTrue(Path(ledger.path).exists())
            self.assertEqual(1, world["generation"])


class DeterminismTests(unittest.TestCase):
    def test_feed_is_stable_for_the_same_verified_event(self):
        snap = snapshot(stats={"bat_AB": 10, "bat_H": 5, "bat_HR": 2}, content_hash="stable")
        event = {
            "kind": "NEW_GAME",
            "delta": {"bat_AB": 4, "bat_H": 2, "bat_HR": 1},
            "milestones": [],
            "snapshot": snap,
            "assess": stat_engine.assess(snap["stats"]),
        }
        config = {"heat": 7, "mode": "standard", "platforms": ["dc", "fmk", "mlb"]}
        spotlight = spotlight_engine.evaluate(snap, {}, event, config)
        self.assertEqual(
            community.build_feed(event, config, {}, spotlight=spotlight),
            community.build_feed(event, config, {}, spotlight=spotlight),
        )

    @staticmethod
    def _phrases(feed):
        values = [article["title"] for article in feed.get("media", [])]
        for board in feed.get("boards", []):
            values.append(board["title"])
            values.extend(comment["text"] for comment in board["comments"])
        return values

    def test_recent_exact_phrases_are_not_reused(self):
        snap = snapshot(stats={"bat_AB": 10, "bat_H": 5, "bat_HR": 2}, content_hash="stable")
        event = {
            "kind": "NEW_GAME",
            "delta": {"bat_AB": 4, "bat_H": 2, "bat_HR": 1},
            "milestones": [],
            "role": "batting",
            "snapshot": snap,
            "assess": stat_engine.assess(snap["stats"]),
        }
        config = {"heat": 7, "mode": "standard", "platforms": ["dc", "fmk", "mlb"]}
        spotlight = spotlight_engine.evaluate(snap, {}, event, config)
        first = community.build_feed(event, config, {}, spotlight=spotlight)
        previous = set(self._phrases(first))
        second = community.build_feed(
            event, config, {"recent_phrases": list(previous)}, spotlight=spotlight
        )
        self.assertTrue(previous.isdisjoint(self._phrases(second)))

    def test_pitching_only_article_does_not_claim_a_batting_appearance(self):
        snap = snapshot(
            stats={
                "pit_IP": 100,
                "pit_K": 180,
                "pit_H": 10,
                "pit_W": 12,
                "bat_AB": 200,
                "bat_H": 100,
                "bat_AVG": 0.5,
                "bat_HR": 40,
            },
            content_hash="pitching-only",
        )
        event = {
            "kind": "NEW_GAME",
            "delta": {"pit_IP": 7, "pit_K": 12, "pit_H": 1},
            "milestones": [],
            "role": "pitching",
            "snapshot": snap,
            "assess": stat_engine.assess(snap["stats"]),
        }
        config = {"heat": 7, "mode": "standard", "platforms": ["dc"]}
        feed = community.build_feed(
            event,
            config,
            {},
            spotlight=spotlight_engine.evaluate(snap, {}, event, config),
        )
        article = " ".join(" ".join(row["body"]) for row in feed["media"])
        self.assertIn("투수로서", article)
        self.assertNotIn("타자로서", article)


class JobControlTests(unittest.TestCase):
    def test_cancel_stops_worker_and_discards_late_result(self):
        manager = JobManager()
        gate = threading.Event()
        stopped = threading.Event()

        def worker(update):
            update("started", 5, "test")
            gate.wait(3)
            try:
                update("after cancel", 50, "test")
            except JobCancelled:
                stopped.set()
                raise
            return {"late": True}

        job = manager.start("check", worker)
        cancelled = manager.cancel(job["id"])
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual("JobCancelled", cancelled["error"]["type"])
        # A new job may start immediately even though the old thread is alive.
        second = manager.start("check", lambda update: {"value": 1})
        gate.set()
        self.assertTrue(stopped.wait(3))
        deadline = time.time() + 3
        while manager.get(second["id"])["status"] != "completed" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual("completed", manager.get(second["id"])["status"])
        self.assertEqual("cancelled", manager.get(job["id"])["status"])
        self.assertIsNone(manager.get(job["id"])["result"])
        self.assertIsNone(manager.cancel("missing"))

    def test_job_cancelled_while_queued_never_starts(self):
        # Hold the worker thread before _run acquires its first lock, the way
        # a scheduler delay would, by capturing the thread instead of starting it.
        import job_manager as job_manager_module

        captured = {}

        class HeldThread:
            def __init__(self, target=None, args=(), name=None, daemon=None):
                captured["target"] = target
                captured["args"] = args

            def start(self):
                return None

        ran = threading.Event()
        manager = JobManager()
        with patch.object(job_manager_module.threading, "Thread", HeldThread):
            job = manager.start("narrative", lambda update: ran.set() or {"late": True})
        self.assertEqual("queued", job["status"])
        self.assertEqual("cancelled", manager.cancel(job["id"])["status"])
        second = manager.start("narrative", lambda update: {"value": 2})
        deadline = time.time() + 3
        while manager.get(second["id"])["status"] != "completed" and time.time() < deadline:
            time.sleep(0.02)
        # Now the delayed first worker finally gets scheduled.
        captured["target"](*captured["args"])
        self.assertFalse(ran.is_set())
        first = manager.get(job["id"])
        self.assertEqual("cancelled", first["status"])
        self.assertIsNone(first["result"])
        self.assertIsNone(first["started_at"])
        self.assertEqual("completed", manager.get(second["id"])["status"])

    def test_timeout_is_reported_without_worker_cooperation(self):
        manager = JobManager(timeouts={"check": 1.0})
        gate = threading.Event()
        job = manager.start("check", lambda update: gate.wait(5))
        manager._jobs[job["id"]]["_deadline"] = time.monotonic() - 1
        polled = manager.get(job["id"])
        self.assertEqual("failed", polled["status"])
        self.assertEqual("JobTimeout", polled["error"]["type"])
        self.assertNotIn("_deadline", polled)
        gate.set()


class JobTests(unittest.TestCase):
    def test_job_ids_isolate_results_and_conflict_while_active(self):
        manager = JobManager()
        gate = threading.Event()

        def worker(update):
            update("started", 10, "test")
            gate.wait(2)
            return {"value": 7}

        first = manager.start("check", worker)
        with self.assertRaises(JobConflictError):
            manager.start("second", worker)
        gate.set()
        deadline = time.time() + 3
        while manager.get(first["id"])["status"] != "completed" and time.time() < deadline:
            time.sleep(0.02)
        finished = manager.get(first["id"])
        self.assertEqual("completed", finished["status"])
        self.assertEqual({"value": 7}, finished["result"])


if __name__ == "__main__":
    unittest.main()
