"""Discourse/typography regressions with synthetic facts, never private drafts."""
import json
import tempfile
import unittest

import gemini_provider as gp
import narrative_context as context
import stat_engine
from tests import test_numeric_passage_recovery as recovery
from tests.test_gemini_provider import _completion, _Response


SOURCE = "검증된 시즌 499탈삼진, 136홈런, 24승. DCInside, FMKorea."
TARGETS = stat_engine.upcoming_narrative_targets({"pit_K": 499, "pit_W": 24, "bat_HR": 136})
FACTS = {"season": {"pit_K": 499, "pit_W": 24, "pit_IP": 216, "pit_H": 2}, "game": {}}


class NarrativeContextTests(unittest.TestCase):
    def validate(self, text):
        gp._validate_generated_text(text, SOURCE, prospective_targets=TARGETS, narrative_metadata=True)

    def test_shared_predicate_binds_both_goals(self):
        for text in ("시즌 500탈삼진과 시즌 25승까지 1개 남았다.",
                     "시즌 25승과 시즌 500탈삼진까지 각각 1개씩만을 남겨두고 있다.",
                     "500탈삼진과 25승이라는 이정표를 향하고 있다.",
                     "140홈런과 500탈삼진이라는 전인미답의 고지를 향한 카운트다운이 시작됐다."):
            with self.subTest(text=text):
                self.validate(text)

    def test_different_remainders_are_mapped_in_order(self):
        self.validate("500탈삼진과 140홈런까지 각각 1개와 4개 남았다.")
        for text in ("500탈삼진과 140홈런까지 각각 4개와 1개 남았다.",
                     "500탈삼진과 140홈런까지 1개 남았다.",
                     "500탈삼진과 25승까지 각각 2개씩 남았다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_already_known_goal_still_participates_in_coordination(self):
        for text in ("다음 이정표인 시즌 500탈삼진과 시즌 25승까지 1개 남았다.",
                     "시즌 25승과 시즌 500탈삼진까지 각각 1개씩만을 남겨두고 있다."):
            with self.subTest(text=text):
                gp._validate_generated_text(text, SOURCE + " 과거 기록 25승.", prospective_targets=TARGETS)

    def test_spacing_and_soft_wraps_keep_the_same_meaning(self):
        for space in ("", " ", "\t", "\n", "\u00a0", "\u3000"):
            with self.subTest(space=repr(space)):
                self.validate(f"500{space}탈{space}삼{space}진과{space}25{space}승까지{space}1개 남았다.")
        self.validate("탈 삼진 500개와 시즌 25승이 다음 목표다.")

    def test_prefix_and_anaphoric_sentence_supply_local_context(self):
        for text in ("다음 목표는 500탈삼진이다.",
                     "다음 목표를 정했다. 500탈삼진이다.",
                     "500탈삼진. 이 목표까지 1개가 남았다.",
                     "## 다음 목표: 500탈삼진\n\n계속 준비한다."):
            with self.subTest(text=text):
                self.validate(text)

    def test_goals_cannot_license_another_speaker_or_completed_sentence(self):
        for text in ("다음 목표를 정했다.\n\n500탈삼진을 기록했다.",
                     "- 다음 목표를 정했다.\n- 500탈삼진을 기록했다.",
                     "500탈삼진을 기록했다. 다음 목표를 정했다.",
                     "500탈삼진과 25승을 이미 달성했다.",
                     "500탈삼진을 향한 기대가 크지만 이미 500탈삼진을 기록했다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_hypothetical_and_negative_are_not_completed_records(self):
        for text in ("500탈삼진을 달성하면 동료들에게 밥을 사겠다고 했다.",
                     "500탈삼진을 달성할 수 있을까? 팬들은 기다렸다.",
                     "500탈삼진을 아직 달성하지 못했다."):
            with self.subTest(text=text):
                self.validate(text)

    def test_dropped_metric_is_resolved_only_from_one_proved_antecedent(self):
        self.validate("시즌 499탈삼진이다. 500개 달성을 앞두고 있다.")
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("식사 이야기를 나눴다. 500개가 목표다.")
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("시즌 499탈삼진이다.\n\n500개를 기록했다.")

    def test_forecast_does_not_license_a_completed_claim(self):
        self.validate("시즌 500탈삼진 달성이 가능성 있는 전망이다.")
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("500탈삼진을 달성했다. 전망대로였다.")

    def test_repair_names_context_not_a_banned_number(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            self.validate("500탈삼진과 25승을 달성했다.")
        prompt = gp._repair_instruction(caught.exception)
        self.assertIn("앞뒤 문장", prompt)
        self.assertNotIn("그 숫자를 쓰지 말고", prompt)
        self.assertIn("목표를 달성 기록", gp.public_failure_detail(caught.exception)["message"])

    def test_timeline_and_url_identifiers_do_not_become_records(self):
        text = "## T+30분, 게시판\n\n[가상 게시글](https://example.invalid/123456789)\n\n500탈삼진과 25승까지 1개 남았다."
        self.validate(text)
        with self.assertRaises(gp.GeminiProviderError):
            self.validate(text + "\n\n123456789홈런을 기록했다.")
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("30이닝을 던졌다.")

    def test_period_scope_flows_from_introduction_but_not_heading_alone(self):
        text = "오늘 경기의 투구 결과를 말씀드립니다. 216이닝, 499탈삼진, 피안타 2입니다."
        issues = context.record_context_issues(text, FACTS)
        self.assertEqual({"pit_IP", "pit_K", "pit_H"}, {row["metrics"][0] for row in issues})
        self.assertFalse(context.record_context_issues("## 오늘 경기 후\n\n시즌 216이닝, 499탈삼진이다.", FACTS))
        self.assertFalse(context.record_context_issues("오늘 발표한 시즌 누적은 499탈삼진이다.", FACTS))
        rows = context.record_context_issues("오늘 경기의 결과를 말씀드립니다. 216이닝이다. 피안타 2다. 시즌 499탈삼진이다.", FACTS)
        self.assertEqual({"pit_IP", "pit_H"}, {row["metrics"][0] for row in rows})

    def test_current_metric_binding_is_not_a_global_number_allowlist(self):
        issues = context.record_context_issues("현재 시즌 216탈삼진을 기록 중이다.", FACTS)
        self.assertEqual("current_metric_mismatch", issues[0]["reason"])
        self.assertFalse(context.record_context_issues("현재 시즌 500탈삼진을 목표로 준비한다.", FACTS))
        self.assertFalse(context.record_context_issues("통산 216탈삼진을 기록했다.", FACTS))

    def test_recollection_and_career_are_not_current_season_totals(self):
        for text in ("지난 시즌 361탈삼진을 기록했다.", "작년 361탈삼진이었다.",
                     "통산 누적 860탈삼진을 기록했다.", "2026년 361탈삼진이었다."):
            with self.subTest(text=text):
                self.assertFalse(context.record_context_issues(text, {**FACTS, "season_year": 2027}))
        self.assertTrue(context.record_context_issues("2027년 361탈삼진을 기록 중이다.", {**FACTS, "season_year": 2027}))

    def test_explicit_inference_is_checked_without_censoring_fan_register(self):
        text = "피안타가 2개라는 것은 상대 타자들이 나오는 동안 안타를 맞지 않았다는 뜻이다."
        self.assertTrue(context.record_context_issues(text, FACTS))
        self.assertFalse(context.record_context_issues("타자들이 공을 맞히질 못하는데 이게 야구냐 시발ㅋㅋ", FACTS))

    def test_unknown_record_fields_are_not_proofs_or_exceptions(self):
        for value in (None, True, "unknown", "NaN", float("nan")):
            with self.subTest(value=value):
                facts = {"season": {"pit_K": value, "pit_H": value}, "game": {}}
                self.assertFalse(context.record_context_issues("현재 시즌 499탈삼진이다.", facts))

    def test_explicit_game_evidence_and_period_switches_survive(self):
        self.assertFalse(context.record_context_issues("오늘 경기에서 499탈삼진을 기록했다.",
                                                      {**FACTS, "game": {"pit_K": 499}}))
        self.assertFalse(context.record_context_issues("오늘 경기 기록을 보자.\n\n시즌 499탈삼진이다.", FACTS))
        self.assertFalse(context.record_context_issues("오늘의 인터뷰였다. 현재 시즌 499탈삼진이다.", FACTS))

    def test_period_conflict_uses_existing_retry_budget(self):
        responses = iter(["오늘 경기에서 499탈삼진을 기록했다.", "현재 시즌 499탈삼진을 기록 중이다."])
        calls = []
        def send(request, timeout):
            calls.append(json.loads(request.data))
            return _Response(_completion(next(responses)))
        result = gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  narrative_facts=FACTS, recover_numeric_passages=True, urlopen=send)
        self.assertEqual(2, result.request_count)
        self.assertIn("시즌 누적", str(calls[1]))
        self.assertEqual("현재 시즌 499탈삼진을 기록 중이다.", result.text)

    def test_shared_target_story_needs_no_retry_or_paragraph_replacement(self):
        text = recovery.draft("500탈삼진과 25승까지 1개 남았다.")
        result = gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  recover_numeric_passages=True, urlopen=lambda *_a, **_k: _Response(_completion(text)))
        self.assertEqual(1, result.request_count)
        self.assertIsNone(result.validation_repair)
        self.assertEqual(text, result.text)

    def test_layout_is_normalized_before_validation_without_another_request(self):
        text = "**1. 다음 장면.** 500\u00a0탈삼진과 25승까지 1개 남았다.다음 경기를 준비했다."
        result = gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  recover_numeric_passages=True, urlopen=lambda *_a, **_k: _Response(_completion(text)))
        self.assertEqual(1, result.request_count)
        self.assertIn("## 1. 다음 장면.\n\n500 탈삼진", result.text)
        self.assertIn("남았다. 다음", result.text)

    def test_meaningful_paragraph_boundary_is_not_removed_for_acceptance(self):
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("500탈삼진.\n\n이 목표까지 1개 남았다.")

    def test_only_sequential_heading_ordinals_are_typography(self):
        self.validate("## 1. 준비\n\n몸을 푼다.\n\n## 2. 저녁\n\n식사를 한다.")
        with self.assertRaises(gp.GeminiProviderError):
            self.validate("## 73. 홈런 기록\n\n73홈런을 기록했다.")

    def test_both_writers_check_record_period_and_preserve_other_scenes(self):
        text = recovery.draft("오늘 경기에서 499탈삼진을 기록했다.")
        for provider in ("gemini", "local_only"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                result, _, _, _, _, _, _ = recovery.NarrativeRecoveryServiceTests().run_fixture(temp, provider, text)
                self.assertNotIsInstance(result, Exception)
                self.assertFalse(result["run"]["fallback"])
                self.assertNotIn("오늘 경기에서 499탈삼진", result["narrative"])
                self.assertIn(recovery.SAFE_A, result["narrative"])
                self.assertIn(recovery.SAFE_B, result["narrative"])


if __name__ == "__main__":
    unittest.main()
