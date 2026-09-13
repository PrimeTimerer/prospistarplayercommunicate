"""Bounded language fixtures and real isolated-service mutation guards."""

import copy
import json
import unicodedata
import unittest
from unittest.mock import patch

import interaction_intent as intent
import star_interactions as interactions
import service as service_module

try:
    from tests import test_star_interactions as fixtures
except ImportError:
    import test_star_interactions as fixtures


POSITIVE_CASES = [
    ("농담을 건네고 싶어", "joke"), ("햄버거 값은 삼진으로 계산하자 ㅋㅋ", "joke"),
    ("웃긴 얘기를 해 볼게", "joke"), ("그건 싫어", "refuse"), ("안 할래", "refuse"),
    ("농담은 하지 마", "refuse"), ("솔직히 요즘은 좀 불안해", "confide"),
    ("내가 잘못했어. 미안해", "reconcile"), ("그 생각에는 반대야", "disagree"),
    ("동의하지 않아", "disagree"), ("좋아, 더 이야기하자", "agree"), ("더 이야기하자", "continue"),
    ("다음 홈런 치면 햄버거 사줄게 약속", "promise"), ("그 약속은 취소할래", "change"),
    ("약속 지켰어, 햄버거 사줬어", "fulfill"), ("오늘 대화는 여기서 마무리하자", "close"),
    ("잠시 멈추자", "pause"), ("다시 이어가자", "resume"), ("기자에게 한마디 말할래", "share"),
    ("좋아, 농담을 해 보자", "joke"),
]
COMPOSITE_CASES = [
    ("농담은 하지 말고 걱정을 들어줘", "confide", "joke"),
    ("사과는 하지 않고 농담을 건넬래", "joke", "reconcile"),
    ("약속을 취소하지 말고 솔직한 속마음을 말할래", "confide", "change"),
    ("기자에게 말하지 말고 농담하자", "joke", "share"),
    ("농담 말고 속마음을 말하자", "confide", "joke"),
    ("반대는 하지 않고 사과할래", "reconcile", "disagree"),
    ("새 약속은 안 할래. 대신 걱정을 말할래", "confide", "promise"),
    ("소재로 만들지 말고 농담만 해줘", "joke", "prop"),
    ("대화는 끝내지 마. 걱정을 말할래", "confide", "close"),
    ("농담은 하지 않아\n속마음을 말할래", "confide", "joke"),
]
NO_ACTION_CASES = [
    "약속은 취소하지 마", "약속을 지키지 못했어", "아직 약속을 이행하지 않았어",
    "사과는 하지 않을래", "화해할 생각은 없어", "반대하지 않아", "거절하지 마",
    "기자에게 공개하지 마", "공개 안 할래", "기자에게 말하지 말자",
    "만약 약속을 취소하면 어떻게 돼?", "약속을 취소할까 말까", "약속을 지켰다고 했어",
    '기자가 "약속을 취소해"라고 말했어', '"약속 취소"라는 문구를 봤어',
    "농담도 하고 속마음도 말할래", "화해하자. 그리고 약속하자", "공개하지 마. 공개해",
    "동의하지 않는 건 아니야", "반대하지 않는 건 아니야", "그 소재로 만들지 마",
    "농담할래. 농담하지 마", "약속을 취소할까?", '"농담하자',
    "약속을 취소하지는 마", "약속은 취소안해", "반대하면 안 돼", "농담은 금지",
]


