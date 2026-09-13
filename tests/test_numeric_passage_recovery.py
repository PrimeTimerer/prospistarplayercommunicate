"""Bounded recovery and private diagnostics, using synthetic model responses."""
import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gemini_provider as gp
import prose_format
import stat_engine
import service as service_module
from tests import test_story_service as story_tests
from tests.test_gemini_provider import _completion, _Response


SOURCE = "검증된 현재 시즌 기록: 136홈런, 499탈삼진."
TARGETS = stat_engine.upcoming_narrative_targets({"bat_HR": 136, "pit_K": 499})
SAFE_A = ("영상실에는 푸른 불빛이 남아 있었다. 선수는 가방을 내려놓고 화면을 다시 돌려보았다. "
          "복도를 지나는 동료의 발소리는 잠시 문 앞에 멈췄다가 다시 멀어졌다. 말이 없어도 서로 무엇을 준비하는지 알 수 있었다.")
SAFE_B = ("숙소로 돌아가는 길에 그는 저녁 메뉴를 떠올렸다. 조금 전까지 붙들고 있던 고민을 내려놓을 시간이 필요했다. "
          "동료가 보낸 짧은 농담을 읽고 웃음이 났다. 내일의 준비는 오늘의 조용한 휴식에서 이어질 것이었다.")
# The formerly brittle coordinated forecast is now a positive discourse test.
# Keep these preservation/cap tests on an actual false achievement instead.
BAD = "140홈런과 500탈삼진이라는 전인미답의 기록을 이미 달성했다."


def draft(bad=BAD):
    return f"## 영상실\n\n{SAFE_A}\n\n## 기록의 무게\n\n{bad}\n\n## 귀갓길\n\n{SAFE_B}"


