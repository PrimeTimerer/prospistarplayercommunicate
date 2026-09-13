#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service as service_module
import stat_engine
from ledger_v2 import Ledger


class FeedRecoveryTests(unittest.TestCase):
    def test_no_change_rebuilds_missing_feed_once_without_repeating_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = {
                "player": {"id": 7, "name": "Test Player", "team": "TEST", "pos": "투수"},
                "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
                "stats": {"pit_IP": 20, "pit_K": 40, "pit_H": 3, "pit_W": 2},
                "content_hash": "verified",
                "profile_fingerprint": "profile",
                "validation": {},
                "provenance": {},
            }
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world-one")
            ledger.commit(ledger.classify(snapshot))
            event = ledger.classify(snapshot)
            self.assertEqual("NO_CHANGE", event["kind"])
            commits = []
            real_commit = ledger.commit
            ledger.commit = lambda value: (commits.append(value), real_commit(value))
            config = {
                "output_dir": temp,
                "shots_dir": str(Path(temp) / "shots"),
                "heat": 7,
                "mode": "standard",
                "platforms": ["dc"],
                "persona": "",
                "auto_capture": False,
            }
            world = {"world_id": "world-one", "generation": 1}
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            app._dashboard = lambda _config, _diagnostics, _ledger, _world, _event, **kwargs: {
                "feed": kwargs.get("feed"),
                "run": kwargs.get("run"),
            }

            with patch.object(service_module.briefing, "build_briefing", return_value="brief"):
                first = app.check_save(lambda *_args: None)
                second = app.check_save(lambda *_args: None)

            feed_path = Path(temp) / "worlds" / "world-one" / "feed.json"
            self.assertTrue(feed_path.is_file())
            self.assertTrue(first["run"]["regenerated"])
            self.assertEqual("기존 기록으로 피드 복원", first["run"]["message"])
            self.assertFalse(second["run"]["regenerated"])
            self.assertEqual("변화 없음", second["run"]["message"])
            self.assertEqual(1, len(commits))
            archives = ledger.state["daily_archive"]
            self.assertEqual(1, len(archives))
            archive_path = Path(temp) / "worlds" / "world-one" / archives[0]["relative_path"]
            self.assertTrue(archive_path.is_file())


if __name__ == "__main__":
    unittest.main()
