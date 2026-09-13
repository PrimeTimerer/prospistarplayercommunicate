#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Permanent regressions for the 2026-09-02 external review findings.

Derived from the review's isolated reproductions (`artifacts/codex-review-20260902`):
P1 #2 a cancelled narrative worker returning late must not overwrite a newer
job's output, and P2 #3 a story response must never be recorded under a
different save, player, world generation, or game date than its prompt.
All state lives in temporary directories; the model is a fixture.
"""

from __future__ import annotations

import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from job_manager import JobCancelled, JobManager
from ledger_v2 import Ledger
import service as service_module
from tests.test_story_service import snapshot


def _fixture(root: Path, world_id: str, player_id: int):
    snap = copy.deepcopy(snapshot())
    snap["player"]["id"] = player_id
    snap["player"]["name"] = f"Review Player {player_id}"
    ledger = Ledger(str(root / f"{world_id}.json"), world_id)
    ledger.commit(ledger.classify(snap))
    config = {
        "output_dir": str(root / "output"),
        "shots_dir": str(root / "shots"),
        "save_path": str(root / f"{world_id}.dat"),
        "heat": 7,
        "mode": "standard",
        "platforms": ["dc"],
        "persona": "",
        "auto_narrate": False,
        "auto_capture": False,
        "theme": "dark",
    }
    world = {"world_id": world_id, "generation": 1, "slot": "00"}
    return config, {}, None, ledger, world, ledger.classify(snap)


def _wait(manager: JobManager, job_id: str, *statuses: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


class CancelledWorkerCommitGateTests(unittest.TestCase):
    def test_cancelled_narrative_worker_cannot_overwrite_newer_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = _fixture(root, "world-one", 7)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: prepared
            app._ensure_local_llm = lambda *args: True
            app._spotlight = lambda *args: {}
            app._dashboard = lambda *args, **kwargs: {"narrative": kwargs.get("narrative")}
            first_entered = threading.Event()
            release_first = threading.Event()
            first_outcome: dict = {}
            calls = {"n": 0}
            lock = threading.Lock()

            def fake_model(*_args, **_kwargs):
                with lock:
                    calls["n"] += 1
                    number = calls["n"]
                if number == 1:
                    first_entered.set()
                    self.assertTrue(release_first.wait(5))
                    return "CANCELLED_OLD_RESULT", "fixture-model"
                return "NEW_SUCCESSFUL_RESULT", "fixture-model"

            def first_worker(update):
                try:
                    return app.generate_narrative(update)
                except JobCancelled as exc:
                    first_outcome["cancelled"] = str(exc)
                    raise
                finally:
                    first_outcome["done"] = True

            manager = JobManager()
            with patch.object(service_module.briefing, "build_briefing", return_value="fixture"), patch.object(
                service_module.narration_module, "narrate", side_effect=fake_model
            ):
                first = manager.start("narrative", first_worker)
                self.assertTrue(first_entered.wait(5))
                self.assertEqual("cancelled", manager.cancel(first["id"])["status"])
                second = manager.start("narrative", app.generate_narrative)
                _wait(manager, second["id"], "completed")
                path = Path(prepared[0]["output_dir"]) / "worlds" / "world-one" / "narrative.md"
                self.assertEqual("NEW_SUCCESSFUL_RESULT", path.read_text(encoding="utf-8"))
                release_first.set()
                deadline = time.monotonic() + 5
                while not first_outcome.get("done") and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(first_outcome.get("done"))

            # The late worker stopped at the commit gate: nothing overwritten.
            self.assertIn("cancelled", first_outcome)
            self.assertEqual("NEW_SUCCESSFUL_RESULT", path.read_text(encoding="utf-8"))
            self.assertEqual("cancelled", manager.get(first["id"])["status"])
            self.assertEqual("completed", manager.get(second["id"])["status"])

    def test_commit_gate_is_a_no_op_outside_a_job(self):
        with service_module._commit_gate(lambda *_args: None):
            pass
        with service_module._commit_gate(None):
            pass


class StoryContextOwnershipTests(unittest.TestCase):
    def test_response_is_refused_when_save_context_changes_during_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = _fixture(root, "world-a", 7)
            switched = _fixture(root, "world-b", 8)
            current = [original]
            app = service_module.StarModeService()
            app._prepare = lambda update=None: current[0]
            app._dashboard = lambda *args, **kwargs: {"world": args[3]["world_id"]}
            app._persist_current_capsule = lambda *args: {}
            seen = {}

            def fake_model(_system_text, user_text):
                seen["prompt_original"] = "Review Player 7" in user_text
                current[0] = switched  # the save selection changes mid-call
                return "MODEL_RESPONSE_FOR_WORLD_A", "fixture-model"

            with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
                service_module.narration_module, "story_chat", side_effect=fake_model
            ):
                with self.assertRaisesRegex(RuntimeError, "세이브 문맥이 바뀌어"):
                    app.chat_story({
                        "category": "career", "situation": "meet_agent", "target": "agent",
                        "visibility": "private", "user_text": "Review fixture request",
                    })
            self.assertTrue(seen["prompt_original"])
            self.assertEqual({}, original[3].state.get("story_sessions", {}))
            self.assertEqual({}, switched[3].state.get("story_sessions", {}))

    def test_same_context_still_appends(self):
        with tempfile.TemporaryDirectory() as temp:
            prepared = _fixture(Path(temp), "world-a", 7)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: prepared
            app._persist_current_capsule = lambda *args: {}
            with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
                service_module.narration_module, "story_chat", return_value=("ok", "fixture-model")
            ):
                result = app.chat_story({
                    "category": "career", "situation": "meet_agent", "target": "agent",
                    "visibility": "private", "user_text": "same context",
                })
            self.assertEqual("llm", result["item"]["source"])
            self.assertEqual(1, len(prepared[3].state["story_sessions"]))


if __name__ == "__main__":
    unittest.main()
