"""Service-level regressions for the 2026-09-03 master-plan integration audit.

All saves, captures, and output live in disposable fixture directories. No
game process, real profile, OCR process, or model is used.
"""

import copy
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import narrative_contracts as nc
import narrative_engine
import editorial_engine
import narrative_state
import narrative_props
import service as service_module
from ledger_v2 import Ledger

try:
    from tests import test_attachments as fixtures
except ImportError:
    import test_attachments as fixtures


class ServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="starmodefeed-integration-")
        self.addCleanup(self.temp.cleanup)
        self.helper = fixtures.AttachmentServiceTests()
        self.app, self.config, self.snap, patches = self.helper._running(self.temp.name)
        patches.extend([
            patch.object(self.app, "_read_snapshot", side_effect=lambda _path: copy.deepcopy(self.snap)),
            patch.object(service_module, "_llm_reachable", return_value=False),
            patch.object(service_module, "_probe_llm_ports", return_value=False),
        ])
        self.patches = fixtures._Patches(patches)
        self.patches.__enter__()
        self.addCleanup(self.patches.__exit__)
        dashboard = self.app.check_save(lambda *_args: None)
        self.world_id = dashboard["world"]["world_id"]
        self.ledger_path = Path(self.config["data_dir"]) / "worlds" / self.world_id / "ledger.json"

    def ledger(self):
        return Ledger(str(self.ledger_path), self.world_id, read_only=True)

    def chat(self, text):
        return self.app.chat_story({"user_text": text, "renderer_preference": "deterministic"})

    def confirm_result(self):
        name = self.helper._write_capture(self.config)
        # Bind the authored OCR example to this test's independent world.
        # Production attachment/date validation must remain strict.
        date = self.snap["date"]
        ocr = json.dumps(fixtures._fixture("game_result_ko.json"), ensure_ascii=False)
        for before, after in (
            ("2030년 5월 12일", f"{date['year']}년 {date['month']}월 {date['day']}일"),
            ("Test Hitter", self.snap["player"]["name"]),
            ("TestHawks", self.snap["player"]["team"].split()[0]),
        ):
            ocr = ocr.replace(before, after)
        row = self.app.analyze_attachments({"name": name, "_ocr_fixture": json.loads(ocr)})["attachments"][0]
        result = self.app.commit_attachment({
            "attachment_id": row["attachment_id"],
            "decisions": {item["observation_id"]: ("user_confirmed" if item["field"] == "game.result" else "ignore") for item in row["observations"]},
            "retention": "keep_original",
        })
        return result["facts"][0]

    def test_reviewed_image_result_answers_with_original_fact_provenance(self):
        fact = self.confirm_result()
        result = self.chat("오늘 경기 결과와 점수 알려줘")
        response = result["response"]
        for text in ("6–3", "승리", "AwayBears", "홈"):
            self.assertIn(text, response["reply"]["text"])
        self.assertIn(fact["fact_id"], response["fact_ids"])
        callouts = [row for row in response["reply"]["blocks"] if fact["fact_id"] in row.get("fact_ids", [])]
        self.assertTrue(callouts)
        self.assertTrue(all(row["evidence_class"] == "user_confirmed" for row in callouts))
        self.assertIn("사용자 확인", callouts[0]["label"])
        self.assertIn(fact["fact_id"], result["turn"]["fact_ids"])
        self.assertEqual(result["turn"]["fact_ids"], self.ledger().state["conversation_turns"][-1]["fact_ids"])
        self.assertEqual([], response["proposed_events"])
        self.assertEqual([], nc.validate_blocks(response["reply"]["blocks"]))

    def test_result_questions_do_not_relabel_yesterday_as_today(self):
        fact = self.confirm_result()
        self.snap["date"]["day"] = 25
        self.snap["content_hash"] = "next-day"
        self.app.check_save(lambda *_args: None)
        today = self.chat("오늘 경기 결과 알려줘")["response"]
        self.assertNotIn("6–3", today["reply"]["text"])
        self.assertNotIn(fact["fact_id"], today["fact_ids"])
        yesterday = self.chat("어제 경기 결과와 점수 알려줘")["response"]
        self.assertIn("6–3", yesterday["reply"]["text"])
        self.assertIn("2027-07-24", yesterday["reply"]["text"])
        self.assertIn(fact["fact_id"], yesterday["fact_ids"])

    def test_conflicting_result_facts_remain_separate_and_visible(self):
        original = self.confirm_result()
        state = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        conflict = nc.new_fact(kind="game.result", label="경기 결과", value={"outcome": "loss", "runs_for": 1, "runs_against": 4, "opponent": "Hanshin", "side": "away"}, evidence_class="user_confirmed", game_date="2027-07-24", scope="game", source="manual")
        state["fact_registry"][conflict["fact_id"]] = conflict
        ledger = Ledger(str(self.ledger_path), self.world_id)
        ledger.state = state
        ledger.save()
        response = self.chat("오늘 경기 결과 알려줘")["response"]
        self.assertIn("서로 다른", response["reply"]["text"])
        self.assertIn("6–3", response["reply"]["text"])
        self.assertIn("1–4", response["reply"]["text"])
        self.assertIn(original["fact_id"], response["fact_ids"])
        self.assertIn(conflict["fact_id"], response["fact_ids"])
        self.assertEqual(2, len(self.ledger().state["fact_registry"]))

    def test_unconfirmed_or_cross_protagonist_images_are_not_chat_facts(self):
        fact = self.confirm_result()
        state = self.ledger().state
        for bad_state in ("analyzed", "foreign_player", "foreign_world"):
            candidate = copy.deepcopy(state)
            record = next(iter(candidate["attachment_records"].values()))
            if bad_state == "analyzed":
                record["status"] = "analyzed"
            elif bad_state == "foreign_player":
                record["binding"]["protagonist_id"] = 1000
            else:
                candidate["world_id"] = "another-universe"
            response = narrative_engine.generate_turn(user_text="오늘 경기 결과 알려줘", snapshot=self.snap, game_date="2027-07-24", spotlight={}, universe_id=self.world_id, ledger_state=candidate)
            with self.subTest(state=bad_state):
                self.assertNotIn("6–3", response["reply"]["text"])
                self.assertNotIn(fact["fact_id"], response["fact_ids"])

    def test_distinct_proposals_can_each_commit_once_and_survive_restart(self):
        response = self.chat("오늘 감독에게 섭섭하지만 기자들 앞에서는 팀을 감싸고 싶어")
        turn = response["turn"]
        self.assertEqual(2, len(turn["proposed_events"]))
        committed = {}
        for proposal in turn["proposed_events"]:
            payload = {"confirm_turn_id": turn["turn_id"], "confirm_proposal_id": proposal["proposal_id"]}
            result = self.app.chat_story(payload)
            committed[proposal["proposal_id"]] = result["item"]["id"]
            self.assertEqual(committed, result["turn"]["committed_proposals"])
            before = self.ledger_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "이미"):
                self.app.chat_story(payload)
            self.assertEqual(before, self.ledger_path.read_bytes())
        stored = self.ledger().state
        self.assertEqual(2, len(stored["world_events"]))
        self.assertEqual(committed, stored["conversation_turns"][-1]["committed_proposals"])
        self.assertEqual(2, len(stored["story_sessions"]["2027-07-24"]["turns"]))

    def test_legacy_committed_proposal_is_read_without_migrating_the_turn(self):
        turn = {"committed_event_id": "old-event", "committed_proposal_id": "first", "proposed_events": [{"proposal_id": "first"}, {"proposal_id": "second"}]}
        before = copy.deepcopy(turn)
        self.assertTrue(narrative_state.proposal_is_committed(turn, "first"))
        self.assertFalse(narrative_state.proposal_is_committed(turn, "second"))
        self.assertEqual(before, turn)

    def advance_home_run(self, day=25):
        self.snap["date"]["day"] = day
        self.snap["content_hash"] = f"home-run-{day}"
        for stat, value in {"bat_AB": 4, "bat_H": 1, "bat_HR": 1, "bat_RBI": 1}.items():
            self.snap["stats"][stat] += value

    def test_verified_home_run_fires_one_private_callback_and_replays_without_generation(self):
        self.chat("햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.chat("다음 홈런 때 이 농담을 다시 꺼내자")
        self.advance_home_run()
        dashboard = self.app.check_save(lambda *_args: None)
        state = self.ledger().state
        prop = next(iter(state["narrative_props"].values()))
        self.assertEqual("callback", prop["state"])
        self.assertEqual("2027-07-25", prop["last_used_date"])
        self.assertEqual(1, len(prop["callbacks"]))
        self.assertTrue(prop["recurrence_rules"]["trigger_events"][0]["fired"])
        game_events = [row for row in state["world_events"] if row["event_type"] == "SP.GAME.BAT.HOME_RUN"]
        self.assertEqual(1, len(game_events))
        self.assertEqual("derived_analysis", game_events[0]["evidence_class"])
        callbacks = [row for row in dashboard["story"]["current"]["turns"] if row["scene"].get("event_type") == "SP.USER.PROP.CALLBACK"]
        self.assertEqual(1, len(callbacks))
        self.assertIn("햄버거", callbacks[0]["scene"]["response"])
        self.assertIn("홈런", callbacks[0]["scene"]["response"])
        self.assertEqual(game_events[0]["event_id"], callbacks[0]["trigger_event_id"])
        self.assertEqual([], callbacks[0]["reactions"]["boards"])
        self.assertEqual([], callbacks[0]["reactions"]["media"])
        self.assertNotIn("햄버거", json.dumps(dashboard["feed"], ensure_ascii=False))
        self.assertTrue(dashboard["feed"]["game_narrative"]["reactions"]["boards"])
        before = self.ledger_path.read_bytes()
        self.app.check_save(lambda *_args: None)
        self.assertEqual(before, self.ledger_path.read_bytes())
        restarted = service_module.StarModeService()
        with patch.object(restarted, "_read_snapshot", return_value=copy.deepcopy(self.snap)), patch.object(
            narrative_engine, "realize_game_reactions", side_effect=AssertionError("archive reads never generate")
        ), patch.object(narrative_engine, "realize_event", side_effect=AssertionError("archive reads never generate")):
            restarted.check_save(lambda *_args: None)
            capsule = restarted.read_universe_capsule(self.world_id, "2027-07-25")
        self.assertEqual(callbacks[0], capsule["story"]["turns"][0])
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_multi_day_gap_and_correction_do_not_invent_a_scheduled_game_scene(self):
        self.chat("햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.chat("다음 홈런 때 이 농담을 다시 꺼내자")
        self.advance_home_run(day=29)
        self.app.check_save(lambda *_args: None)
        self.assertEqual([], self.ledger().state["world_events"])
        prop = next(iter(self.ledger().state["narrative_props"].values()))
        self.assertFalse(prop["recurrence_rules"]["trigger_events"][0]["fired"])
        self.snap["content_hash"] = "correction"
        self.snap["stats"]["bat_HR"] -= 1
        self.app.check_save(lambda *_args: None)
        self.assertEqual([], self.ledger().state["world_events"])

    def test_scheduling_after_observation_waits_for_a_later_home_run(self):
        self.chat("햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.advance_home_run()
        self.chat("다음 홈런 때 이 농담을 다시 꺼내자")
        self.app.check_save(lambda *_args: None)
        prop = next(iter(self.ledger().state["narrative_props"].values()))
        self.assertEqual([], prop["callbacks"])
        self.advance_home_run(day=26)
        self.app.check_save(lambda *_args: None)
        prop = next(iter(self.ledger().state["narrative_props"].values()))
        self.assertEqual(1, len(prop["callbacks"]))

    def test_pitching_delta_never_certifies_a_perfect_game_or_complete_game(self):
        self.snap["date"]["day"] = 25
        self.snap["content_hash"] = "nine-hitless-innings"
        self.snap["stats"]["pit_IP"] += 9
        self.snap["stats"]["pit_TBF"] += 27
        self.snap["stats"]["pit_K"] += 27
        dashboard = self.app.check_save(lambda *_args: None)
        events = self.ledger().state["world_events"]
        self.assertTrue(events)
        self.assertNotIn("SP.GAME.PITCH.PERFECT_GAME", [row["event_type"] for row in events])
        self.assertNotIn("SP.GAME.PITCH.NO_HITTER", [row["event_type"] for row in events])
        self.assertNotIn("SP.GAME.PITCH.COMPLETE_GAME", [row["event_type"] for row in events])
        bundle = json.dumps(dashboard["feed"]["game_narrative"]["reactions"], ensure_ascii=False)
        for claim in ("퍼펙트", "완투", "완봉", "노히트노런"):
            self.assertNotIn(claim, bundle)

    def test_cancelled_game_commit_does_not_fire_or_persist_the_callback(self):
        self.chat("햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.chat("다음 홈런 때 이 농담을 다시 꺼내자")
        self.advance_home_run()
        before = self.ledger_path.read_bytes()

        @contextlib.contextmanager
        def cancelled_commit():
            raise RuntimeError("synthetic cancellation")
            yield  # pragma: no cover - context manager must never enter

        def update(*_args):
            pass

        update.commit = cancelled_commit
        with self.assertRaisesRegex(RuntimeError, "synthetic cancellation"):
            self.app.check_save(update)
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.app.check_save(lambda *_args: None)
        self.assertEqual(1, len(next(iter(self.ledger().state["narrative_props"].values()))["callbacks"]))

    def test_forced_refresh_reuses_reactions_without_refiring_reserved_motifs(self):
        self.chat("햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.chat("다음 홈런 때 이 농담을 다시 꺼내자")
        self.advance_home_run()
        first = self.app.check_save(lambda *_args: None)
        before = self.ledger().state
        with patch.object(narrative_engine, "realize_game_reactions", side_effect=AssertionError("reuse the stored bundle")):
            refreshed = self.app.check_save(lambda *_args: None, {"force": True})
        after = self.ledger().state
        for key in ("world_events", "narrative_props", "reaction_instances"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(first["feed"]["game_narrative"], refreshed["feed"]["game_narrative"])

    def test_new_editorial_memory_is_committed_and_archive_reads_never_generate(self):
        first = self.ledger().state["community_memory"][editorial_engine.MEMORY_KEY]
        self.assertEqual(self.world_id, first["binding"]["universe_id"])
        original_files = {p: p.read_bytes() for p in Path(self.config["output_dir"]).rglob("*.json") if "archive" in p.parts}
        self.assertTrue(original_files)
        self.advance_home_run()
        dashboard = self.app.check_save(lambda *_args: None)
        feed = dashboard["feed"]
        self.assertEqual(self.world_id, feed["editorial"]["binding"]["universe_id"])
        self.assertTrue(feed["media"])
        remembered_ids = {r["id"] for r in self.ledger().state["community_memory"][editorial_engine.MEMORY_KEY]["articles"]}
        self.assertTrue({r["id"] for r in feed["media"]} <= remembered_ids)
        before = self.ledger_path.read_bytes()
        with patch.object(editorial_engine, "build", side_effect=AssertionError("stored reads never regenerate prose")):
            self.app.check_save(lambda *_args: None)
            self.app.read_universe_capsule(self.world_id, "2027-07-24")
        self.assertEqual(before, self.ledger_path.read_bytes())
        for path, contents in original_files.items():
            self.assertEqual(contents, path.read_bytes())

    def test_forced_editorial_refresh_keeps_hitless_secondary_and_both_sources(self):
        self.snap["date"]["day"] = 25
        self.snap["content_hash"] = "two-way-hitless"
        for key, value in {"pit_IP": 9, "pit_TBF": 27, "pit_K": 27, "bat_AB": 4}.items():
            self.snap["stats"][key] += value
        first = self.app.check_save(lambda *_args: None)
        forced = self.app.check_save(lambda *_args: None, {"force": True})
        for dashboard in (first, forced):
            text = " ".join(p for a in dashboard["feed"]["media"] for p in a["body"])
            self.assertIn("4타수 무안타", text)
            self.assertIn("27탈삼진", text)
            self.assertNotIn("맹타", text)
            facts = dashboard["feed"]["editorial"]["facts"]
            intervals = [f for f in facts if f["scope"] == "observation_interval"]
            self.assertTrue(intervals)
            self.assertTrue(all(len(f["source_ids"]) >= 2 for f in intervals))
        self.assertEqual(first["feed"]["game_narrative"], forced["feed"]["game_narrative"])

    def test_auto_model_empty_reply_falls_back_with_reviewed_facts_and_one_stored_turn(self):
        fact = self.confirm_result()
        message = "오늘 경기 결과와 점수 알려줘"
        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", return_value=(None, "synthetic no response")
        ) as model:
            result = self.app.chat_story({"user_text": message, "renderer_preference": "auto"})
        model.assert_called_once()
        self.assertEqual("deterministic", result["renderer"])
        self.assertEqual("no_response", result["fallback"]["reason"])
        self.assertIn("6–3", result["turn"]["reply_text"])
        self.assertIn(fact["fact_id"], result["turn"]["fact_ids"])
        self.assertEqual(message, result["turn"]["user_text"])
        self.assertEqual([], result["turn"]["proposed_events"])
        state = self.ledger().state
        self.assertEqual(1, len(state["conversation_turns"]))
        self.assertEqual(result["fallback"], state["conversation_turns"][0]["provenance"]["renderer_fallback"])

    def test_auto_model_exception_uses_normal_proposals_without_exposing_error_text(self):
        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", side_effect=TimeoutError("private-endpoint-detail")
        ):
            result = self.app.chat_story({"user_text": "오늘 감독에게 서운했어", "renderer_preference": "auto"})
        self.assertEqual("TimeoutError", result["fallback"]["error_type"])
        self.assertTrue(result["turn"]["proposed_events"])
        self.assertIsNone(result["item"])
        self.assertNotIn("private-endpoint-detail", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("private-endpoint-detail", self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual([], self.ledger().state["world_events"])

    def test_explicit_model_failure_does_not_silently_change_renderer_or_write(self):
        before = self.ledger_path.read_bytes()
        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", return_value=(None, "synthetic model failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic model failure"):
                self.app.chat_story({"user_text": "감독과 이야기할래", "renderer_preference": "llm"})
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_auto_fallback_refuses_a_changed_date_before_writing_the_turn(self):
        before = self.ledger_path.read_bytes()

        def changed_date(*_args):
            self.snap["date"]["day"] = 25
            self.snap["content_hash"] = "changed-during-model"
            return None, "synthetic response failure"

        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", side_effect=changed_date
        ):
            with self.assertRaisesRegex(RuntimeError, "세이브 문맥이 바뀌어"):
                self.app.chat_story({"user_text": "감독과 이야기할래", "renderer_preference": "auto"})
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_auto_fallback_refuses_a_save_that_disappeared_during_inference(self):
        before = self.ledger_path.read_bytes()

        def missing_save(*_args):
            Path(self.config["save_path"]).unlink()
            return None, "synthetic response failure"

        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", side_effect=missing_save
        ):
            with self.assertRaises((FileNotFoundError, service_module.WorldReadOnlyError)):
                self.app.chat_story({"user_text": "감독과 이야기할래", "renderer_preference": "auto"})
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_auto_fallback_reloads_intervening_button_events_without_overwriting_them(self):
        created = []

        def intervening_event(*_args):
            created.append(self.app.create_story_event({"category": "career", "situation": "meet_agent", "target": "agent", "visibility": "private"})["item"])
            raise TimeoutError("synthetic model failure")

        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", side_effect=intervening_event
        ):
            result = self.app.chat_story({"user_text": "오늘 감독에게 서운했어", "renderer_preference": "auto"})
        self.assertEqual("deterministic", result["renderer"])
        state = self.ledger().state
        self.assertEqual([row["id"] for row in created], [row["id"] for row in state["story_sessions"]["2027-07-24"]["turns"]])
        self.assertEqual(1, len(state["conversation_turns"]))


if __name__ == "__main__":
    unittest.main()
