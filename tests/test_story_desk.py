"""Synthetic director desk gates: never contact a provider or inspect user saves."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import gemini_provider
import memory_windows
import narrate
import service
import story_desk as desk
import story_engine
import test_story_service as fixtures

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


class StoryDeskCoreTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixtures.snapshot()
        self.world = {"world_id": "world-one", "generation": 1}
        self.state = {"world_id": "world-one"}
        self.context = desk.origin(self.state, self.snapshot, self.world)

    def selection(self, **extra):
        return desk.normalize({"request_id": "request-core-123", "text": "햄버거를 먹으며 동료와 농담한다.", **extra})

    def test_catalog_has_unique_sources_and_71_executable_choices(self):
        catalog = desk.catalog()
        self.assertEqual(71, len(catalog["actions"]))
        self.assertEqual(71, len({r["id"] for r in catalog["actions"]}))
        for row in catalog["actions"]:
            self.assertTrue(row["source_detail"]["url"].startswith("https://"))
            normalized = self.selection(action_id=row["id"])
            state = copy.deepcopy(self.state)
            desk.append_turn(state, self.snapshot, self.context, normalized, normalized["text"], None, [], {})
            self.assertEqual(1, len(state[desk.STORE]["turns"]))

    def test_no_active_draft_tryout_or_midcareer_manager_claim(self):
        for key in ("active_draft", "tryout"):
            self.assertEqual("fiction_only", desk.action(key)["status"])
            with self.assertRaises(ValueError):
                self.selection(action_id=key, mode="observed", action_confirmed=True)
        self.assertIn("도중", desk.action("player_manager")["note"])
        self.assertIn("18세", desk.action("meet_partner")["note"])

    def test_observed_action_requires_confirmation(self):
        with self.assertRaises(ValueError): self.selection(action_id="rest", mode="observed")
        self.assertEqual("observed", self.selection(action_id="rest", mode="observed", action_confirmed=True)["mode"])

    def test_long_input_and_image_validation_never_truncate(self):
        text = "가" * 8000
        self.assertEqual(text, self.selection(text=text)["text"])
        for values in ({"text": text + "가"}, {"text": text, "remember_input": True},
                       {"images": ["one.png"]}, {"images": ["one.png"] * 2, "images_confirmed": True},
                       {"provider": "auto"}):
            with self.assertRaises(ValueError): self.selection(**values)

    def test_append_is_world_scoped_and_new_date_seals_previous(self):
        selected = self.selection()
        desk.append_turn(self.state, self.snapshot, self.context, selected, "첫 장면", "local", [], {})
        other = {**self.context, "world_id": "world-two"}
        self.assertEqual([], desk.turns(self.state, other))
        self.assertEqual([], desk.turns(self.state, {**self.context, "player_id": "8"}))
        day2 = copy.deepcopy(self.snapshot); day2["date"]["day"] += 1
        second = desk.origin(self.state, day2, self.world)
        desk.append_turn(self.state, day2, second, self.selection(request_id="request-day2-123"), "둘째 장면", None, [], {})
        self.assertEqual("sealed", self.state["story_sessions"]["2027-07-24"]["status"])
        self.assertEqual(2, len(desk.turns(self.state, second)))

    def test_same_request_is_idempotent_but_changed_payload_rejected(self):
        selected = self.selection()
        desk.append_turn(self.state, self.snapshot, self.context, selected, "장면", None, [], {})
        self.assertIsNotNone(desk.duplicate(self.state, selected, self.context))
        with self.assertRaises(ValueError): desk.duplicate(self.state, self.selection(text="다른 내용"), self.context)

    def test_memory_revision_retirement_keeps_historical_content(self):
        note = desk.add_memory(self.state, {"kind":"place", "label":"니시노미야", "detail":"가상 단골 가게"}, self.context)
        day2 = {**self.context, "game_date": "2027-07-25"}
        desk.add_memory(self.state, {**note, "detail":"새로운 메뉴"}, day2)
        self.assertEqual("가상 단골 가게", desk.memories(self.state, self.context)[0]["detail"])
        self.assertEqual("새로운 메뉴", desk.memories(self.state, day2)[0]["detail"])
        desk.retire_memory(self.state, note["id"], day2)
        self.assertEqual([], desk.memories(self.state, day2))
        self.assertEqual(3, len(self.state[desk.STORE]["memories"]))

    def test_model_reply_is_never_automatically_a_fact_or_memory(self):
        before = copy.deepcopy(self.snapshot)
        desk.append_turn(self.state, self.snapshot, self.context, self.selection(), "가상의 감독이 농담했다.", "model", [], {})
        self.assertEqual([], self.state[desk.STORE]["memories"])
        self.assertEqual(before, self.snapshot)
        self.assertNotIn("fact_registry", self.state)

    def test_remote_recall_is_opt_in_independent_of_publicity(self):
        desk.add_memory(self.state, {"label":"로컬 비밀", "detail":"비밀 요리"}, self.context)
        desk.add_memory(self.state, {"label":"허용 취향", "detail":"치즈버거", "remote_allowed":True}, self.context)
        local = self.selection(visibility="public")
        desk.append_turn(self.state, self.snapshot, self.context, local, "로컬만의 농담", "local", [], {})
        _, remote, refs = desk.prompt(self.state, self.snapshot, self.context, self.selection(provider="gemini"))
        self.assertNotIn("로컬 비밀", remote); self.assertNotIn("로컬만의 농담", remote)
        self.assertIn("허용 취향", remote); self.assertEqual([], refs["turn_ids"])
        legacy = memory_windows.build_view(self.state, as_of_date=self.context["game_date"], universe_id="world-one", protagonist_id=7, remote=True)
        self.assertNotIn("로컬만의 농담", json.dumps(legacy, ensure_ascii=False))
        _, old_prompt = story_engine.chat_prompt(self.snapshot, self.state["story_sessions"][self.context["game_date"]], {"user_text":"다음 장면"})
        self.assertNotIn("로컬만의 농담", old_prompt)

    def test_bounded_recall_finds_old_motif_without_deleting_history(self):
        for i in range(45):
            text = "햄버거 약속" if i == 0 else f"훈련 장면 {i}"
            selected = self.selection(request_id=f"request-seq-{i:04}", text=text)
            desk.append_turn(self.state, self.snapshot, self.context, selected, "장면 본문 " * 200, None, [], {})
        _, packet, refs = desk.prompt(self.state, self.snapshot, self.context, self.selection())
        self.assertIn("햄버거 약속", packet)
        self.assertLessEqual(len(refs["turn_ids"]), 7)
        self.assertLess(len(packet), 31000)
        page = desk.view(self.state, self.snapshot, self.world)
        self.assertTrue(page["has_more"]); self.assertEqual(30, len(page["turns"]))
        older = desk.view(self.state, self.snapshot, self.world, before=page["turns"][0]["id"])
        self.assertEqual(15, len(older["turns"]))
        self.assertEqual(45, len(self.state[desk.STORE]["turns"]))

    def test_reference_is_opt_in_and_invalid_source_rejected(self):
        self.assertEqual(12, len(desk.reference_pack()["items"]))
        self.assertNotIn(desk.STORE, self.state)
        with self.assertRaises(ValueError):
            desk.add_memory(self.state, {"label":"a", "detail":"b", "source_url":"javascript:alert(1)"}, self.context)

    def test_inline_generated_markdown_and_empty_failures(self):
        formatted = desk.validate_reply("**1. 첫 장면.** 본문이다. **2. 다음 장면.** 다음 본문이다.")
        self.assertIn("\n\n## 2.", formatted)
        for text in ("", "English only"):
            with self.assertRaises(ValueError): desk.validate_reply(text)


class StoryDeskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.snap, self.ledger, self.event, self.config, self.world = fixtures.StoryServiceTests()._fixture(self.temp.name)
        self.config.update(gemini_consent=True, gemini_model="gemini-3.5-flash-lite")
        self.app = service.StarModeService()
        self.app._prepare = lambda update=None: (self.config, {}, None, self.ledger, self.world, self.event)
        self.app._secret_store = Mock(return_value=Mock(get_gemini_key=Mock(return_value=("fake-test-key", "test"))))
        self.app._record_gemini_generation = Mock(); self.app._record_gemini_failure = Mock()
        p = patch.object(service, "_llm_reachable", return_value=False); p.start(); self.addCleanup(p.stop)
        self.update = lambda message, progress=None, phase=None: None

    def payload(self, **extra):
        return {"desk_origin": desk.origin(self.ledger.state, self.snap, self.world),
                "request_id":"service-request-001", "text":"햄버거를 들고 동료에게 농담한다.", **extra}

    def test_note_roundtrip_existing_vault_and_no_provider(self):
        with patch.object(narrate, "director_chat") as local, patch.object(gemini_provider, "generate_text") as remote:
            result = self.app.write_director_turn(self.update, self.payload())
        local.assert_not_called(); remote.assert_not_called()
        self.assertEqual(1, result["story_desk"]["total"])
        self.assertEqual(1, result["story"]["current"]["sequence"])
        capsule = self.ledger.history_capsule("2027-07-24", self.snap)
        self.assertEqual(1, len(capsule["story_desk"]["turns"]))
        self.assertTrue((Path(self.config["output_dir"]) / "worlds/world-one/history/2027/07/24/capsule.json").is_file())
        reloaded = fixtures.Ledger(self.ledger.path, "world-one")
        self.assertEqual(self.ledger.state[desk.STORE], reloaded.state[desk.STORE])

    def test_local_and_gemini_share_prompt_and_formatting(self):
        with patch.object(narrate, "director_chat", return_value=("**1. 첫 장면.** 본문이다. **2. 다음 장면.** 농담했다.", "local-test")) as local:
            result = self.app.write_director_turn(self.update, self.payload(provider="local"))
        self.assertEqual("local-test", result["story_desk"]["turns"][-1]["model"])
        self.assertIn("\n\n## 2.", result["story_desk"]["turns"][-1]["reply"])
        remote_result = Mock(text="동료가 웃으며 다음 약속을 꺼냈다.", model="gemini-3.5-flash-lite")
        with patch.object(gemini_provider, "generate_text", return_value=remote_result) as remote:
            result = self.app.write_director_turn(self.update, self.payload(provider="gemini", remote_consent=True, request_id="service-request-002"))
        self.assertEqual(local.call_args.args[0], remote.call_args.args[0])
        self.assertEqual(2, result["story_desk"]["total"])
        self.app._record_gemini_generation.assert_called_once()

    def test_gemini_requires_settings_and_per_request_consent(self):
        with patch.object(gemini_provider, "generate_text") as remote:
            with self.assertRaises(ValueError): self.app.write_director_turn(self.update, self.payload(provider="gemini"))
            self.config["gemini_consent"] = False
            with self.assertRaises(ValueError): self.app.write_director_turn(self.update, self.payload(provider="gemini", remote_consent=True))
        remote.assert_not_called(); self.assertNotIn(desk.STORE, self.ledger.state)

    def test_failure_keeps_prior_ledger_and_never_falls_back(self):
        before = copy.deepcopy(self.ledger.state)
        with patch.object(gemini_provider, "generate_text", side_effect=gemini_provider.GeminiProviderError("BUSY", "busy")), patch.object(narrate, "director_chat") as local:
            with self.assertRaises(gemini_provider.GeminiProviderError):
                self.app.write_director_turn(self.update, self.payload(provider="gemini", remote_consent=True))
        local.assert_not_called(); self.assertEqual(before, self.ledger.state)

    def test_stale_start_and_late_result_have_no_side_effects(self):
        stale = self.payload(); stale["desk_origin"]["player_id"] = "other"
        with patch.object(narrate, "director_chat") as local:
            with self.assertRaises(ValueError): self.app.write_director_turn(self.update, {**stale, "provider":"local"})
            local.assert_not_called()
        def advance(*args, **kwargs):
            self.snap["date"]["day"] += 1
            return "다음 장면", "model"
        with patch.object(narrate, "director_chat", side_effect=advance):
            with self.assertRaises(ValueError): self.app.write_director_turn(self.update, self.payload(provider="local"))
        self.assertNotIn(desk.STORE, self.ledger.state)

    def test_read_only_guard_precedes_images_and_models(self):
        self.app._require_live = Mock(side_effect=RuntimeError("readonly"))
        with patch.object(narrate, "director_chat") as local:
            with self.assertRaises(RuntimeError): self.app.write_director_turn(self.update, self.payload(provider="local"))
            with self.assertRaises(RuntimeError): self.app.write_story_memory(self.payload(label="a", detail="b"))
        local.assert_not_called(); self.assertNotIn(desk.STORE, self.ledger.state)

    def test_cancel_before_commit_drops_response(self):
        from contextlib import contextmanager
        from job_manager import JobCancelled
        @contextmanager
        def rejected():
            raise JobCancelled("cancelled")
            yield
        self.update.commit = rejected
        with patch.object(narrate, "director_chat", return_value=("뒤늦은 응답", "model")):
            with self.assertRaises(JobCancelled): self.app.write_director_turn(self.update, self.payload(provider="local"))
        self.assertNotIn(desk.STORE, self.ledger.state)

    def test_memory_change_while_writing_rejects_old_answer(self):
        def mutate(*args, **kwargs):
            self.app.write_story_memory(self.payload(label="바뀐 설정", detail="다른 기억"))
            return "오래된 응답", "model"
        with patch.object(narrate, "director_chat", side_effect=mutate):
            with self.assertRaises(ValueError): self.app.write_director_turn(self.update, self.payload(provider="local"))
        self.assertEqual([], self.ledger.state[desk.STORE]["turns"])

    def test_images_are_explicit_bounded_and_copied_to_world(self):
        shots = Path(self.config["shots_dir"]); shots.mkdir()
        (shots / "one.png").write_bytes(PNG); (shots / "unused.png").write_bytes(PNG)
        payload = self.payload(images=["one.png"], images_confirmed=True, provider="local")
        with patch.object(narrate, "director_chat", return_value=("그림 속 사물을 바라본다.", "vision")) as local:
            result = self.app.write_director_turn(self.update, payload)
        parts = local.call_args.kwargs["image_parts"]
        self.assertEqual(1, len(parts)); self.assertEqual(PNG, parts[0]["data"])
        image = result["story_desk"]["turns"][0]["images"][0]
        self.assertTrue(self.app.read_story_image(image["file"]).is_file())
        (shots / "one.png").unlink()
        self.assertEqual(PNG, self.app.read_story_image(image["file"]).read_bytes())
        with self.assertRaises(ValueError): self.app.read_story_image("../outside.png")

    def test_duplicate_completed_request_does_not_call_twice(self):
        payload = self.payload(provider="local")
        with patch.object(narrate, "director_chat", return_value=("장면을 이어간다.", "local")) as local:
            self.app.write_director_turn(self.update, payload)
            result = self.app.write_director_turn(self.update, payload)
        self.assertEqual(1, local.call_count); self.assertEqual(1, result["story_desk"]["total"])

    def test_reference_import_does_not_retarget_player_or_enable_remote(self):
        result = self.app.write_story_memory(self.payload(operation="import_reference", ids=["burger","koshien"]))
        notes = result["dashboard"]["story_desk"]["memories"]
        self.assertEqual(2, len(notes)); self.assertTrue(all(not row["remote_allowed"] for row in notes))
        self.assertTrue(all(row["player_id"] == "7" for row in notes))
        self.assertEqual("Test Player", self.snap["player"]["name"])

    def test_preserved_image_read_does_not_need_or_touch_live_save(self):
        shots = Path(self.config["shots_dir"]); shots.mkdir()
        (shots / "one.png").write_bytes(PNG)
        result = self.app.write_director_turn(self.update, self.payload(images=["one.png"], images_confirmed=True))
        image = result["story_desk"]["turns"][0]["images"][0]
        before = copy.deepcopy(self.ledger.state)
        self.app._prepare = Mock(side_effect=FileNotFoundError("removed save"))
        with patch("story_desk_service.config_module.load", return_value=self.config), patch.object(self.app, "_universe_ledger", return_value=(self.ledger, {"state":"preserved_read_only"})):
            self.assertEqual(PNG, self.app.read_story_image(image["file"], universe_id="world-one").read_bytes())
            with self.assertRaises(FileNotFoundError): self.app.read_story_image(image["file"], universe_id="other-world")
        self.assertEqual(before, self.ledger.state)
        self.app._prepare.assert_not_called()

    def test_capsule_keeps_all_day_turns_not_only_chat_page(self):
        context = desk.origin(self.ledger.state, self.snap, self.world)
        for i in range(35):
            choice = desk.normalize({"request_id":f"history-all-{i:04}", "text":f"장면 {i}"})
            desk.append_turn(self.ledger.state, self.snap, context, choice, "원문", None, [], {})
        capsule = self.ledger.history_capsule("2027-07-24", self.snap)
        self.assertEqual(35, len(capsule["story_desk"]["turns"]))
        self.assertNotIn("catalog", capsule["story_desk"])
