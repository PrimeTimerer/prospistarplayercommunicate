"""Synthetic-only contracts for the first Star Player interaction bridge."""

import copy
import contextlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import event_ontology
import narrative_contracts as nc
import narrative_engine
import service as service_module
import star_interactions as interactions
import story_engine
from ledger_v2 import Ledger

try:
    from tests import test_attachments as fixtures
except ImportError:
    import test_attachments as fixtures


class InteractionPlanTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"player": {"id": 123, "name": "가상 주인공", "team": "가상 구단"},
                         "date": {"year": 2027, "month": 7, "day": 24}, "stats": {}, "content_hash": "fixture"}
        self.payload = {"category": "starplayer", "situation": "player_exchange", "target": "rookie",
                        "participant_name": "가상 후배", "interaction_topic": "햄버거", "visibility": "private"}

    def plan(self, payload=None, state=None):
        return interactions.start_plan(payload or self.payload, snapshot=self.snapshot, game_date="2027-07-24", universe_id="world", state=state or {})

    def test_additive_catalog_preserves_all_legacy_choices(self):
        catalog = story_engine.catalog()
        self.assertEqual(28, sum(len(row["situations"]) for row in catalog["categories"]))
        self.assertEqual(4, len(catalog["interactions"]["actions"]))
        self.assertFalse(catalog["interactions"]["game_writes"])
        self.assertEqual("unknown_in_current_save", catalog["interactions"]["availability"])
        for action in catalog["interactions"]["actions"]:
            self.assertTrue(event_ontology.is_registered(action["event_type"]))
            self.assertTrue(action["rule_sources"])
            self.assertTrue(all(url.startswith("https://www.konami.com/") for url in action["rule_sources"]))

    def test_every_authored_action_and_beat_renders_typed_slots(self):
        for action_id in interactions.pack()["actions"]:
            payload = dict(self.payload, situation=action_id, interaction_topic="러닝 또는 햄버거", target="rookie")
            plan = self.plan(payload)
            row = plan["initial"]
            for beat in interactions.BEATS:
                with self.subTest(action=action_id, beat=beat):
                    candidate = dict(plan, beat=beat)
                    for index in range(24):
                        snap = dict(self.snapshot, content_hash=f"sample-{index}")
                        blocks = interactions._blocks(row, candidate, snapshot=snap)
                        self.assertEqual([], nc.validate_blocks(blocks))
                        text = nc.project_blocks(blocks)
                        self.assertNotRegex(text, r"\{(?:player|partner|place|topic)")
                        self.assertIn("가상 주인공", text)
                        self.assertGreater(len(text), 50)

    def test_start_planning_and_preview_are_pure_and_repeatable(self):
        state = {}
        snapshot = copy.deepcopy(self.snapshot)
        first = self.plan(state=state)
        self.assertEqual(first, self.plan(state=state))
        self.assertEqual({}, state)
        response = interactions.chat_plan(state, dict(self.payload, user_text="같이 이야기하고 싶어"),
                                          snapshot=self.snapshot, game_date="2027-07-24", universe_id="world", sequence=1)
        self.assertTrue(response["proposed_events"])
        self.assertIn("확정 전", response["reply"]["text"])
        self.assertEqual({}, state)
        self.assertEqual(snapshot, self.snapshot)

    def test_authored_scene_keeps_resolvable_template_paths_and_manifest(self):
        response = interactions.chat_plan({}, dict(self.payload, user_text='"같이 얘기하자"'),
                                          snapshot=self.snapshot, game_date="2027-07-24", universe_id="world", sequence=1)
        self.assertEqual(interactions.manifest_hash(), response["provenance"]["corpus_manifest"])
        self.assertEqual(64, len(response["provenance"]["corpus_manifest"]))
        self.assertTrue(response["provenance"]["template_ids"])
        for template_id in response["provenance"]["template_ids"]:
            pack_id, source = template_id.split(":", 1)
            self.assertEqual(interactions.pack()["pack_id"], pack_id)
            node = interactions.pack()
            for key in source.split("/"):
                node = node[int(key)] if isinstance(node, list) else node[key]
            self.assertIsInstance(node, str)
        quote = next(block for block in response["reply"]["blocks"] if block.get("speaker") == "가상 주인공")
        self.assertEqual("user_explicit", quote["origin"])

    def test_user_confirmation_cannot_be_spoofed_as_save_verification(self):
        for mode, confirmed in (("save_verified", True), ("unknown", True), ("user_confirmed", False), ("user_confirmed", "true")):
            with self.subTest(mode=mode, confirmed=confirmed), self.assertRaises(ValueError):
                self.plan(dict(self.payload, interaction_mode=mode, action_confirmed=confirmed))
        row = self.plan(dict(self.payload, interaction_mode="user_confirmed", action_confirmed=True))["initial"]
        self.assertEqual("user_confirmed", row["activity_evidence_class"])

    def test_private_start_and_bounded_identity_fields_are_enforced(self):
        invalid = [
            dict(self.payload, visibility="social"), dict(self.payload, participant_name="x" * 61),
            dict(self.payload, interaction_topic=["not", "text"]),
            dict(self.payload, situation="tryout"), dict(self.payload, target="reporter"),
            dict(self.payload, situation="learning", interaction_topic=""),
            dict(self.payload, situation="learning", learning_progress="automatic"),
            dict(self.payload, interaction_origin={"universe_id": "other"}),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.plan(payload)

    def test_foreign_world_player_and_date_proposals_are_rejected(self):
        proposal = self.plan()
        for key, value in (("universe_id", "other"), ("protagonist_id", "456"), ("game_date", "2027-07-23")):
            bad = dict(proposal, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                interactions.validate_plan({}, bad, universe_id="world", protagonist_id=123, game_date="2027-07-24")

    def test_solo_conversations_do_not_create_an_unseen_partner(self):
        plan = self.plan(dict(self.payload, situation="outing_walk", target="self", participant_name=""))
        row = plan["initial"]
        for beat in interactions.BEATS:
            blocks = interactions._blocks(row, dict(plan, beat=beat, line="내 약속"), snapshot=self.snapshot)
            self.assertFalse(any(block["type"] == "quote" for block in blocks))
            self.assertNotIn("가상 후배", nc.project_blocks(blocks))
            self.assertEqual([], nc.validate_blocks(blocks))

    def test_instructions_are_not_literal_dialogue_but_quoted_lines_are(self):
        plan = self.plan(dict(self.payload, user_text="농담을 건네고 싶어"))
        blocks = interactions._blocks(plan["initial"], plan, snapshot=self.snapshot)
        self.assertNotIn("농담을 건네고 싶어", [block["text"] for block in blocks if block["type"] == "quote"])
        quoted = dict(plan, line='"햄버거 값은 삼진으로 계산할까?" 하고 농담한다')
        blocks = interactions._blocks(plan["initial"], quoted, snapshot=self.snapshot)
        self.assertIn("햄버거 값은 삼진으로 계산할까?", [block["text"] for block in blocks if block["type"] == "quote"])

    def test_unsupported_mechanics_are_not_advertised_as_playable_actions(self):
        rows = {row["mechanic"]: row for row in event_ontology.FAQ_CHECKLIST}
        self.assertEqual("unavailable", rows["Tryout"]["game_support"])
        self.assertEqual("setup_only", rows["Player-manager"]["game_support"])
        self.assertTrue(event_ontology.is_registered("SP.ROSTER.TRYOUT"))
        self.assertNotIn("tryout", interactions.pack()["actions"])


class InteractionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="starmodefeed-interactions-")
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

    def start(self, **kwargs):
        return self.app.create_story_event({
            "category": "starplayer", "situation": "player_exchange", "target": "rookie",
            "participant_name": "가상 후배", "interaction_place": "둘만 아는 가게",
            "interaction_topic": "햄버거", "visibility": "private", **kwargs,
        })

    def chat(self, interaction_id, text):
        return self.app.chat_story({"interaction_id": interaction_id, "user_text": text, "renderer_preference": "deterministic"})

    def confirm(self, result, **extra):
        proposal = result["turn"]["proposed_events"][0]
        return self.app.chat_story({"confirm_turn_id": result["turn"]["turn_id"],
                                   "confirm_proposal_id": proposal["proposal_id"], **extra})

    def row(self, ident):
        return self.ledger().state[interactions.STORE][ident]

    def advance_home_run(self, day=25):
        self.snap["date"]["day"] = day
        self.snap["content_hash"] = f"new-homer-{day}"
        for key, n in (("bat_AB", 4), ("bat_H", 2), ("bat_HR", 1), ("bat_RBI", 2)):
            self.snap["stats"][key] += n

    def test_button_starts_named_world_bound_scene_and_conversation(self):
        snapshot = copy.deepcopy(self.snap)
        result = self.start()
        ident = result["interaction_id"]
        row = self.row(ident)
        self.assertEqual(self.world_id, row["universe_id"])
        self.assertEqual(str(self.snap["player"]["id"]), row["protagonist_id"])
        self.assertEqual("가상 후배", row["participant_label"])
        self.assertEqual(1, row["revision"])
        self.assertIn("햄버거", result["item"]["scene"]["response"])
        self.assertEqual("starplayer", result["item"]["input"]["category"])
        self.assertEqual([], result["item"]["reactions"]["boards"])
        self.assertEqual([], result["item"]["reactions"]["media"])
        self.assertEqual([], result["item"]["reactions"]["waves"])
        self.assertEqual(snapshot, self.snap)
        self.assertEqual(1, len(result["dashboard"]["story"]["interactions"]))
        self.assertFalse(any(effect["kind"] == "relationship_signal" for effect in result["item"]["effects"]))

    def test_multi_turn_previews_do_not_change_relationships_or_promises(self):
        ident = self.start()["interaction_id"]
        replies = []
        for text, expected in (("햄버거 값은 삼진으로 계산하자 ㅋㅋ", "joke"), ("그건 싫어", "refuse"),
                               ("솔직히 요즘은 좀 불안해", "confide"), ("다음 홈런 치면 햄버거 사줄게 약속", "promise")):
            before = self.ledger().state
            response = self.chat(ident, text)
            self.assertEqual(expected, response["turn"]["proposed_events"][0]["beat"])
            previewed = self.ledger().state
            for key in (interactions.STORE, interactions.EVENTS, "relationship_edges", "world_events"):
                self.assertEqual(before[key], previewed[key])
            result = self.confirm(response)
            replies.append(result["item"]["scene"]["response"])
        self.assertEqual(4, len(set(replies)))
        row = self.row(ident)
        self.assertEqual("pending", row["promise"]["status"])
        self.assertEqual("SP.GAME.BAT.HOME_RUN", row["promise"]["trigger_type"])
        self.assertEqual(5, row["revision"])
        self.assertEqual(1, len(self.ledger().state["narrative_threads"]))
        self.assertEqual("noticed", next(iter(self.ledger().state["narrative_threads"].values()))["state"])

    def test_stale_proposals_cannot_overwrite_a_later_choice(self):
        ident = self.start()["interaction_id"]
        first = self.chat(ident, "다음에 같이 먹자 약속")
        second = self.chat(ident, "그건 싫어")
        self.confirm(second)
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "이후 대화"):
            self.confirm(first)
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.assertIsNone(self.row(ident)["promise"])

    def test_confirmation_is_single_use_and_survives_restart(self):
        ident = self.start()["interaction_id"]
        turn = self.chat(ident, "농담을 건네고 싶어")
        self.confirm(turn)
        self.app = service_module.StarModeService()
        with patch.object(self.app, "_read_snapshot", return_value=copy.deepcopy(self.snap)):
            before = self.ledger_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "이미"):
                self.confirm(turn)
            recalled = self.chat(ident, "지난 이야기가 뭐였지?")
        self.assertIn("햄버거", recalled["response"]["reply"]["text"])
        self.assertEqual(2, self.row(ident)["revision"])

    def test_next_day_continuation_does_not_edit_the_sealed_day(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음에 같이 햄버거 먹자 약속"))
        self.snap["date"]["day"] = 25
        self.snap["content_hash"] = "quiet-next-day"
        self.app.check_save(lambda *_args: None)
        sealed = copy.deepcopy(self.ledger().state["story_sessions"]["2027-07-24"])
        response = self.chat(ident, "지난 약속이 뭐였지?")
        self.assertIn("2027-07-24", response["response"]["reply"]["text"])
        self.assertIn("햄버거", response["response"]["reply"]["text"])
        self.assertEqual([], response["response"]["proposed_events"])
        self.confirm(self.chat(ident, "어제 이야기 다시 이어가자"))
        state = self.ledger().state
        self.assertEqual(sealed, state["story_sessions"]["2027-07-24"])
        self.assertEqual("2027-07-25", self.row(ident)["last_date"])
        with patch.object(interactions, "realized_event", side_effect=AssertionError("read must not generate")):
            capsule = self.app.read_universe_capsule(self.world_id, "2027-07-24")
        self.assertEqual(sealed["turns"], capsule["story"]["turns"])

    def test_two_named_players_do_not_share_edges_or_threads(self):
        first = self.start(participant_name="후배 하나")["interaction_id"]
        second = self.start(participant_name="후배 둘")["interaction_id"]
        self.confirm(self.chat(first, "솔직히 요즘 힘들어"))
        a, b = self.row(first), self.row(second)
        self.assertNotEqual(a["participant_key"], b["participant_key"])
        self.assertNotEqual(a["thread_id"], b["thread_id"])
        edges = self.ledger().state["relationship_edges"]
        self.assertGreater(edges[nc.edge_key("protagonist", a["participant_key"])]["trust"],
                           edges[nc.edge_key("protagonist", b["participant_key"])]["trust"])
        foreign = copy.deepcopy(self.ledger().state)
        foreign[interactions.STORE][first]["protagonist_id"] = "another-player"
        self.assertNotIn(first, [row["interaction_id"] for row in interactions.summaries(foreign, universe_id=self.world_id, protagonist_id=self.snap["player"]["id"], game_date="2027-07-24")])

    def test_game_observation_and_fictional_dialogue_have_separate_evidence(self):
        result = self.start(interaction_mode="user_confirmed", action_confirmed=True)
        ident = result["interaction_id"]
        state = self.ledger().state
        fact = state["fact_registry"][f"interaction-action:{ident}"]
        self.assertEqual("user_confirmed", fact["evidence_class"])
        self.assertEqual("starplayer.action", fact["kind"])
        self.assertEqual("fictional_intervention", state["world_events"][-1]["evidence_class"])
        self.assertIn("사용자 확인", result["item"]["scene"]["response"])
        self.assertEqual([], nc.validate_blocks(result["item"]["scene"]["blocks"]))
        self.assertNotIn("친밀도", fact["value"])

    def test_learning_registration_and_acquisition_are_not_inferred(self):
        for progress in interactions.PROGRESS:
            with self.subTest(progress=progress):
                ident = self.start(situation="learning", interaction_topic="가상 구종", learning_progress=progress,
                                   interaction_mode="user_confirmed", action_confirmed=True)["interaction_id"]
                self.assertEqual(progress, self.row(ident)["game_progress"])
                self.confirm(self.chat(ident, "좋아, 더 이야기하자"))
                self.assertEqual(progress, self.row(ident)["game_progress"])
        fictional = self.start(situation="learning", interaction_topic="가상 변화구", learning_progress="acquired")["interaction_id"]
        self.assertIsNone(self.row(fictional)["game_progress"])
        self.assertNotIn(f"interaction-action:{fictional}", self.ledger().state["fact_registry"])

    def test_close_pause_and_resume_use_the_same_durable_interaction(self):
        ident = self.start()["interaction_id"]
        for line, status in (("잠시 멈추자", "paused"), ("다시 이어가자", "open"), ("오늘 대화는 마무리하자", "closed")):
            self.confirm(self.chat(ident, line))
            self.assertEqual(status, self.row(ident)["status"])
        blocked = self.chat(ident, "좋아")
        self.assertEqual([], blocked["response"]["proposed_events"])
        self.confirm(self.chat(ident, "다시 이어가자"))
        self.assertEqual("open", self.row(ident)["status"])
        self.assertEqual(1, len(self.ledger().state[interactions.STORE]))

    def test_share_is_confirmed_once_and_does_not_publish_private_material(self):
        ident = self.start(participant_name="비밀선수이름", interaction_topic="감춰둔햄버거", interaction_place="비밀장소코드")["interaction_id"]
        preview = self.chat(ident, "기자들에게 이 이야기를 공개할래")
        self.assertEqual("share", preview["turn"]["proposed_events"][0]["beat"])
        self.assertEqual("private", self.row(ident)["visibility"])
        result = self.confirm(preview, statement="동료와 교류하는 시간을 소중하게 생각합니다.")
        publication = json.dumps(result["item"]["reactions"], ensure_ascii=False)
        for secret in ("비밀선수이름", "감춰둔햄버거", "비밀장소코드"):
            self.assertNotIn(secret, publication)
        self.assertIn("동료와", publication)
        self.assertTrue(result["item"]["reactions"]["media"])
        self.assertEqual("private", self.row(ident)["visibility"])
        private = self.confirm(self.chat(ident, "솔직한 속마음을 말할래"))
        self.assertEqual([], private["item"]["reactions"]["media"])
        self.assertEqual([], private["item"]["reactions"]["boards"])

    def test_promised_home_run_callback_fires_once_without_claiming_fulfillment(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음 홈런 치면 햄버거 사줄게 약속"))
        self.advance_home_run()
        dashboard = self.app.check_save(lambda *_args: None)
        row = self.row(ident)
        self.assertEqual("reminded", row["promise"]["status"])
        self.assertTrue(row["promise"]["fired_by"])
        callbacks = [turn for turn in dashboard["story"]["current"]["turns"] if turn["narrative_provenance"].get("beat") == "callback"]
        self.assertEqual(1, len(callbacks))
        self.assertIn("햄버거", callbacks[0]["scene"]["response"])
        self.assertIn("홈런", callbacks[0]["scene"]["response"])
        self.assertEqual([], callbacks[0]["reactions"]["media"])
        self.assertNotIn("햄버거", json.dumps(dashboard["feed"], ensure_ascii=False))
        before = self.ledger_path.read_bytes()
        self.app.check_save(lambda *_args: None)
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.confirm(self.chat(ident, "약속 지켰어, 햄버거 사줬어"))
        self.assertEqual("fulfilled", self.row(ident)["promise"]["status"])

    def test_cancelled_promise_and_skipped_interval_do_not_fire_callbacks(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음 홈런 때 햄버거 사줄게 약속"))
        self.confirm(self.chat(ident, "그 약속은 취소할래"))
        self.advance_home_run()
        self.app.check_save(lambda *_args: None)
        self.assertEqual("cancelled", self.row(ident)["promise"]["status"])
        self.assertFalse(self.row(ident)["promise"]["fired_by"])
        other = self.start()["interaction_id"]
        self.confirm(self.chat(other, "다음 홈런 때 햄버거 사줄게 약속"))
        self.advance_home_run(day=29)
        self.app.check_save(lambda *_args: None)
        self.assertEqual("pending", self.row(other)["promise"]["status"])

    def test_reserving_after_an_observed_home_run_waits_for_a_new_snapshot(self):
        ident = self.start()["interaction_id"]
        self.advance_home_run()
        self.confirm(self.chat(ident, "다음 홈런 때 햄버거 사줄게 약속"))
        self.app.check_save(lambda *_args: None)
        self.assertEqual("pending", self.row(ident)["promise"]["status"])
        self.advance_home_run(day=26)
        self.app.check_save(lambda *_args: None)
        self.assertEqual("reminded", self.row(ident)["promise"]["status"])

    def test_cancelled_commit_does_not_persist_callback_or_advance_interaction(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음 홈런 치면 햄버거 사줄게 약속"))
        self.advance_home_run()
        before = self.ledger_path.read_bytes()

        @contextlib.contextmanager
        def cancelled():
            raise RuntimeError("synthetic cancellation")
            yield

        def update(*_args):
            pass

        update.commit = cancelled
        with self.assertRaisesRegex(RuntimeError, "synthetic cancellation"):
            self.app.check_save(update)
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_context_guard_and_missing_interaction_fail_without_writing(self):
        self.start()
        for payload in ({"interaction_id": "foreign", "user_text": "좋아"},
                        {"category": "starplayer", "interaction_origin": {"universe_id": "foreign"}, "user_text": "농담할래"}):
            before = self.ledger_path.read_bytes()
            with self.assertRaises(ValueError):
                self.app.chat_story(payload)
            self.assertEqual(before, self.ledger_path.read_bytes())

    def test_deterministic_interaction_never_calls_a_model_even_if_available(self):
        ident = self.start()["interaction_id"]
        with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
            service_module.narration_module, "story_chat", side_effect=AssertionError("no model")
        ):
            response = self.app.chat_story({"interaction_id": ident, "user_text": "농담할래", "renderer_preference": "llm"})
            self.assertEqual("deterministic", response["renderer"])
            self.confirm(response)

    def test_gemini_enriches_expression_but_deterministic_plan_owns_commit(self):
        ident = self.start()["interaction_id"]
        self.config.update({
            "ai_provider": "gemini",
            "gemini_consent": True,
            "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
        })
        secret = Mock()
        secret.get_gemini_key.return_value = ("test-secret", "windows_account")
        remote = service_module.gemini_provider.ProviderResult(
            text="가게 안에는 고소한 향이 번졌다.\n\n두 사람은 햄버거 농담을 주고받으며 긴장을 풀었다.",
            model=self.config["gemini_model"],
            request_hash="expression-hash",
            request_bytes=120,
            response_bytes=80,
            finish_reason="STOP",
        )
        before = copy.deepcopy(self.row(ident))
        with patch.object(self.app, "_secret_store", return_value=secret), patch.object(
            service_module.gemini_provider, "generate_text", return_value=remote
        ) as generate:
            response = self.app.chat_story({
                "interaction_id": ident,
                "user_text": "햄버거 농담을 건네고 싶어",
                "renderer_preference": "gemini",
            })

        self.assertEqual("gemini_expression", response["renderer"])
        self.assertEqual(before, self.row(ident))
        proposal = response["turn"]["proposed_events"][0]
        self.assertEqual("joke", proposal["beat"])
        self.assertIn("provider_expression", proposal)
        self.assertIn("고소한 향", response["response"]["reply"]["text"])
        self.assertNotIn("test-secret", str(response))
        self.assertIn("expression_draft_to_enrich", generate.call_args.args[1])

        committed = self.confirm(response)
        self.assertEqual("gemini_expression", committed["renderer"])
        self.assertEqual(before["revision"] + 1, self.row(ident)["revision"])
        self.assertEqual("joke", self.row(ident)["last_beat"])
        self.assertIn("고소한 향", committed["item"]["scene"]["response"])

    def test_gemini_interaction_failure_uses_local_expression_without_changing_plan(self):
        ident = self.start()["interaction_id"]
        self.config.update({
            "ai_provider": "gemini",
            "gemini_consent": True,
            "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
        })
        secret = Mock()
        secret.get_gemini_key.return_value = ("test-secret", "windows_account")
        provider_error = service_module.gemini_provider.GeminiProviderError(
            "REMOTE_UNAVAILABLE", "upstream-private-detail", retryable=True
        )
        before = copy.deepcopy(self.row(ident))
        with patch.object(self.app, "_secret_store", return_value=secret), patch.object(
            service_module.gemini_provider, "generate_text", side_effect=provider_error
        ), patch.object(
            service_module, "_llm_reachable", return_value=True
        ), patch.object(
            service_module.narration_module,
            "story_chat",
            return_value=(
                "가게 안에 고소한 향이 번졌다.\n\n두 사람은 햄버거 농담을 주고받으며 긴장을 풀었다.",
                "local-test",
            ),
        ):
            response = self.app.chat_story({
                "interaction_id": ident,
                "user_text": "햄버거 농담을 건네고 싶어",
                "renderer_preference": "gemini",
            })

        self.assertEqual("local_llm_expression", response["renderer"])
        self.assertEqual(before, self.row(ident))
        self.assertEqual("local_llm", response["provider_audit"]["provider"])
        self.assertEqual(
            "REMOTE_UNAVAILABLE",
            response["provider_audit"]["fallback_chain"][0]["code"],
        )
        proposal = response["turn"]["proposed_events"][0]
        self.assertEqual("joke", proposal["beat"])
        self.assertEqual("local_llm", proposal["provider_expression"]["provider"])
        self.assertNotIn("upstream-private-detail", str(response))

        committed = self.confirm(response)
        self.assertEqual("local_llm_expression", committed["renderer"])
        self.assertEqual(before["revision"] + 1, self.row(ident)["revision"])
        self.assertEqual("joke", self.row(ident)["last_beat"])
        self.assertIn("고소한 향", committed["item"]["scene"]["response"])

    def test_parallel_starts_are_serialized_without_lost_meetings(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda index: self.start(participant_name=f"가상 선수 {index}")["interaction_id"], range(4)))
        self.assertEqual(4, len(set(ids)))
        state = self.ledger().state
        self.assertEqual(4, len(state[interactions.STORE]))
        self.assertEqual(4, len(state[interactions.EVENTS]))
        self.assertEqual(4, len(state["story_sessions"]["2027-07-24"]["turns"]))

    def test_unrelated_chat_and_prop_commands_keep_existing_routes(self):
        ident = self.start()["interaction_id"]
        response = self.app.chat_story({"user_text": "오늘 감독에게 서운했어", "renderer_preference": "deterministic"})
        self.assertFalse(response["response"]["understanding"]["primary_act"].startswith("interaction_"))
        self.chat(ident, "햄버거 얘기를 앞으로 내부 농담으로 만들자")
        self.assertEqual(1, len(self.ledger().state["narrative_props"]))
        self.assertEqual(1, self.row(ident)["revision"])

    def test_private_refusal_does_not_create_a_publication_or_prop(self):
        ident = self.start()["interaction_id"]
        for text in ("공개하지 마", "기자에게 말하지 말자", "공개 안 할래"):
            response = self.chat(ident, text)
            self.assertEqual([], response["response"]["proposed_events"])
            self.assertIn("범위를 유지", response["response"]["reply"]["text"])
        self.assertEqual({}, self.ledger().state["narrative_props"])
        self.assertEqual(1, self.row(ident)["revision"])

    def test_negative_home_run_condition_does_not_schedule_a_positive_trigger(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음 경기에서 홈런 못 치면 햄버거 사줄게 약속"))
        self.assertIsNone(self.row(ident)["promise"]["trigger_type"])
        self.advance_home_run()
        self.app.check_save(lambda *_args: None)
        self.assertEqual("pending", self.row(ident)["promise"]["status"])

    def test_previous_learning_record_links_without_mutating_its_date_or_prose(self):
        ident = self.start(situation="learning", interaction_topic="가상 커브", learning_progress="registered",
                           interaction_mode="user_confirmed", action_confirmed=True)["interaction_id"]
        before = copy.deepcopy(self.row(ident))
        other = self.start(situation="learning", interaction_topic="가상 커브", learning_progress="acquired",
                           interaction_mode="user_confirmed", action_confirmed=True)
        row = self.row(other["interaction_id"])
        self.assertEqual(ident, row["previous_learning"]["interaction_id"])
        self.assertEqual("registered", row["previous_learning"]["progress"])
        self.assertEqual(before, self.row(ident))
        self.assertIn("러닝 등록 → 오늘 습득 확인", other["item"]["scene"]["response"])

    def test_foreign_interaction_is_rejected_even_for_a_prop_command_or_fact_question(self):
        self.start()
        for text in ("햄버거를 내부 농담으로 만들자", "오늘 경기 결과 알려줘"):
            before = self.ledger_path.read_bytes()
            with self.assertRaises(ValueError):
                self.chat("foreign-world-interaction", text)
            self.assertEqual(before, self.ledger_path.read_bytes())

    def test_deleted_save_preserves_interactions_and_refuses_new_scenes(self):
        ident = self.start()["interaction_id"]
        before = self.ledger_path.read_bytes()
        Path(self.config["save_path"]).unlink()
        for operation in (lambda: self.chat(ident, "좋아"), self.start):
            with self.assertRaises((FileNotFoundError, ValueError, RuntimeError)):
                operation()
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.assertEqual(ident, self.row(ident)["interaction_id"])


if __name__ == "__main__":
    unittest.main()