class IntentAnalysisTests(unittest.TestCase):
    def test_established_single_intents_remain_supported(self):
        for text, expected in POSITIVE_CASES:
            with self.subTest(text=text):
                result = intent.analyze(text)
                self.assertEqual(("resolved", expected), (result["status"], result["beat"]))

    def test_negative_clause_is_excluded_before_selecting_the_other_action(self):
        for text, expected, denied in COMPOSITE_CASES:
            with self.subTest(text=text):
                result = intent.analyze(text)
                self.assertEqual(("resolved", expected), (result["status"], result["beat"]))
                self.assertIn(denied, [row["beat"] for row in result["excluded"]])
                self.assertNotEqual(text, result["selected_text"])

    def test_ambiguous_reported_hypothetical_and_negative_intents_do_not_act(self):
        for text in NO_ACTION_CASES:
            with self.subTest(text=text):
                self.assertIsNone(intent.analyze(text)["beat"])

    def test_productive_negative_suffix_matrix(self):
        for word, denied in (("농담", "joke"), ("사과", "reconcile"), ("화해", "reconcile"),
                             ("반대", "disagree"), ("거절", "refuse"), ("공개", "share")):
            for ending in ("하지 마", "하지 않을래", "하지 않았어", "는 안 할래", "할 생각은 없어", "하고 싶지 않아"):
                text = word + ending
                with self.subTest(text=text):
                    self.assertNotEqual(denied, intent.analyze(text)["beat"])

    def test_negation_does_not_match_inside_affirmative_words(self):
        for text, expected in (("불안해", "confide"), ("내가 잘못했어", "reconcile"),
                               ("그 제안에 동의해", None), ("농담안해", None), ("사과안할래", None)):
            with self.subTest(text=text):
                self.assertEqual(expected, intent.analyze(text)["beat"])

    def test_multiple_effectful_actions_are_not_priority_sorted_into_one(self):
        for text in ("농담하고 약속하자", "사과하고 속마음을 말할래", "약속을 지켰고 화해하자"):
            with self.subTest(text=text):
                result = intent.analyze(text)
                self.assertEqual("clarify", result["status"])
                self.assertIsNone(result["beat"])
                self.assertIn("multiple_actions", result["reasons"])

    def test_recall_and_past_question_are_read_only(self):
        for text in ("지난 약속이 뭐였지?", "무슨 약속이었지", "약속 지켰어?", "내가 햄버거 사줬어?"):
            with self.subTest(text=text):
                self.assertEqual("recall", intent.analyze(text)["status"])

    def test_quote_is_data_unless_an_outside_action_is_explicit(self):
        for text in ('"공개해"라는 말을 봤어', '「약속 취소」라고 써 있어', '“약속 지켰어”라고 했어'):
            with self.subTest(text=text):
                self.assertIsNone(intent.analyze(text)["beat"])
        result = intent.analyze('"햄버거 값은 삼진으로 계산할까?" 하고 농담한다')
        self.assertEqual("joke", result["beat"])
        self.assertTrue(result["allow_literal_quote"])

    def test_forbidden_quote_is_not_literal_dialogue(self):
        result = intent.analyze('"약속을 취소하자"라는 농담은 하지 마')
        self.assertEqual("refuse", result["beat"])
        self.assertFalse(result["allow_literal_quote"])

    def test_privacy_does_not_prevent_private_conversation(self):
        for text in ("기자에게 말하지 말고 농담하자", "비밀로 하고 농담하자", "우리끼리 농담하자"):
            with self.subTest(text=text):
                result = intent.analyze(text)
                self.assertEqual("joke", result["beat"])
                self.assertTrue(result["keep_private"])
        self.assertIsNone(intent.analyze("우리끼리. 기자에게 공개해")["beat"])

    def test_condition_negation_does_not_reverse_promise_polarity(self):
        for text in ("다음 홈런 못 치면 햄버거 사줄게 약속", "다음 홈런 치면 햄버거 사줄게 약속"):
            with self.subTest(text=text):
                self.assertEqual("promise", intent.analyze(text)["beat"])
        self.assertIsNone(intent.analyze("다음 홈런 치면 햄버거 사줄게 약속하지 마")["beat"])

    def test_parser_is_deterministic_and_keeps_inspectable_spans(self):
        text = "농담은 하지 말고 걱정을 들어줘"
        first = intent.analyze(text)
        self.assertEqual(first, intent.analyze(text))
        for row in first["clauses"]:
            self.assertEqual(row["text"], first["text"][slice(*row["span"])].strip())
        self.assertEqual(text, first["text"])

    def test_unicode_and_clause_bounds_do_not_bypass_negation(self):
        text = "농담하지 말고 걱정을 말할래"
        self.assertEqual("confide", intent.analyze(unicodedata.normalize("NFD", text))["beat"])
        self.assertEqual("confide", intent.analyze(text.replace("하지", "하\u200b지"))["beat"])
        self.assertIsNone(intent.analyze("농담하자. " * 25)["beat"])

    def test_positive_prop_keeps_its_separate_owner_and_negative_prop_is_held(self):
        self.assertFalse(intent.prop_needs_review("햄버거 얘기를 앞으로 내부 농담으로 만들자"))
        self.assertFalse(intent.prop_needs_review("그 소재를 공개하지 마"))
        for text in ("햄버거를 소재로 만들지 마", "이걸 기억하지 마", "소재로 만들자, 그리고 약속하자"):
            with self.subTest(text=text):
                self.assertTrue(intent.prop_needs_review(text))

    def test_recall_combined_with_an_action_needs_explicit_selection(self):
        for text in ("지난 약속이 뭐였지? 그리고 농담하자", "약속 지켰어? 그리고 공개해"):
            with self.subTest(text=text):
                result = intent.analyze(text)
                self.assertEqual("clarify", result["status"])
                self.assertIn("recall", result["candidates"])
                self.assertIn("multiple_actions", result["reasons"])

    def test_adversative_suffix_preserves_negation_on_its_own_clause(self):
        result = intent.analyze("아직 약속을 이행하지 않았지만 농담은 할래")
        self.assertEqual("joke", result["beat"])
        self.assertEqual("농담은 할래", result["selected_text"])
        self.assertIn("fulfill", [row["beat"] for row in result["excluded"]])

    def test_keeping_a_promise_is_not_creating_a_replacement(self):
        for text in ("약속은 그대로 두자", "기존 약속을 유지하자"):
            with self.subTest(text=text):
                self.assertEqual("keep", intent.analyze(text)["status"])
                self.assertIsNone(intent.analyze(text)["beat"])
        self.assertEqual("joke", intent.analyze("기존 약속은 유지하고 농담하자")["beat"])


