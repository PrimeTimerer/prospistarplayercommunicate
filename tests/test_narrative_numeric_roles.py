"""Numeric roles from the 2.7.4 refusal, without personal diagnostic fixtures."""
import tempfile
import unittest

import gemini_provider as gp
import stat_engine
from tests import test_numeric_passage_recovery as recovery_tests
from tests.test_gemini_provider import _completion, _Response
from tests.test_numeric_passage_recovery import SAFE_A, SAFE_B, draft


SOURCE = "현재 시즌 499탈삼진, 136홈런, 24승. 1개, 4개. 요청 커뮤니티: DCInside, FMKorea."
TARGETS = stat_engine.upcoming_narrative_targets({"pit_K": 499, "bat_HR": 136, "pit_W": 24})


class NarrativeNumericRoleTests(unittest.TestCase):
    def validate(self, text, source=SOURCE, targets=TARGETS):
        gp._validate_generated_text(text, source, prospective_targets=targets)

    def generate(self, text, source=SOURCE, *, cinematic=True):
        return gp.generate_text("한국어 서사", source, api_key="fixture", prospective_targets=TARGETS,
            recover_numeric_passages=cinematic,
            urlopen=lambda *_a, **_k: _Response(_completion(text)))

    def test_observed_reverse_metric_and_conditional_remain_original_prose(self):
        for text in ("시즌 탈삼진 500개 눈앞인데 다음 등판이 기대된다.",
                     "다음 등판 때 시즌 500탈삼진 채우면 인터뷰 할려나.",
                     "시즌 140홈런까지 4개 남았고 시즌 탈삼진 500개 눈앞인데.",
                     "시즌 탈삼진 500개까지 딱 1개 남았다."):
            with self.subTest(text=text):
                result = self.generate(text)
                self.assertEqual(text, result.text)
                self.assertEqual(1, result.request_count)
                self.assertIsNone(result.validation_repair)

    def test_both_orders_share_metric_and_remaining_proofs(self):
        for stats in ({"pit_K": 549}, {"bat_HR": 149}, {"bat_SB": 179},
                      {"bat_H": 299}, {"bat_RBI": 349}, {"pit_W": 29}):
            row = stat_engine.upcoming_narrative_targets(stats)[0]
            source = f"현재 시즌 {row['current']}{row['label']}"
            for text in (f"{row['target']}{row['label']}까지 단 1개 남았다.",
                         f"{row['label']} {row['target']}개까지 딱 1개 남았다."):
                with self.subTest(stats=stats, text=text):
                    self.validate(text, source, [row])

    def test_thousands_markdown_and_inverted_quantity(self):
        rows = stat_engine.upcoming_narrative_targets({"pit_K": 999})
        self.validate("시즌 **탈삼진 1,000개**까지 1개 남았다.", "현재 999탈삼진", rows)

    def test_conditional_does_not_license_a_later_completed_claim(self):
        for text in ("시즌 500탈삼진을 채우면 좋겠지만 이미 500탈삼진을 기록했다.",
                     "탈삼진 500개를 향한 목표를 달성했다.", "시즌 탈삼진 500개를 기록했다.",
                     "시즌 탈삼진 500개 달성의 순간이었다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_exact_gap_with_colloquial_emphasis_not_bypassed_by_allowed_number(self):
        for text in ("500탈삼진까지 딱 4개 남았다.", "탈삼진 500개까지 딱 4개 남았다.",
                     "500탈삼진까지 정확히 4개 남았다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_inverted_target_does_not_license_different_units_or_signed_values(self):
        for text in ("홈런 500개까지 1개 남았다.", "탈삼진 -500개 눈앞에 있다.",
                     "탈삼진 1500개가 목표다.", "탈삼진 500.5개가 목표다.",
                     "탈삼진 500명까지 1개 남았다.", "탈삼진 500원까지 1개 남았다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_recovery_uses_the_same_inverted_metric_binding(self):
        text, audit = gp.recover_narrative_passages(draft("시즌 탈삼진 500개를 달성했다."), SOURCE, TARGETS)
        self.assertNotIn("500", text)
        self.assertIn("499탈삼진", text)
        self.assertIn(SAFE_A, text)
        self.assertEqual(1, audit["changed_paragraphs"])

    def test_forum_header_is_display_metadata_not_a_record_number(self):
        text = "ㅇㅇ (192.0)\n시즌 탈삼진 500개 눈앞인데 다음 경기가 기대된다."
        result = self.generate(text)
        self.assertEqual(text, result.text)
        self.assertEqual(1, result.request_count)
        self.assertIsNone(result.validation_repair)
        self.assertFalse(result.validation_draft)

    def test_forum_header_handling_is_cinematic_and_requested_forum_only(self):
        text = "ㅇㅇ (192.0)\n다음 등판을 기다린다."
        with self.assertRaises(gp.GeminiProviderError):
            self.generate(text, cinematic=False)
        with self.assertRaises(gp.GeminiProviderError):
            self.generate(text, source="현재 시즌 499탈삼진")

    def test_display_tag_never_enters_the_global_number_allowlist(self):
        text = "ㅇㅇ (192.0)\n시즌 192.0홈런을 기록했다."
        with self.assertRaises(gp.GeminiProviderError):
            self.generate(text)

    def test_statistics_and_non_headers_cannot_disguise_numbers_as_display_tags(self):
        for text in ("ERA (2.35)\n기록을 보자.", "승수 (192.0)\n기록을 보자.",
                     "탈삼진 (192.0)\n기록을 보자.", "이닝 (192.0)\n기록을 보자.",
                     "ㅇㅇ (999.36)\n다음 등판이 기대된다.", "ㅇㅇ (192.0.2.1)\n기대된다.",
                     "ㅇㅇ (192.0) 다음 등판이 기대된다.", "ㅇㅇ (192.0)"):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.generate(text)

    def test_mixed_forum_narrative_completes_without_a_rewrite(self):
        text = (f"## 기록의 다음 장\n\n{SAFE_A}\n\n"
                "ㅇㅇ (192.0)\n시즌 500탈삼진까지 딱 1개 남았다.\n\n"
                "조용한관중 (198.51)\n시즌 140홈런까지 4개 남았고 시즌 탈삼진 500개 눈앞인데.\n\n"
                "[DCInside 야구갤러리]\n밤의타석 (203.0)\n다음 등판 때 시즌 500탈삼진 채우면 인터뷰 할려나.\n\n"
                f"## 다음 아침\n\n{SAFE_B}")
        result = self.generate(text)
        self.assertEqual(text, result.text)
        self.assertEqual(1, result.request_count)
        self.assertEqual(20, result.total_tokens)
        self.assertIsNone(result.validation_repair)

    def test_both_service_writers_keep_a_valid_inverted_goal_and_forum_text(self):
        text = (f"{SAFE_A}\n\nㅇㅇ (192.0)\n시즌 탈삼진 500개 눈앞인데 다음 등판이 기대된다."
                f"\n\n다음 등판 때 시즌 500탈삼진 채우면 인터뷰 할려나.\n\n{SAFE_B}")
        for provider in ("gemini", "local_only"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                result, output, _, _, _, _, _ = recovery_tests.NarrativeRecoveryServiceTests().run_fixture(temp, provider, text)
                self.assertNotIsInstance(result, Exception)
                self.assertFalse(result["run"]["fallback"])
                self.assertEqual(text, result["narrative"])
                self.assertNotIn("validation_repair", result["run"]["provider_audit"])
                self.assertFalse((output / "diagnostics/narrative-validation.latest.json").exists())


if __name__ == "__main__":
    unittest.main()
