#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preserved-world registry, live-binding guard, museum reads, relink, and API.

Golden scenarios 20, 24, 31, 32, 33, 34, 35 of the master plan in synthetic
form: every state lives in a temporary directory, the "save" is a stub file,
and the parser is patched to return synthetic verified snapshots.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import service as service_module
from ledger_v2 import Ledger
from ui_server import LocalAppServer
from universe_registry import (
    MISSING_CHECKS_BEFORE_PRESERVE,
    MISSING_SECONDS_BEFORE_PRESERVE,
    UniverseRegistry,
    WorldBindingChangedError,
    WorldReadOnlyError,
    save_fingerprint,
    summarize_ledger,
)
from world_store import WorldStore


def _snapshot(*, player_id=100, name="Test Player", day=24, content_hash="h1", stats=None, slot="00"):
    return {
        "schema_version": 2,
        "player": {"id": player_id, "name": name, "team": "TEST TEAM", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": day, "career_year": 2},
        "stats": stats or {"pit_IP": 20, "pit_TBF": 70, "pit_H": 3, "pit_K": 40, "pit_W": 2},
        "profile_fingerprint": "profile",
        "source": {"file": "StarPlayer.dat", "slot": slot},
        "provenance": {"season_stats": "save_verified"},
        "validation": {"container": "verified", "verified_chunks": 51, "chunk_count": 51},
        "content_hash": content_hash,
    }


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RegistryStateMachineTests(unittest.TestCase):
    def _world(self, data_dir, save_path, snap):
        store = WorldStore(data_dir)
        ledger, world = store.resolve(snap, save_path)
        ledger.commit(ledger.classify(snap))
        return ledger, world

    def test_world_index_migrates_into_preserved_rows_without_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            snap = _snapshot()
            self._world(data_dir, save_path, snap)
            index_before = (data_dir / "world-index.json").read_bytes()
            registry = UniverseRegistry(data_dir)
            listing = registry.list_universes()
            self.assertEqual(1, len(listing["universes"]))
            card = listing["universes"][0]
            self.assertEqual("preserved_read_only", card["state"])
            self.assertEqual("migrated_from_world_index", card["preservation_reason"])
            self.assertEqual("Test Player", card["player_name"])
            self.assertEqual(1, card["counts"]["verified_events"])
            self.assertEqual(index_before, (data_dir / "world-index.json").read_bytes())

    def test_live_observation_binds_and_a_replaced_slot_preserves_the_former_universe(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            registry = UniverseRegistry(data_dir)
            first = _snapshot(player_id=100, name="First Player")
            ledger_a, world_a = self._world(data_dir, save_path, first)
            token = registry.observe_live(world_a, first, save_path, ledger_state=ledger_a.state)
            self.assertTrue(token["live"])
            self.assertEqual("live", token["state"])
            again = registry.observe_live(world_a, first, save_path, ledger_state=ledger_a.state)
            self.assertEqual(token["binding_id"], again["binding_id"])
            self.assertEqual(1, len(registry.bindings(world_a["world_id"])))
            registry.require_live_binding(token)

            second = _snapshot(player_id=200, name="Second Player", content_hash="h2")
            ledger_b, world_b = self._world(data_dir, save_path, second)
            self.assertNotEqual(world_a["world_id"], world_b["world_id"])
            token_b = registry.observe_live(world_b, second, save_path, ledger_state=ledger_b.state)
            self.assertTrue(token_b["live"])
            former = registry.get(world_a["world_id"])
            self.assertEqual("preserved_read_only", former["state"])
            self.assertEqual("slot_identity_replaced", former["preservation_reason"])
            with self.assertRaises(WorldReadOnlyError):
                registry.require_live_binding(token)
            listing = registry.list_universes([{"path": save_path}])
            states = {row["player_name"]: row for row in listing["universes"]}
            self.assertTrue(states["Second Player"]["capabilities"]["can_continue"])
            self.assertFalse(states["First Player"]["capabilities"]["can_continue"])
            self.assertTrue(states["First Player"]["capabilities"]["can_view"])
            self.assertEqual("보존·관람 전용", states["First Player"]["availability_badge"])
            self.assertEqual(1, listing["preserved_count"])
            self.assertEqual(1, listing["live_count"])

    def test_missing_save_is_bounded_then_preserved_and_identical_return_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            clock = FakeClock()
            registry = UniverseRegistry(data_dir, clock=clock)
            snap = _snapshot()
            ledger, world = self._world(data_dir, save_path, snap)
            token = registry.observe_live(world, snap, save_path, ledger_state=ledger.state)
            os.remove(save_path)
            affected = registry.observe_missing(save_path)
            self.assertEqual("temporarily_missing", affected[0]["state"])
            for _ in range(MISSING_CHECKS_BEFORE_PRESERVE):
                registry.observe_missing(save_path)
            # Not enough wall time has passed: still only temporarily missing.
            self.assertEqual("temporarily_missing", registry.get(world["world_id"])["state"])
            clock.advance(MISSING_SECONDS_BEFORE_PRESERVE + 1)
            registry.observe_missing(save_path)
            row = registry.get(world["world_id"])
            self.assertEqual("preserved_read_only", row["state"])
            self.assertEqual("save_missing", row["preservation_reason"])
            with self.assertRaises(WorldReadOnlyError):
                registry.require_live_binding(token)
            # The identical file returns: nothing diverged, so it resumes.
            Path(save_path).write_bytes(b"save")
            resumed = registry.observe_live(world, snap, save_path, ledger_state=ledger.state)
            self.assertTrue(resumed["live"])
            self.assertEqual("live_relinked", resumed["state"])
            registry.require_live_binding(resumed)

    def test_newer_returning_save_requires_explicit_relink(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            clock = FakeClock()
            registry = UniverseRegistry(data_dir, clock=clock)
            snap = _snapshot()
            ledger, world = self._world(data_dir, save_path, snap)
            registry.observe_live(world, snap, save_path, ledger_state=ledger.state)
            os.remove(save_path)
            for _ in range(MISSING_CHECKS_BEFORE_PRESERVE + 1):
                registry.observe_missing(save_path)
                clock.advance(MISSING_SECONDS_BEFORE_PRESERVE)
            self.assertEqual("preserved_read_only", registry.get(world["world_id"])["state"])
            newer = _snapshot(day=30, content_hash="h-newer")
            Path(save_path).write_bytes(b"save2")
            token = registry.observe_live(world, newer, save_path, ledger_state=ledger.state)
            self.assertFalse(token["live"])
            self.assertEqual("relink_pending", token["state"])
            with self.assertRaises(WorldReadOnlyError):
                registry.require_live_binding(token)
            preview = registry.relink_preview(world["world_id"], newer, save_path)
            self.assertTrue(preview["identity_match"])
            self.assertEqual("newer", preview["chronology"])
            self.assertTrue(preview["can_relink"])
            other = _snapshot(player_id=999, day=30, content_hash="x")
            self.assertFalse(registry.relink_preview(world["world_id"], other, save_path)["can_relink"])
            relinked = registry.relink_confirm(world["world_id"], newer, save_path, note="restored from backup", ledger_state=ledger.state)
            self.assertEqual("live_relinked", relinked["state"])
            registry.require_live_binding(relinked)
            kinds = [row["kind"] for row in registry.status_events(world["world_id"])]
            self.assertIn("preserved", kinds)
            self.assertIn("relink_pending", kinds)
            self.assertIn("relinked", kinds)
            self.assertEqual(2, len(registry.bindings(world["world_id"])))
            self.assertEqual(1, sum(1 for row in registry.bindings(world["world_id"]) if row["active"]))

    def test_older_restore_branches_and_preserves_the_later_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            registry = UniverseRegistry(data_dir)
            store = WorldStore(data_dir)
            later = _snapshot(day=30, content_hash="later")
            ledger, world = store.resolve(later, save_path)
            ledger.commit(ledger.classify(later))
            registry.observe_live(world, later, save_path, ledger_state=ledger.state)
            older = _snapshot(day=10, content_hash="older")
            branch_ledger, branch = store.resolve(older, save_path)
            self.assertNotEqual(world["world_id"], branch["world_id"])
            self.assertEqual(2, branch["generation"])
            branch_ledger.commit(branch_ledger.classify(older))
            token = registry.observe_live(branch, older, save_path, ledger_state=branch_ledger.state)
            self.assertTrue(token["live"])
            source = registry.get(world["world_id"])
            self.assertEqual("preserved_read_only", source["state"])
            self.assertEqual("branched_from_older_save", source["preservation_reason"])
            self.assertEqual(world["world_id"], registry.get(branch["world_id"])["branched_from"])
            # The later timeline keeps its own ledger untouched.
            self.assertEqual("later", Ledger(str(data_dir / "worlds" / world["world_id"] / "ledger.json"), read_only=True).state["last_snapshot"]["content_hash"])

    def test_binding_change_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_path = str(Path(temp) / "StarPlayer.dat")
            Path(save_path).write_bytes(b"save")
            registry = UniverseRegistry(data_dir)
            snap = _snapshot()
            ledger, world = self._world(data_dir, save_path, snap)
            token = registry.observe_live(world, snap, save_path, ledger_state=ledger.state)
            with self.assertRaises(WorldBindingChangedError):
                registry.require_live_binding(token, generation=7)
            with self.assertRaises(WorldBindingChangedError):
                registry.require_live_binding(token, save_fingerprint=save_fingerprint(str(Path(temp) / "other.dat")))

    def test_summary_is_frozen_from_ledger_state(self):
        state = Ledger.__new__(Ledger)  # noqa: F841 - only the helper is exercised
        summary = summarize_ledger(
            {
                "career_profile": {"seasons": [{"season_year": 2026}], "honors": [{"title": "시즌 MVP"}]},
                "milestone_ledger": [{"label": "시즌 300탈삼진"}],
                "daily_archive": [{"article_count": 3, "board_count": 2}],
                "story_sessions": {"2027-07-24": {"turns": [{"scene": {"title": "미디어 · 경기 뒤 인터뷰"}}]}},
                "history": [{"game_lines": ["등판: 9이닝"]}],
                "last_snapshot": {"date": {"year": 2027, "month": 7, "day": 24, "career_year": 2}, "content_hash": "h", "player": {"name": "P"}},
            }
        )
        self.assertEqual(["시즌 MVP", "시즌 300탈삼진"], summary["highlights"])
        self.assertEqual("미디어 · 경기 뒤 인터뷰", summary["headline"])
        self.assertEqual("2027-07-24", summary["last_verified_date"])
        self.assertEqual(3, summary["articles"])


class MuseumModeServiceTests(unittest.TestCase):
    """Delete the save, keep every record readable, block every mutation."""

    def _config(self, temp):
        return {
            "output_dir": str(Path(temp) / "output"),
            "shots_dir": str(Path(temp) / "shots"),
            "data_dir": str(Path(temp) / "data"),
            "ledger_path": None,
            "save_path": str(Path(temp) / "StarPlayer.dat"),
            "heat": 7,
            "mode": "standard",
            "platforms": ["dc"],
            "persona": "",
            "auto_narrate": False,
            "auto_capture": False,
            "theme": "dark",
            "llm_launcher": None,
        }

    def test_deleted_save_keeps_museum_reads_and_blocks_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._config(temp)
            Path(config["save_path"]).write_bytes(b"save")
            snap = _snapshot()
            clock = FakeClock()
            app = service_module.StarModeService()
            story_calls = []
            with patch.object(service_module.config_module, "load_with_diagnostics", return_value=(config, {})), patch.object(
                service_module.config_module, "load", return_value=config
            ), patch.object(service_module.save_reader, "read_snapshot", return_value=snap), patch.object(
                service_module, "_llm_reachable", return_value=False
            ), patch.object(service_module, "UniverseRegistry", lambda data_dir: UniverseRegistry(data_dir, clock=clock)), patch.object(
                service_module.narration_module, "story_chat", side_effect=lambda *a, **k: story_calls.append(1) or ("x", "m")
            ), patch.object(service_module.config_module, "autodetect_saves", return_value=[]):
                dashboard = app.check_save(lambda *_args: None)
                world_id = dashboard["world"]["world_id"]
                self.assertTrue(dashboard["world"]["binding"]["live"])
                app.create_story_event(
                    {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
                )
                ledger_path = Path(config["data_dir"]) / "worlds" / world_id / "ledger.json"
                before_bytes = ledger_path.read_bytes()

                # The save disappears. Nothing is deleted; reads keep working.
                os.remove(config["save_path"])
                empty = app.bootstrap()
                self.assertTrue(empty["empty"])
                self.assertEqual(1, len(empty["universes"]["universes"]))
                self.assertEqual("temporarily_missing", empty["universes"]["universes"][0]["state"])
                for _ in range(MISSING_CHECKS_BEFORE_PRESERVE + 1):
                    clock.advance(MISSING_SECONDS_BEFORE_PRESERVE)
                    app.bootstrap()
                card = app.read_universe(world_id)
                self.assertEqual("preserved_read_only", card["state"])
                self.assertEqual("save_missing", card["preservation_reason"])
                self.assertTrue(card["capabilities"]["read_only"])
                self.assertIsNotNone(card["read_only_notice"])
                self.assertEqual(1, card["counts"]["story_turns"])

                history = app.read_universe_history(world_id)
                self.assertTrue(history["read_only"])
                self.assertEqual(["2027-07-24"], [row["game_date"] for row in history["history"]])
                capsule = app.read_universe_capsule(world_id, "2027-07-24")
                self.assertEqual(1, len(capsule["story"]["turns"]))
                self.assertEqual(1, len(capsule["reaction_archives"]))
                self.assertFalse(capsule["editable"])
                archive_id = capsule["reaction_archive_index"][0]["id"]
                self.assertIn("feed", app.read_universe_reaction(world_id, archive_id))
                library = app.list_universes()
                self.assertEqual(1, library["preserved_count"])
                self.assertTrue(library["universes"][0]["capabilities"]["can_view"])
                self.assertEqual(before_bytes, ledger_path.read_bytes())

                # Mutations fail on the missing save exactly as before.
                with self.assertRaises(FileNotFoundError):
                    app.create_story_event({"category": "media", "situation": "postgame_interview"})
                self.assertEqual(before_bytes, ledger_path.read_bytes())

                # A newer save returns: reads work, mutations are refused
                # before any ledger, output, or model side effect.
                newer = _snapshot(day=30, content_hash="newer", stats={"pit_IP": 30, "pit_TBF": 100, "pit_H": 5, "pit_K": 60, "pit_W": 3})
                with patch.object(service_module.save_reader, "read_snapshot", return_value=newer):
                    Path(config["save_path"]).write_bytes(b"save-newer")
                    dash = app.bootstrap()
                    self.assertEqual("relink_pending", dash["world"]["binding"]["state"])
                    output_before = sorted(str(path) for path in Path(config["output_dir"]).rglob("*"))
                    for call in (
                        lambda: app.create_story_event({"category": "media", "situation": "postgame_interview"}),
                        lambda: app.chat_story({"category": "media", "situation": "postgame_interview", "user_text": "말"}),
                        lambda: app.upsert_day_context({"standings": []}),
                        lambda: app.upsert_career_honor({"title": "x", "season_year": 2026, "kind": "other"}),
                        lambda: app.check_save(lambda *_args: None),
                        lambda: app.assert_mutable(),
                    ):
                        with self.assertRaises(WorldReadOnlyError):
                            call()
                    self.assertEqual([], story_calls)
                    self.assertEqual(before_bytes, ledger_path.read_bytes())
                    self.assertEqual(output_before, sorted(str(path) for path in Path(config["output_dir"]).rglob("*")))
                    preview = app.relink_preview(world_id)
                    self.assertTrue(preview["can_relink"], preview)
                    self.assertEqual("newer", preview["chronology"])
                    result = app.relink_confirm(world_id, {"note": "restored"})
                    self.assertEqual("live_relinked", result["binding"]["state"])
                    resumed = app.create_story_event(
                        {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
                    )
                    self.assertEqual(1, resumed["item"]["sequence"])
                    self.assertEqual("2027-07-30", resumed["item"]["game_date"])
                    self.assertNotEqual(before_bytes, ledger_path.read_bytes())
                    export = app.export_universe(world_id)
                    self.assertTrue(Path(export["path"]).is_file())

    def test_switching_saves_never_contaminates_universes(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._config(temp)
            save_a = str(Path(temp) / "a" / "00" / "StarPlayer.dat")
            save_b = str(Path(temp) / "b" / "00" / "StarPlayer.dat")
            for path in (save_a, save_b):
                Path(path).parent.mkdir(parents=True)
                Path(path).write_bytes(b"save")
            snap_a = _snapshot(player_id=1, name="Player A", content_hash="a")
            snap_b = _snapshot(player_id=2, name="Player B", content_hash="b")
            app = service_module.StarModeService()
            current = {"config": dict(config, save_path=save_a), "snap": snap_a}
            with patch.object(service_module.config_module, "load_with_diagnostics", side_effect=lambda: (current["config"], {})), patch.object(
                service_module.config_module, "load", side_effect=lambda: current["config"]
            ), patch.object(service_module.save_reader, "read_snapshot", side_effect=lambda _path: copy.deepcopy(current["snap"])), patch.object(
                service_module, "_llm_reachable", return_value=False
            ), patch.object(service_module.config_module, "autodetect_saves", return_value=[]):
                dash_a = app.check_save(lambda *_args: None)
                app.create_story_event({"category": "fans", "situation": "fan_message", "target": "fans", "visibility": "public", "user_text": "A만의 메시지"})
                current["config"] = dict(config, save_path=save_b)
                current["snap"] = snap_b
                dash_b = app.check_save(lambda *_args: None)
                self.assertNotEqual(dash_a["world"]["world_id"], dash_b["world"]["world_id"])
                self.assertEqual(0, len(dash_b["story"]["current"]["turns"]))
                self.assertEqual([], dash_b["story"]["history"])
                ledger_b = Path(config["data_dir"]) / "worlds" / dash_b["world"]["world_id"] / "ledger.json"
                self.assertNotIn("A만의 메시지", ledger_b.read_text(encoding="utf-8"))
                library = app.list_universes()
                self.assertEqual(2, len(library["universes"]))
                self.assertEqual(2, library["live_count"])
                self.assertNotEqual(dash_a["world"]["binding"]["universe_id"], dash_b["world"]["binding"]["universe_id"])


class FakeMuseumService:
    def __init__(self):
        self.read_only = False
        self.mutations = 0

    def bootstrap(self):
        return {"schema_version": 1, "app": {"offline": True}}

    def assert_mutable(self):
        if self.read_only:
            raise WorldReadOnlyError("museum")

    def check_save(self, update, options=None):
        self.mutations += 1
        update("done", 100, "complete")
        return {"checked": True}

    def create_story_event(self, payload):
        if self.read_only:
            raise WorldReadOnlyError("museum")
        self.mutations += 1
        return {"item": payload, "dashboard": {}}

    def list_universes(self):
        return {"universes": [{"universe_id": "u1", "capabilities": {"can_view": True}}], "preserved_count": 1, "live_count": 0}

    def read_universe(self, universe_id):
        if universe_id != "u1":
            raise FileNotFoundError("missing")
        return {"universe_id": universe_id, "state": "preserved_read_only"}

    def read_universe_history(self, universe_id):
        return {"universe_id": universe_id, "history": [{"game_date": "2027-07-24"}]}

    def read_universe_capsule(self, universe_id, game_date):
        return {"universe_id": universe_id, "game_date": game_date, "read_only": True}

    def read_universe_reaction(self, universe_id, archive_id):
        return {"id": archive_id, "feed": {}}

    def relink_preview(self, universe_id):
        return {"universe_id": universe_id, "can_relink": True}

    def relink_confirm(self, universe_id, payload):
        self.mutations += 1
        return {"binding": {"universe_id": universe_id, "state": "live_relinked", "note": payload.get("note")}}

    def export_universe(self, universe_id):
        return {"path": "x", "universe_id": universe_id}


class UniverseApiTests(unittest.TestCase):
    def setUp(self):
        self.server = LocalAppServer()
        self.service = FakeMuseumService()
        self.server.service = self.service
        parsed = urlparse(self.server.start())
        self.host, self.port = parsed.hostname, parsed.port

    def tearDown(self):
        self.server.stop()

    def request(self, method, path, body=None, token=True):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-StarMode-Token"] = self.server.csrf_token
        connection.request(method, path, body=json.dumps(body or {}), headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload

    def test_read_paths_and_capabilities(self):
        status, payload = self.request("GET", "/api/v1/universes")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["preserved_count"])
        self.assertEqual(200, self.request("GET", "/api/v1/universes/u1")[0])
        self.assertEqual(404, self.request("GET", "/api/v1/universes/nope")[0])
        status, payload = self.request("GET", "/api/v1/universes/u1/history")
        self.assertEqual(200, status)
        self.assertEqual("2027-07-24", payload["history"][0]["game_date"])
        status, payload = self.request("GET", "/api/v1/universes/u1/history/2027-07-24")
        self.assertTrue(payload["capsule"]["read_only"])
        status, payload = self.request("GET", "/api/v1/universes/u1/reactions/arc")
        self.assertEqual("arc", payload["archive"]["id"])

    def test_relink_and_export_require_token(self):
        self.assertEqual(403, self.request("POST", "/api/v1/universes/u1/relink/confirm", token=False)[0])
        status, payload = self.request("POST", "/api/v1/universes/u1/relink/preview")
        self.assertTrue(payload["preview"]["can_relink"])
        status, payload = self.request("POST", "/api/v1/universes/u1/relink/confirm", {"note": "ok"})
        self.assertEqual(200, status)
        self.assertEqual("live_relinked", payload["binding"]["state"])
        self.assertEqual(200, self.request("POST", "/api/v1/universes/u1/export")[0])

    def test_read_only_world_rejects_jobs_and_story_before_side_effects(self):
        self.service.read_only = True
        status, payload = self.request("POST", "/api/v1/jobs/check")
        self.assertEqual(409, status)
        self.assertEqual("WORLD_READ_ONLY", payload["error"]["code"])
        status, payload = self.request("POST", "/api/v1/story/event", {"category": "media"})
        self.assertEqual(409, status)
        self.assertEqual("WORLD_READ_ONLY", payload["error"]["code"])
        self.assertEqual(0, self.service.mutations)
        self.assertIsNone(self.server.jobs.latest())
        self.service.read_only = False
        status, _payload = self.request("POST", "/api/v1/jobs/check")
        self.assertEqual(202, status)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.server.jobs.latest()["status"] in ("queued", "running"):
            time.sleep(0.02)
        self.assertEqual(1, self.service.mutations)


if __name__ == "__main__":
    unittest.main()