class IntentServiceTests(unittest.TestCase):
    setUp = fixtures.InteractionServiceTests.setUp
    ledger = fixtures.InteractionServiceTests.ledger
    start = fixtures.InteractionServiceTests.start
    chat = fixtures.InteractionServiceTests.chat
    confirm = fixtures.InteractionServiceTests.confirm
    row = fixtures.InteractionServiceTests.row
    advance_home_run = fixtures.InteractionServiceTests.advance_home_run

    def assert_no_scene_change(self, before):
        after = self.ledger().state
        for key in (interactions.STORE, interactions.EVENTS, "relationship_edges", "world_events", "narrative_props", "community_memory"):
            self.assertEqual(before.get(key), after.get(key), key)

    def test_pending_promise_is_not_cancelled_or_fulfilled_by_negation_or_question(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "다음 홈런 치면 햄버거 사줄게 약속"))
        for text in ("약속을 취소하지 마", "약속을 이행하지 않았어", "약속 지켰어?", "약속을 취소하면 어떻게 돼?", "약속은 그대로 두자"):
            with self.subTest(text=text):
                before = self.ledger().state
                response = self.chat(ident, text)
                self.assertEqual([], response["turn"]["proposed_events"])
                self.assert_no_scene_change(before)
                self.assertEqual("pending", self.row(ident)["promise"]["status"])

    def test_private_composite_commits_only_selected_clause_and_restarts(self):
        ident = self.start()["interaction_id"]
        before = self.ledger().state
        response = self.chat(ident, "기자에게 말하지 말고 농담하자")
        self.assert_no_scene_change(before)
        proposal = response["turn"]["proposed_events"][0]
        self.assertEqual("joke", proposal["beat"])
        self.assertEqual("농담하자", proposal["line"])
        saved = copy.deepcopy(proposal["intent_analysis"])
        self.app = service_module.StarModeService()
        result = self.confirm(response)
        self.assertEqual(response["response"]["reply"]["blocks"][1:], result["item"]["scene"]["blocks"])
        self.assertEqual([], result["item"]["reactions"]["media"])
        self.assertEqual("private", self.row(ident)["visibility"])
        self.assertEqual(saved, self.ledger().state[interactions.EVENTS][-1]["intent_analysis"])

    def test_declined_reconciliation_does_not_erase_tension(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "그 생각에는 반대야"))
        before = self.ledger().state
        response = self.chat(ident, "사과는 하지 않을래")
        self.assertEqual([], response["turn"]["proposed_events"])
        self.assert_no_scene_change(before)
        self.assertGreater(self.row(ident)["emotional_continuity"]["tension"], 0)

    def test_ambiguity_does_not_batch_apply_or_offer_an_arbitrary_first_action(self):
        ident = self.start()["interaction_id"]
        before = self.ledger().state
        response = self.chat(ident, "농담도 하고 약속도 할래")
        self.assertEqual([], response["turn"]["proposed_events"])
        self.assertIn("먼저", response["response"]["reply"]["text"])
        self.assert_no_scene_change(before)
        self.confirm(self.chat(ident, "농담을 건넬래"))
        self.assertIsNone(self.row(ident)["promise"])

    def test_negated_material_command_does_not_bypass_activity_parser(self):
        ident = self.start()["interaction_id"]
        for text in ("햄버거를 소재로 만들지 마", "이걸 기억하지 마", "소재로 만들자, 그리고 약속하자"):
            with self.subTest(text=text):
                before = self.ledger().state
                response = self.app.chat_story({"interaction_id": ident, "user_text": text, "confirm": True})
                self.assertEqual([], response["turn"]["proposed_events"])
                self.assert_no_scene_change(before)

    def test_negation_and_quotes_cannot_seed_a_new_unwanted_encounter(self):
        for text in ("만남을 시작하지 마", "농담도 하고 약속도 할래", '기자가 "공개해"라고 했어'):
            with self.subTest(text=text):
                before = self.ledger().state
                response = self.app.chat_story({"category": "starplayer", "situation": "player_exchange", "target": "rookie", "user_text": text})
                self.assertEqual([], response["turn"]["proposed_events"])
                self.assert_no_scene_change(before)
                self.assertTrue(all(not row.get("interaction_id") for row in response["turn"]["choices"]))

    def test_forbidden_quoted_words_do_not_become_player_speech(self):
        ident = self.start()["interaction_id"]
        response = self.chat(ident, '"약속을 취소하자"라는 농담은 하지 마')
        result = self.confirm(response)
        self.assertFalse(any(block.get("speaker") == self.snap["player"]["name"] for block in result["item"]["scene"]["blocks"]))

    def test_interpretation_is_bound_and_legacy_proposals_remain_readable(self):
        ident = self.start()["interaction_id"]
        response = self.chat(ident, "농담은 하지 말고 걱정을 들어줘")
        proposal = response["turn"]["proposed_events"][0]
        snapshot = copy.deepcopy(self.snap)
        for key, value in (("line", "약속을 취소해"), ("beat", "change")):
            bad = dict(proposal, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                interactions.validate_plan(self.ledger().state, bad, universe_id=self.world_id,
                                           protagonist_id=self.snap["player"]["id"], game_date="2027-07-24")
        old = copy.deepcopy(proposal)
        old.pop("intent_analysis")
        interactions.validate_plan(self.ledger().state, old, universe_id=self.world_id,
                                   protagonist_id=self.snap["player"]["id"], game_date="2027-07-24")
        self.assertEqual(snapshot, self.snap)
        for invalid in ([], {"text": []}, {"text": 1}, {"text": "x" * 1201}):
            with self.subTest(interpretation=invalid), self.assertRaises(ValueError):
                interactions.validate_plan(self.ledger().state, dict(proposal, intent_analysis=invalid), universe_id=self.world_id,
                                           protagonist_id=self.snap["player"]["id"], game_date="2027-07-24")

    def test_new_interpretations_keep_stale_confirmation_guard(self):
        ident = self.start()["interaction_id"]
        pending = self.chat(ident, "농담은 하지 말고 걱정을 들어줘")
        self.confirm(self.chat(ident, "그건 싫어"))
        before = self.ledger_path.read_bytes()
        with self.assertRaises(ValueError):
            self.confirm(pending)
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_offline_path_does_not_call_a_model_and_old_capsule_stays_exact(self):
        ident = self.start()["interaction_id"]
        self.advance_home_run()
        self.app.check_save(lambda *_args: None)
        before = copy.deepcopy(self.ledger().state["story_sessions"]["2027-07-24"])
        with patch.object(service_module.narration_module, "story_chat", side_effect=AssertionError("no model")):
            response = self.app.chat_story({"interaction_id": ident, "user_text": "농담은 하지 말고 걱정을 들어줘", "renderer_preference": "llm"})
            self.confirm(response)
        self.assertEqual(before, self.ledger().state["story_sessions"]["2027-07-24"])