class NumericPassageRecoveryTests(unittest.TestCase):
    def test_false_target_achievement_is_reproduced_but_remainder_of_story_survives(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp._validate_generated_text(draft(), SOURCE, prospective_targets=TARGETS)
        self.assertEqual(["140", "500"], caught.exception.unsupported_numbers)
        text, audit = gp.recover_narrative_passages(draft(), SOURCE, TARGETS)
        self.assertIn(SAFE_A, text)
        self.assertIn(SAFE_B, text)
        self.assertNotIn("140", text)
        self.assertNotIn("500", text)
        self.assertIn("136홈런", text)
        self.assertIn("499탈삼진", text)
        self.assertEqual(1, audit["changed_paragraphs"])
        self.assertGreater(audit["retained_fraction"], .8)
        gp._validate_generated_text(text, SOURCE, prospective_targets=TARGETS)

    def test_false_achievement_is_replaced_not_reclassified_as_a_forecast(self):
        text, _ = gp.recover_narrative_passages(draft("500탈삼진과 140홈런을 달성해 마침내 축포가 터졌다."), SOURCE, TARGETS)
        self.assertNotIn("달성해", text)
        self.assertNotIn("축포", text)
        self.assertIn("현재 시즌 기록", text)

    def test_wrong_remainder_is_replaced_with_current_facts_not_rounded_numbers(self):
        text, _ = gp.recover_narrative_passages(draft("500탈삼진까지 4개 남았다."), SOURCE + " 4월", TARGETS)
        self.assertNotIn("4개", text)
        self.assertIn("499탈삼진", text)

    def test_non_numeric_prose_remains_byte_equal_after_normalization(self):
        original = draft()
        text, _ = gp.recover_narrative_passages(original, SOURCE, TARGETS)
        expected = prose_format.normalize_generated_markdown(original).replace(
            BAD, "현재 시즌 기록은 499탈삼진 · 136홈런이다. 다음 기록을 향한 이야기는 아직 진행 중이다.")
        self.assertEqual(expected, text)

    def test_wrong_metric_unrelated_units_and_unknown_numbers_are_not_eligible(self):
        for bad in ("500홈런을 달성했다.", "500원짜리 햄버거를 먹었다.", "999승을 달성했다."):
            with self.subTest(bad=bad):
                self.assertIsNone(gp.recover_narrative_passages(draft(bad), SOURCE, TARGETS))

    def test_another_unsupported_paragraph_blocks_whole_publication(self):
        self.assertIsNone(gp.recover_narrative_passages(draft() + "\n\n999승을 기록했다.", SOURCE, TARGETS))

    def test_short_or_predominantly_invalid_draft_is_not_a_model_success(self):
        self.assertIsNone(gp.recover_narrative_passages(BAD, SOURCE, TARGETS))
        self.assertIsNone(gp.recover_narrative_passages(draft(BAD * 12), SOURCE, TARGETS))
        repeated = draft() + ("\n\n" + BAD) * 4
        self.assertIsNone(gp.recover_narrative_passages(repeated, SOURCE, TARGETS))

    def test_size_and_proof_boundaries_are_enforced(self):
        self.assertIsNone(gp.recover_narrative_passages(draft() * 200, SOURCE, TARGETS))
        self.assertIsNone(gp.recover_narrative_passages(draft(), SOURCE, []))

    def test_recovery_is_opt_in_and_consumes_no_third_request(self):
        send = lambda *_a, **_k: _Response(_completion(draft()))
        with self.assertRaises(gp.GeminiProviderError):
            gp.generate_text("한국어로 쓴다", SOURCE, api_key="fixture", prospective_targets=TARGETS, urlopen=send)
        result = gp.generate_text("한국어로 쓴다", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  recover_numeric_passages=True, urlopen=send)
        self.assertEqual(2, result.request_count)
        self.assertEqual(40, result.total_tokens)
        self.assertEqual(1, result.validation_repair["changed_paragraphs"])
        self.assertEqual(draft(), result.validation_draft)
        self.assertNotIn(BAD, repr(result))
        gp._validate_generated_text(result.text, SOURCE, prospective_targets=TARGETS)

    def test_healthy_completion_unchanged_and_no_diagnostic_draft(self):
        text = SAFE_A + "\n\n" + SAFE_B
        result = gp.generate_text("한국어로 쓴다", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  recover_numeric_passages=True, urlopen=lambda *_a, **_k: _Response(_completion(text)))
        self.assertEqual(text, result.text)
        self.assertEqual(1, result.request_count)
        self.assertIsNone(result.validation_repair)
        self.assertFalse(result.validation_draft)

    def test_rejected_draft_is_local_diagnostic_data_with_api_key_redacted(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp.generate_text("한국어로 쓴다", SOURCE, api_key="SENSITIVE_FIXTURE", prospective_targets=TARGETS,
                             recover_numeric_passages=True, urlopen=lambda *_a, **_k: _Response(_completion("500탈삼진을 달성했다. SENSITIVE_FIXTURE")))
        self.assertNotIn("SENSITIVE_FIXTURE", caught.exception.validation_draft)
        self.assertIn("[redacted-key]", caught.exception.validation_draft)
        self.assertNotIn("달성했다", str(gp.public_failure_detail(caught.exception)))

    def test_transport_and_truncated_errors_never_become_recovered_success(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp.generate_text("한국어로 쓴다", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                             recover_numeric_passages=True,
                             urlopen=lambda *_a, **_k: _Response(_completion(draft(), "MAX_TOKENS")))
        self.assertEqual("TRUNCATED_RESPONSE", caught.exception.code)
        self.assertFalse(getattr(caught.exception, "validation_draft", ""))

    def test_oversized_rejected_draft_is_bounded_and_marked_truncated(self):
        oversized = draft() * 200
        self.assertGreater(len(oversized), 32768)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp.generate_text("한국어로 쓴다", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                             recover_numeric_passages=True,
                             urlopen=lambda *_a, **_k: _Response(_completion(oversized)))
        self.assertLessEqual(len(caught.exception.validation_draft), 32768)
        self.assertTrue(caught.exception.validation_draft_truncated)


class NarrativeRecoveryServiceTests(unittest.TestCase):
    def run_fixture(self, temp, provider="gemini", body=None, *, stale=False, cancel=False):
        snap, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(temp)
        snap["stats"].update(pit_K=499, bat_HR=136, bat_AB=200, bat_AVG=.5)
        config.update(ai_provider=provider, gemini_consent=True)
        app = service_module.StarModeService()
        app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
        output = Path(config["output_dir"]) / "worlds" / world["world_id"]
        output.mkdir(parents=True)
        (output / "narrative.md").write_text("以前の物語", encoding="utf-8")
        before = copy.deepcopy(ledger.state)
        original_generate = gp.generate_text
        body = draft() if body is None else body
        secret = Mock()
        secret.get_gemini_key.return_value = ("fixture", "fixture")
        def remote(system, prompt, **kwargs):
            result = original_generate(system, prompt, **kwargs,
                urlopen=lambda *_a, **_k: _Response(_completion(body)))
            if stale:
                snap["content_hash"] = "a-different-current-save"
            return result
        logs = []
        def update(*args):
            logs.append(args)
        @contextlib.contextmanager
        def stop_commit():
            raise RuntimeError("cancelled fixture job")
            yield
        if cancel:
            update.commit = stop_commit
        with patch.object(app, "_secret_store", return_value=secret), \
                patch.object(app, "_ensure_local_llm", return_value=provider != "gemini"), \
                patch.object(gp, "generate_text", side_effect=remote), \
                patch.object(service_module.narration_module, "narrate", return_value=(body, "local-fixture")):
            try:
                result = app.generate_narrative(update)
            except RuntimeError as error:
                result = error
        return result, output, ledger, before, logs, world, snap

    def test_both_writers_publish_repaired_story_and_isolate_original_diagnostic(self):
        for provider in ("gemini", "local_only"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                result, output, ledger, _, logs, world, snap = self.run_fixture(temp, provider)
                self.assertNotIsInstance(result, Exception)
                self.assertFalse(result["run"]["fallback"])
                self.assertIn("기록 문단 1곳 검증 교정", result["run"]["message"])
                self.assertIn(SAFE_A, result["narrative"])
                self.assertNotIn(BAD, result["narrative"])
                self.assertNotIn(BAD, str(ledger.state))
                evidence = json.loads((output / "diagnostics/narrative-validation.latest.json").read_text(encoding="utf-8"))
                self.assertEqual(world["world_id"], evidence["world_id"])
                self.assertEqual(snap["content_hash"], evidence["source_hash"])
                self.assertFalse(evidence["remote_allowed"])
                self.assertEqual(draft(), evidence["records"][0]["draft"])
                self.assertEqual("repaired", evidence["records"][0]["result"])
                self.assertIn("로컬 진단", str(logs))

    def test_failed_recovery_preserves_previous_story_and_diagnostic_is_not_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            result, output, ledger, before, _, _, _ = self.run_fixture(temp, body="500탈삼진을 달성했다.")
            self.assertTrue(result["run"]["provider_failed"])
            self.assertEqual("以前の物語", (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertEqual(before, ledger.state)
            evidence = json.loads((output / "diagnostics/narrative-validation.latest.json").read_text(encoding="utf-8"))
            self.assertEqual("rejected", evidence["records"][0]["result"])
            self.assertNotIn("500탈삼진을 달성했다", str(result))

    def test_stale_or_cancelled_worker_cannot_write_story_or_diagnostic(self):
        for option in ("stale", "cancel"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temp:
                result, output, _, _, _, _, _ = self.run_fixture(temp, **{option: True})
                self.assertIsInstance(result, RuntimeError)
                self.assertFalse((output / "diagnostics").exists())
                self.assertEqual("以前の物語", (output / "narrative.md").read_text(encoding="utf-8"))

    def test_diagnostic_io_failure_does_not_discard_valid_repaired_story(self):
        original_write = service_module.atomic_write_json
        def write(path, value):
            if Path(path).name == "narrative-validation.latest.json":
                raise OSError("fixture diagnostic directory unavailable")
            return original_write(path, value)
        with tempfile.TemporaryDirectory() as temp, patch.object(service_module, "atomic_write_json", side_effect=write):
            result, output, _, _, _, _, _ = self.run_fixture(temp)
            self.assertFalse(result["run"]["fallback"])
            self.assertIn(SAFE_B, (output / "narrative.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
