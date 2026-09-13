#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import career_engine
from ledger_v2 import Ledger


def snapshot(*, year=2027, month=7, day=1, career_year=2, stats=None, content_hash="one", history=None):
    return {
        "schema_version": 2,
        "player": {"id": 77, "name": "Tracked Player", "team": "TEST", "pos": "투수"},
        "date": {
            "year": year,
            "month": month,
            "day": day,
            "career_year": career_year,
        },
        "stats": stats or {},
        "profile_fingerprint": "career-profile",
        "source": {"file": "StarPlayer.dat", "slot": "00"},
        "provenance": {"season_stats": "save_verified"},
        "validation": {"container": "verified"},
        "content_hash": content_hash,
        "career_history": history or {
            "format": "test-season-summary-v1",
            "status": "verified",
            "seasons": [],
            "awards": {"status": "not_structurally_decoded", "honors": []},
        },
    }


class InningsTests(unittest.TestCase):
    def test_baseball_innings_are_added_as_outs(self):
        self.assertEqual(302, career_engine.innings_to_outs("100.2"))
        self.assertEqual("111", career_engine.outs_to_innings(302 + 31))
        with self.assertRaises(ValueError):
            career_engine.innings_to_outs("100.3")


class CareerLedgerTests(unittest.TestCase):
    def test_same_date_parser_upgrade_does_not_create_false_correction(self):
        current = snapshot(
            stats={"pit_IP": 153, "pit_TBF": 465, "pit_H": 0, "pit_K": 388, "pit_W": 17,
                   "bat_AB": 282, "bat_H": 206, "bat_HR": 110, "bat_RBI": 230,
                   "bat_R": 179, "bat_SO": 11, "bat_SB": 129},
            content_hash="season-summary",
            history={
                "format": "prospi2026-season-summary-v1",
                "status": "verified",
                "seasons": [],
                "awards": {"status": "not_structurally_decoded", "honors": []},
            },
        )
        legacy = snapshot(
            stats={"pit_IP": 153, "pit_TBF": 465, "pit_H": 4, "pit_K": 388, "pit_W": 17,
                   "bat_AB": 282, "bat_H": 206, "bat_HR": 110, "bat_RBI": 230,
                   "bat_R": 179, "bat_SO": 11, "bat_SB": 129},
            content_hash="legacy",
        )
        legacy.pop("career_history", None)
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            ledger.state["last_snapshot"] = legacy
            result = ledger.sync_snapshot_parser_baseline(current)
            self.assertTrue(result["changed"])
            self.assertEqual(["pit_H"], result["corrected_fields"])
            self.assertEqual("NO_CHANGE", ledger.classify(current)["kind"])
            self.assertEqual(1, len(ledger.state["parser_migrations"]))

    def test_parser_upgrade_does_not_swallow_real_same_date_stat_change(self):
        legacy = snapshot(stats={"bat_AB": 100, "bat_H": 30}, content_hash="legacy")
        legacy.pop("career_history", None)
        current = snapshot(stats={"bat_AB": 101, "bat_H": 31}, content_hash="new")
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            ledger.state["last_snapshot"] = legacy
            result = ledger.sync_snapshot_parser_baseline(current)
            self.assertFalse(result["changed"])
            self.assertEqual("SEASON_UPDATE", ledger.classify(current)["kind"])

    def test_verified_save_history_replaces_manual_row_without_double_counting(self):
        prior_stats = {
            "bat_G": 25, "bat_PA": 84, "bat_AB": 76, "bat_H": 26,
            "bat_1B": 10, "bat_2B": 7, "bat_3B": 0, "bat_HR": 9,
            "bat_RBI": 22, "bat_R": 17, "bat_SO": 12, "bat_BB": 6,
            "bat_HBP": 2, "bat_SB": 5,
            "pit_G": 25, "pit_IP": 227, "pit_TBF": 683, "pit_H": 3,
            "pit_K": 361, "pit_W": 22, "pit_L": 2, "pit_CG": 23,
            "pit_SHO": 20, "pit_BB": 2, "pit_R": 4, "pit_ER": 3,
        }
        current_stats = {
            "bat_G": 17, "bat_PA": 314, "bat_AB": 282, "bat_H": 206,
            "bat_1B": 76, "bat_2B": 16, "bat_3B": 4, "bat_HR": 110,
            "bat_RBI": 230, "bat_R": 179, "bat_SO": 11, "bat_SF": 1,
            "bat_BB": 27, "bat_HBP": 4, "bat_SB": 129,
            "pit_G": 17, "pit_IP": 153, "pit_TBF": 465, "pit_H": 0,
            "pit_K": 388, "pit_W": 17, "pit_CG": 17, "pit_SHO": 16,
            "pit_R": 1, "pit_ER": 1,
        }
        snap = snapshot(
            stats=current_stats,
            history={
                "format": "test-season-summary-v1",
                "status": "verified",
                "seasons": [{
                    "season_year": 2026,
                    "career_year": 1,
                    "stats": prior_stats,
                    "provenance": "save_verified_historical_season",
                }],
                "awards": {"status": "not_structurally_decoded", "honors": []},
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            ledger.upsert_career_season(
                {"season_year": 2026, "stats": {"bat_H": 1}, "note": "screen note"},
                snap,
            )
            first = ledger.sync_save_history(snap)
            second = ledger.sync_save_history(snap)
            view = ledger.career_view(snap)

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual([2026], first["manual_replaced_years"])
            self.assertEqual(1, len(view["seasons"]))
            self.assertEqual("save_verified_historical_season", view["seasons"][0]["provenance"])
            self.assertEqual("screen note", view["seasons"][0]["note"])
            self.assertEqual(232, view["totals"]["bat_H"])
            self.assertEqual(119, view["totals"]["bat_HR"])
            self.assertEqual(749, view["totals"]["pit_K"])
            self.assertEqual("380", view["totals"]["pit_IP"])
            self.assertEqual("S+", view["player_grade"]["code"])
            self.assertFalse(view["tracking"]["history_incomplete"])
            self.assertEqual(1, view["tracking"]["previous_seasons_save_verified"])
            first_ids = {row["id"] for row in view["timeline"] if row["id"].startswith("career-first:")}
            self.assertEqual(7, len(first_ids))
            first_rows = [row for row in view["timeline"] if row["id"] in first_ids]
            self.assertTrue(all(row["season_year"] == 2026 for row in first_rows))
            self.assertTrue(all(row["date_precision"] == "season_year" for row in first_rows))

    def test_confirmed_awards_can_raise_historic_profile_to_ex(self):
        snap = snapshot(
            stats={
                "bat_G": 17, "bat_PA": 314, "bat_AB": 282, "bat_H": 206,
                "bat_1B": 76, "bat_2B": 16, "bat_3B": 4, "bat_HR": 110,
                "bat_RBI": 230, "bat_R": 179, "bat_SO": 11, "bat_SF": 1,
                "bat_BB": 27, "bat_HBP": 4, "bat_SB": 129,
                "pit_G": 17, "pit_IP": 153, "pit_TBF": 465, "pit_K": 388,
                "pit_W": 17, "pit_CG": 17, "pit_SHO": 16, "pit_R": 1, "pit_ER": 1,
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            for kind, count in (("season_mvp", 1), ("monthly_mvp", 4), ("all_star_selection", 1)):
                ledger.upsert_career_honor(
                    {"kind": kind, "season_year": 2026, "count": count},
                    snap,
                )
            grade = ledger.career_view(snap)["player_grade"]
            self.assertEqual("EX", grade["code"])
            self.assertEqual(15, grade["components"]["confirmed_honors"])

    def test_first_milestones_are_created_for_first_year_save(self):
        snap = snapshot(
            year=2026,
            career_year=1,
            stats={"bat_G": 1, "bat_PA": 1, "bat_AB": 1, "bat_H": 1, "bat_1B": 1},
        )
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            result = ledger.sync_save_history(snap)
            ids = {row["id"] for row in ledger.state["milestone_ledger"]}
            self.assertFalse(result["changed"])
            self.assertIn("career-first:appearance", ids)
            self.assertIn("career-first:bat_H", ids)

    def test_mid_tracking_previous_season_is_combined_without_current_double_count(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            current = snapshot(
                stats={"bat_AB": 100, "bat_H": 40, "bat_HR": 15, "pit_IP": 10, "pit_K": 30}
            )
            ledger.upsert_career_season(
                {
                    "season_year": 2026,
                    "team": "OLD TEAM",
                    "stats": {
                        "bat_AB": 500,
                        "bat_H": 200,
                        "bat_HR": 55,
                        "pit_IP": "100.2",
                        "pit_K": 250,
                    },
                },
                current,
            )
            view = ledger.career_view(current)
            self.assertEqual(600, view["totals"]["bat_AB"])
            self.assertEqual(70, view["totals"]["bat_HR"])
            self.assertEqual(280, view["totals"]["pit_K"])
            self.assertEqual("110.2", view["totals"]["pit_IP"])
            self.assertFalse(view["tracking"]["history_incomplete"])
            self.assertEqual("manual_confirmed", view["seasons"][0]["provenance"])

    def test_current_season_cannot_be_manually_added(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            with self.assertRaises(ValueError):
                ledger.upsert_career_season(
                    {"season_year": 2027, "stats": {"bat_H": 1}},
                    snapshot(),
                )

    def test_career_and_npb_record_crossings_are_persisted_with_observed_date(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            first = snapshot(
                day=1,
                stats={"bat_AB": 300, "bat_H": 49, "bat_HR": 59},
                content_hash="first",
            )
            ledger.upsert_career_season(
                {"season_year": 2026, "stats": {"bat_AB": 3000, "bat_H": 950}},
                first,
            )
            ledger.commit(ledger.classify(first))
            second = snapshot(
                day=2,
                stats={"bat_AB": 304, "bat_H": 50, "bat_HR": 61},
                content_hash="second",
            )
            event = ledger.classify(second)
            labels = [row["label"] for row in event["career_milestones"]]
            self.assertTrue(any("통산 1,000 안타" in label for label in labels))
            self.assertTrue(any("NPB 시즌 홈런 기록" in label for label in labels))
            ledger.commit(event)
            entries = ledger.state["milestone_ledger"]
            tracked = [row for row in entries if row.get("provenance") == "derived_save_delta"]
            self.assertTrue(tracked)
            self.assertTrue(all(row.get("occurred_on") == "2027-07-02" for row in tracked))

    def test_rollover_archives_verified_completed_season_once(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            old = snapshot(
                year=2027,
                month=10,
                day=20,
                career_year=2,
                stats={"bat_AB": 500, "bat_H": 200, "bat_HR": 60, "pit_IP": 180, "pit_K": 250},
                content_hash="old",
            )
            ledger.commit(ledger.classify(old))
            new = snapshot(
                year=2028,
                month=3,
                day=2,
                career_year=3,
                stats={"bat_AB": 2, "bat_H": 1, "pit_IP": 0, "pit_K": 0},
                content_hash="new",
            )
            event = ledger.classify(new)
            self.assertEqual("SEASON_ROLLOVER", event["kind"])
            ledger.commit(event)
            seasons = ledger.state["career_profile"]["seasons"]
            self.assertEqual(1, len(seasons))
            self.assertEqual("save_verified_completed_season", seasons[0]["provenance"])
            self.assertEqual(2027, seasons[0]["season_year"])
            self.assertEqual(201, ledger.career_view(new)["totals"]["bat_H"])

    def test_honors_keep_exact_or_season_only_dates_without_invention(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "world")
            exact = ledger.upsert_career_honor(
                {
                    "kind": "monthly_mvp",
                    "occurred_on": "2027-07-15",
                    "title": "7월 월간 MVP",
                    "count": 1,
                },
                snapshot(),
            )
            season_only = ledger.upsert_career_honor(
                {
                    "kind": "all_star_selection",
                    "season_year": 2026,
                    "title": "올스타 선정",
                    "count": 2,
                },
                snapshot(),
            )
            series_mvp = ledger.upsert_career_honor(
                {
                    "kind": "japan_series_mvp",
                    "season_year": 2026,
                    "title": "일본시리즈 MVP",
                    "count": 1,
                },
                snapshot(),
            )
            self.assertEqual("2027-07-15", exact["occurred_on"])
            self.assertIsNone(season_only["occurred_on"])
            self.assertEqual("japan_series_mvp", series_mvp["kind"])
            summary = {row["kind"]: row["count"] for row in ledger.career_view(snapshot())["honor_summary"]}
            self.assertEqual(2, summary["all_star_selection"])
            self.assertEqual(1, summary["japan_series_mvp"])


class NpbCatalogTests(unittest.TestCase):
    def test_official_reference_catalog_contains_verified_headline_records(self):
        records = {row["id"]: row for row in career_engine.catalog()["records"]}
        self.assertEqual(60, records["season-bat-hr"]["value"])
        self.assertEqual(868, records["career-bat-hr"]["value"])
        self.assertEqual(401, records["season-pit-k"]["value"])
        self.assertEqual(4490, records["career-pit-k"]["value"])
        self.assertEqual("5526.2", records["career-pit-ip"]["display_value"])


if __name__ == "__main__":
    unittest.main()
