#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local attachment understanding (master plan step 11, sections 8.12 / 16.7 / 17.4).

Guarantees under test:
- known Korean game screens parse into validated, confidence-tagged observations
  from saved OCR fixtures without any OCR process, LLM, or network call;
- uploads and derived copies never keep camera metadata;
- duplicates are detected before anything is applied;
- validation failures cannot become user-confirmed facts;
- the service commits facts as ``user_confirmed`` only, refuses dates that are
  not the open date, keeps provenance when originals are deleted, and never
  reaches an LLM or the network on the local path.
"""

from __future__ import annotations

import copy
import json
import socket
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

import attachments
import service as service_module

try:
    from tests.test_narrative_engine import _snapshot
    from tests.test_universe_registry import FakeClock
except ImportError:  # pragma: no cover - depends on the discovery root
    from test_narrative_engine import _snapshot
    from test_universe_registry import FakeClock

from universe_registry import (
    MISSING_CHECKS_BEFORE_PRESERVE,
    MISSING_SECONDS_BEFORE_PRESERVE,
    UniverseRegistry,
    WorldReadOnlyError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ocr"
NAMES = ("Test Hitter", "Hitter")
TEAM = "TestHawks"


def _fixture(name: str) -> dict:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    payload.setdefault("engine_language", "ko")
    return payload


def _analyze(name: str, **overrides) -> dict:
    options = {
        "protagonist_names": NAMES,
        "protagonist_team": TEAM,
        "protagonist_id": 99,
        "universe_id": "u1",
        "current_game_date": "2030-05-12",
        "ocr": _fixture(name),
    }
    options.update(overrides)
    return attachments.analyze_local(str(FIXTURES / name), **options)


def _fields(record: dict) -> dict:
    return {row["field"]: row for row in record["observations"]}


def _png_bytes(width: int = 2560, height: int = 1600) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)


def _jpeg_with_metadata() -> bytes:
    def segment(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body

    app0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    exif = segment(0xE1, b"Exif\x00\x00" + b"II*\x00" + b"\x08\x00\x00\x00" + b"GPS-LIKE-PAYLOAD")
    comment = segment(0xFE, b"camera comment")
    sof0 = segment(0xC0, b"\x08" + struct.pack(">HH", 1600, 2560) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    sos = bytes([0xFF, 0xDA]) + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\x56" + bytes([0xFF, 0xD9])
    return bytes([0xFF, 0xD8]) + app0 + exif + comment + sof0 + sos


class ImageBytesTests(unittest.TestCase):
    def test_dimensions_are_read_from_png_and_jpeg_headers(self):
        self.assertEqual((2560, 1600), attachments.image_dimensions(_png_bytes()))
        self.assertEqual((2560, 1600), attachments.image_dimensions(_jpeg_with_metadata()))
        self.assertIsNone(attachments.image_dimensions(b"not an image"))

    def test_jpeg_metadata_segments_are_dropped_but_jfif_and_frame_survive(self):
        original = _jpeg_with_metadata()
        stripped = attachments.strip_jpeg_metadata(original)
        self.assertIn(b"GPS-LIKE-PAYLOAD", original)
        self.assertNotIn(b"GPS-LIKE-PAYLOAD", stripped)
        self.assertNotIn(b"camera comment", stripped)
        self.assertIn(b"JFIF", stripped)
        self.assertEqual((2560, 1600), attachments.image_dimensions(stripped))
        self.assertTrue(stripped.endswith(b"\xff\xd9"))
        self.assertEqual(_png_bytes(), attachments.strip_jpeg_metadata(_png_bytes()))


class ScreenParserTests(unittest.TestCase):
    def test_game_result_screen_yields_validated_result_and_home_run_count(self):
        record = _analyze("game_result_ko.json")
        self.assertEqual("game_result", record["screen_type"])
        self.assertEqual("known_screen_parser", record["analysis_path"])
        self.assertFalse(record["llm_used"])
        self.assertFalse(record["network_used"])
        fields = _fields(record)
        self.assertEqual("2030-05-12", fields["game.date"]["value"])
        self.assertEqual(27500, fields["game.attendance"]["value"])
        self.assertEqual({"wins": 6, "losses": 3, "ties": 0}, fields["game.head_to_head"]["value"])
        result = fields["game.result"]["value"]
        self.assertEqual(("win", 6, 3, "AwayBears"), (result["outcome"], result["runs_for"], result["runs_against"], result["opponent"]))
        self.assertEqual(6, sum(run for run in fields["game.linescore.home"]["value"] if isinstance(run, int)))
        self.assertEqual(2, fields["game.protagonist_home_runs"]["value"])
        self.assertTrue(fields["game.home_runs"]["value"]["protagonist"])
        self.assertTrue(all(row["validation"] == "ok" for row in record["observations"]))
        self.assertTrue(all(0.0 < row["confidence"] <= 1.0 for row in record["observations"]))
        binding = record["binding"]
        self.assertEqual(("2030-05-12", "screen", False), (binding["game_date"], binding["date_source"], binding["date_conflict"]))
        self.assertTrue(binding["protagonist_seen"])

    def test_batting_log_screen_yields_plate_appearances(self):
        record = _analyze("batting_log_ko.json")
        self.assertEqual("batting_log", record["screen_type"])
        fields = _fields(record)
        results = [row["result"] for row in fields["batting.plate_appearances"]["value"]]
        self.assertEqual(["HR", "HR", "HR", "HR"], results)
        self.assertEqual((4, 4, 4), (fields["batting.at_bats"]["value"], fields["batting.hits"]["value"], fields["batting.home_runs"]["value"]))
        self.assertEqual("warn", fields["game.linescore.visitor"]["validation"])

    def test_batting_stats_screen_yields_one_protagonist_line(self):
        record = _analyze("batting_stats_ko.json")
        self.assertEqual("batting_stats", record["screen_type"])
        lines = [row for row in record["observations"] if row["field"] == "batting.line"]
        self.assertEqual(1, len(lines))
        line = lines[0]["value"]
        self.assertTrue(line["protagonist"])
        self.assertEqual((1, 2, 1), (line["bat_HR"], line["bat_RBI"], line["bat_R"]))
        self.assertAlmostEqual(0.321, line["average"])
        table = next(row for row in record["observations"] if row["field"] == "batting.table")
        self.assertEqual("session_only", table["suggested_decision"])

    def test_unknown_screen_falls_back_to_manual_description(self):
        record = _analyze("game_result_en.json")
        self.assertEqual(("unknown", "ocr_only"), (record["screen_type"], record["analysis_path"]))
        self.assertEqual([], record["observations"])
        self.assertTrue(record["needs_user_description"])

    def test_screen_date_conflict_is_flagged_against_the_open_date(self):
        record = _analyze("game_result_ko.json", current_game_date="2030-05-13")
        self.assertTrue(record["binding"]["date_conflict"])
        self.assertTrue(record["binding"]["requires_confirmation"])

    def test_fixture_analysis_never_spawns_ocr_llm_or_network(self):
        def blocked(*_args, **_kwargs):
            raise AssertionError("local analysis must not spawn processes or open sockets")

        with patch.object(attachments.subprocess, "run", side_effect=blocked), patch.object(socket, "create_connection", side_effect=blocked):
            record = _analyze("game_result_ko.json")
        self.assertEqual("game_result", record["screen_type"])

    def test_non_image_without_injected_ocr_is_unsupported_and_still_reviewable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "note.txt"
            path.write_text("hello", encoding="utf-8")
            record = attachments.analyze_local(str(path), protagonist_names=NAMES, current_game_date="2030-05-12")
        self.assertEqual("unsupported", record["analysis_path"])
        self.assertEqual(0.0, record["screen_confidence"])
        self.assertTrue(record["needs_user_description"])


class DuplicateAndProposalTests(unittest.TestCase):
    def test_identical_and_near_identical_captures_are_flagged(self):
        first = _analyze("batting_log_ko.json")
        second = _analyze("batting_log_ko_2.json")
        clone = copy.deepcopy(first)
        clone["file_name"] = "clone.png"
        records = attachments.detect_duplicates_and_conflicts([first, second, clone])
        reasons = {row["file_name"]: sorted(dup["reason"] for dup in row["duplicates"]) for row in records}
        self.assertIn("near_identical_text", reasons["batting_log_ko_2.json"])
        self.assertIn("identical_bytes", reasons["clone.png"])

    def test_validation_failures_cannot_become_user_confirmed_facts(self):
        record = _analyze("game_result_ko.json")
        broken = next(row for row in record["observations"] if row["field"] == "game.attendance")
        broken["validation"] = "fail"
        decisions = {row["observation_id"]: "user_confirmed" for row in record["observations"]}
        proposal = attachments.propose_application(record, decisions)
        self.assertEqual([broken["observation_id"]], [row["observation_id"] for row in proposal["blocked"]])
        self.assertTrue(all(row["evidence_class"] == "user_confirmed" for row in proposal["proposals"] if row["target"] == "fact_registry"))
        self.assertNotIn("save_verified", {row["evidence_class"] for row in proposal["proposals"]})

    def test_story_prop_and_manual_description_become_fictional_material(self):
        record = _analyze("game_result_ko.json")
        result_id = next(row["observation_id"] for row in record["observations"] if row["field"] == "game.result")
        proposal = attachments.propose_application(record, {result_id: "story_prop"}, manual_description="라커룸 햄버거 내기")
        targets = {row["target"] for row in proposal["proposals"]}
        self.assertIn("narrative_prop", targets)
        self.assertTrue(any(row["value"] == "라커룸 햄버거 내기" for row in proposal["proposals"] if row["target"] == "narrative_prop"))
        self.assertTrue(all(row["evidence_class"] == "fictional_intervention" for row in proposal["proposals"] if row["target"] == "narrative_prop"))


class AttachmentServiceTests(unittest.TestCase):
    def _config(self, temp: str) -> dict:
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

    def _running(self, temp: str, *, synthetic_ocr: bool = False):
        config = self._config(temp)
        Path(config["save_path"]).write_bytes(b"save")
        Path(config["shots_dir"]).mkdir(parents=True, exist_ok=True)
        snap = _snapshot()
        if synthetic_ocr:
            snap["player"].update(name="Test Hitter", team="TestHawks")
            snap["date"].update(year=2030, month=5, day=12)
        app = service_module.StarModeService()

        def blocked(*_args, **_kwargs):
            raise AssertionError("the local attachment path must not reach an LLM or the network")

        def loopback_only(address=None, *_args, **_kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if host in ("127.0.0.1", "localhost", "::1"):
                raise OSError("loopback probe refused in tests")
            raise AssertionError(f"non-loopback connection attempted: {address!r}")

        patches = [
            patch.object(service_module.config_module, "load_with_diagnostics", return_value=(config, {})),
            patch.object(service_module.config_module, "load", return_value=config),
            patch.object(service_module.save_reader, "read_snapshot", return_value=snap),
            patch.object(service_module.config_module, "autodetect_saves", return_value=[]),
            patch.object(service_module.narration_module, "narrate", side_effect=blocked),
            patch.object(service_module.narration_module, "story_chat", side_effect=blocked),
            patch.object(service_module.narration_module, "pick_endpoint", return_value=(None, None)),
            patch.object(attachments, "_run_script", side_effect=blocked),
            patch.object(attachments, "ocr_status", return_value={"available": False, "languages": [], "engine": None, "error": "disabled in tests"}),
            patch.object(socket, "create_connection", side_effect=loopback_only),
        ]
        return app, config, snap, patches

    def _write_capture(self, config: dict, name: str = "capture-test.png") -> str:
        (Path(config["shots_dir"]) / name).write_bytes(_png_bytes())
        return name

    def test_local_analysis_commits_user_confirmed_facts_and_props_with_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, snap, patches = self._running(temp, synthetic_ocr=True)
            with _Patches(patches):
                service_module._llm_probe_state["at"] = 0.0
                app.check_save(lambda *_args: None)
                name = self._write_capture(config)
                analysis = app.analyze_attachments({"names": [name], "_ocr_fixture": _fixture("game_result_ko.json")})
                record = analysis["attachments"][0]
                self.assertEqual("game_result", record["screen_type"])
                self.assertEqual("analyzed", record["status"])
                self.assertFalse(analysis["status"]["llm_vision_reachable"])
                proposal = app.propose_attachment({"attachment_id": record["attachment_id"], "decisions": {}})
                self.assertGreater(proposal["summary"]["facts"], 0)
                wanted = {"game.result", "game.protagonist_home_runs"}
                decisions = {row["observation_id"]: ("user_confirmed" if row["field"] in wanted else "ignore") for row in record["observations"]}
                result = app.commit_attachment({
                    "attachment_id": record["attachment_id"],
                    "decisions": decisions,
                    "manual_description": "라커룸 햄버거 내기",
                    "retention": "analysis_only",
                })
                self.assertEqual("committed", result["attachment"]["status"])
                self.assertEqual("private", result["attachment"]["reaction_scope"])
                self.assertEqual("private", result["reaction"]["scope"])
                self.assertIsNone(result["reaction"]["event_id"])
                self.assertEqual(0, result["reaction"]["counts"]["social_posts"])
                self.assertEqual(2, len(result["facts"]))
                self.assertTrue(all(fact["evidence_class"] == "user_confirmed" for fact in result["facts"]))
                self.assertEqual(1, len(result["props"]))
                self.assertFalse((Path(config["shots_dir"]) / name).exists())
                self.assertIsNotNone(result["attachment"]["original_deleted_at"])
                dashboard = result["dashboard"]
                self.assertIn("story", dashboard)
                _config, _diagnostics, _store, ledger, _world, _event = app._prepare()
                facts = [row for row in ledger.state["fact_registry"].values() if str(row.get("source") or "").startswith("attachment:")]
                self.assertEqual(2, len(facts))
                self.assertTrue(all(row["evidence_class"] == "user_confirmed" for row in facts))
                events = [row for row in ledger.state["world_events"] if row.get("event_type") == "SP.GAME.POST.WIN"]
                self.assertEqual(1, len(events))
                self.assertEqual("user_confirmed", events[0]["evidence_class"])
                links = ledger.state["attachment_fact_links"]
                self.assertEqual(3, len(links))
                self.assertEqual(1, len([row for row in links if row["prop_id"]]))
                props = [row for row in ledger.state["narrative_props"].values() if row.get("origin_attachment_id") == record["attachment_id"]]
                self.assertEqual(1, len(props))
                stored = ledger.state["attachment_records"][record["attachment_id"]]
                self.assertEqual("analysis_only", stored["retention"])
                self.assertEqual("committed", stored["status"])
                with self.assertRaises(ValueError):
                    app.commit_attachment({"attachment_id": record["attachment_id"], "decisions": decisions})
                with self.assertRaises(ValueError):
                    app.discard_attachment({"attachment_id": record["attachment_id"]})

    def test_explicit_public_image_scene_creates_articles_boards_and_social_without_publishing_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, _snap, patches = self._running(temp, synthetic_ocr=True)
            config["heat"] = 10
            with _Patches(patches):
                service_module._llm_probe_state["at"] = 0.0
                app.check_save(lambda *_args: None)
                name = self._write_capture(config, "public-scene.png")
                record = app.analyze_attachments({"name": name, "_ocr_fixture": _fixture("game_result_ko.json")})["attachments"][0]
                decisions = {
                    row["observation_id"]: ("user_confirmed" if row["field"] == "game.result" else "ignore")
                    for row in record["observations"]
                }
                result = app.commit_attachment(
                    {
                        "attachment_id": record["attachment_id"],
                        "decisions": decisions,
                        "manual_description": "경기 뒤 관중석을 향해 모자를 들어 인사했다",
                        "retention": "keep_original",
                        "reaction_scope": "public",
                    }
                )
                counts = result["reaction"]["counts"]
                self.assertGreaterEqual(counts["articles"], 1)
                self.assertGreaterEqual(counts["boards"], 1)
                self.assertGreaterEqual(counts["social_posts"], 2)
                self.assertEqual("public", result["attachment"]["reaction_scope"])
                self.assertTrue(result["reaction"]["event_id"])
                feed = result["dashboard"]["feed"]
                self.assertGreaterEqual(len(feed["media"]), counts["articles"])
                self.assertGreaterEqual(len(feed["boards"]), counts["boards"])
                self.assertGreaterEqual(len(feed["social"]), counts["social_posts"])
                public_text = json.dumps(
                    {"media": feed["media"][: counts["articles"]], "boards": feed["boards"][: counts["boards"]], "social": feed["social"][: counts["social_posts"]]},
                    ensure_ascii=False,
                )
                self.assertIn("모자를 들어 인사", public_text)
                self.assertNotIn(name, public_text)
                self.assertNotIn(record["sha256"], public_text)
                _config, _diagnostics, _store, ledger, _world, _event = app._prepare()
                attachment_events = [
                    row for row in ledger.state["world_events"]
                    if row.get("event_type") == "SP.MEDIA.FEATURE" and (row.get("payload") or {}).get("attachment_id") == record["attachment_id"]
                ]
                self.assertEqual(1, len(attachment_events))
                self.assertFalse(attachment_events[0]["payload"]["raw_image_published"])

    def test_public_image_reaction_requires_a_durable_review_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, _snap, patches = self._running(temp, synthetic_ocr=True)
            with _Patches(patches):
                app.check_save(lambda *_args: None)
                name = self._write_capture(config, "empty-public.png")
                record = app.analyze_attachments({"name": name, "_ocr_fixture": _fixture("game_result_ko.json")})["attachments"][0]
                with self.assertRaisesRegex(ValueError, "확인 항목"):
                    app.commit_attachment(
                        {
                            "attachment_id": record["attachment_id"],
                            "decisions": {row["observation_id"]: "ignore" for row in record["observations"]},
                            "reaction_scope": "public",
                        }
                    )

    def test_commit_refuses_screens_from_another_date_and_unconfirmed_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, snap, patches = self._running(temp, synthetic_ocr=True)
            with _Patches(patches):
                service_module._llm_probe_state["at"] = 0.0
                app.check_save(lambda *_args: None)
                name = self._write_capture(config, "older.png")
                fixture = _fixture("game_result_ko.json")
                for line in fixture["lines"]:
                    if "5월 12일" in line["text"]:
                        line["text"] = line["text"].replace("5월 12일", "5월 10일")
                        for word in line.get("words", []):
                            word["text"] = word["text"].replace("12일", "10일")
                record = app.analyze_attachments({"names": [name], "_ocr_fixture": fixture})["attachments"][0]
                self.assertEqual("2030-05-10", record["binding"]["game_date"])
                self.assertTrue(record["binding"]["date_conflict"])
                decisions = {row["observation_id"]: "user_confirmed" for row in record["observations"] if row["field"] == "game.result"}
                with self.assertRaises(ValueError) as caught:
                    app.commit_attachment({"attachment_id": record["attachment_id"], "decisions": decisions, "binding_confirmed": True})
                self.assertIn("날짜", str(caught.exception))
                self.assertTrue((Path(config["shots_dir"]) / name).exists())
                discarded = app.discard_attachment({"attachment_id": record["attachment_id"]})
                self.assertEqual("discarded", discarded["status"])

    def test_llm_path_requires_consent_and_a_local_vision_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, snap, patches = self._running(temp, synthetic_ocr=True)
            with _Patches(patches):
                name = self._write_capture(config, "photo.png")
                with self.assertRaises(ValueError):
                    app.analyze_attachment_llm({"name": name})
                with self.assertRaises(RuntimeError):
                    app.analyze_attachment_llm({"name": name, "consent": True})

    def test_gemini_vision_sends_only_confirmed_image_bytes_as_an_unverified_hint(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, _snap, patches = self._running(temp, synthetic_ocr=True)
            config.update({
                "ai_provider": "gemini",
                "gemini_consent": True,
                "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
            })
            with _Patches(patches):
                name = self._write_capture(config, "vision.png")
                secret = Mock()
                secret.get_gemini_key.return_value = ("test-secret", "windows_account")
                remote = service_module.gemini_provider.ProviderResult(
                    text="화면에 야구 기록표가 보입니다.",
                    model=config["gemini_model"],
                    request_hash="vision-hash",
                    request_bytes=100,
                    response_bytes=50,
                    finish_reason="STOP",
                )
                with patch.object(app, "_secret_store", return_value=secret), patch.object(
                    service_module.gemini_provider, "generate_vision", return_value=remote
                ) as generate:
                    result = app.analyze_attachment_llm({
                        "name": name,
                        "provider": "gemini",
                        "consent": True,
                    })

                self.assertEqual("gemini", result["provider"])
                self.assertTrue(result["network_used"])
                self.assertEqual("gemini_vision_hint", result["provenance"])
                self.assertNotIn("test-secret", str(result))
                kwargs = generate.call_args.kwargs
                self.assertEqual(_png_bytes(), kwargs["image_bytes"])
                self.assertEqual("image/png", kwargs["mime_type"])
                self.assertNotIn(name, " ".join(str(value) for value in generate.call_args.args))

    def test_reanalysis_keeps_the_entire_committed_review_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, _snap, patches = self._running(temp, synthetic_ocr=True)
            with _Patches(patches):
                dashboard = app.check_save(lambda *_args: None)
                name = self._write_capture(config)
                row = app.analyze_attachments({"name": name, "_ocr_fixture": _fixture("game_result_ko.json")})["attachments"][0]
                result = app.commit_attachment({
                    "attachment_id": row["attachment_id"],
                    "decisions": {item["observation_id"]: ("user_confirmed" if item["field"] == "game.result" else "ignore") for item in row["observations"]},
                    "manual_description": "라커룸 햄버거 내기",
                    "retention": "keep_original",
                })
                ledger_path = Path(config["data_dir"]) / "worlds" / dashboard["world"]["world_id"] / "ledger.json"
                before = ledger_path.read_bytes()
                # Even another parser result for identical bytes cannot replace
                # the reviewed observations, binding, choices, or retention.
                again = app.analyze_attachments({"name": name, "_ocr_fixture": _fixture("batting_log_ko.json")})
                self.assertEqual(result["attachment"], again["attachments"][0])
                self.assertEqual(before, ledger_path.read_bytes())

    def test_non_live_attachment_paths_have_no_ledger_image_or_model_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            app, config, snap, patches = self._running(temp, synthetic_ocr=True)
            clock = FakeClock()
            patches.extend([
                patch.object(service_module, "UniverseRegistry", lambda directory: UniverseRegistry(directory, clock=clock)),
                patch.object(app, "_read_snapshot", side_effect=lambda _path: copy.deepcopy(snap)),
            ])
            with _Patches(patches):
                dashboard = app.check_save(lambda *_args: None)
                name = self._write_capture(config)
                row = app.analyze_attachments({"name": name, "_ocr_fixture": _fixture("game_result_ko.json")})["attachments"][0]
                ledger_path = Path(config["data_dir"]) / "worlds" / dashboard["world"]["world_id"] / "ledger.json"
                save_path = Path(config["save_path"]).resolve()
                self.assertTrue(save_path.is_relative_to(Path(temp).resolve()))
                save_path.unlink()
                for _ in range(MISSING_CHECKS_BEFORE_PRESERVE + 1):
                    clock.advance(MISSING_SECONDS_BEFORE_PRESERVE)
                    app.bootstrap()
                self.assertEqual("preserved_read_only", app.read_universe(dashboard["world"]["world_id"])["state"])
                snap["date"]["day"] = 30
                snap["content_hash"] = "returned-newer-save"
                save_path.write_bytes(b"returned-newer-save")
                self.assertEqual("relink_pending", app.bootstrap()["world"]["binding"]["state"])
                before = ledger_path.read_bytes()
                files_before = {str(path): path.read_bytes() for key in ("output_dir", "shots_dir") for path in Path(config[key]).rglob("*") if path.is_file()}
                with patch.object(attachments, "analyze_local", side_effect=AssertionError("OCR must not start")), patch.object(
                    service_module.narration_module, "pick_endpoint", side_effect=AssertionError("model probe must not start")
                ), patch.object(service_module.Ledger, "sync_save_history", side_effect=AssertionError("preserved history must not migrate")):
                    for method, payload in (
                        (app.analyze_attachments, {"name": name}),
                        (app.discard_attachment, {"attachment_id": row["attachment_id"]}),
                        (app.commit_attachment, {"attachment_id": row["attachment_id"]}),
                        (app.analyze_attachment_llm, {"name": name, "consent": True}),
                    ):
                        with self.subTest(method=method.__name__), self.assertRaises(WorldReadOnlyError):
                            method(payload)
                self.assertEqual(before, ledger_path.read_bytes())
                self.assertEqual(files_before, {str(path): path.read_bytes() for key in ("output_dir", "shots_dir") for path in Path(config[key]).rglob("*") if path.is_file()})

    def test_api_routes_and_upload_metadata_rules_exist(self):
        source = (Path(__file__).resolve().parent.parent / "ui_server.py").read_text(encoding="utf-8")
        for route in ("analyze-local", "propose-application", "commit", "discard", "analyze-llm"):
            self.assertIn(f'"{route}"', source)
        self.assertIn("/api/v1/attachments/status", source)
        self.assertIn("strip_jpeg_metadata", source)


class _Patches:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *_exc):
        for item in reversed(self.patches):
            item.stop()
        return False


if __name__ == "__main__":
    unittest.main()
