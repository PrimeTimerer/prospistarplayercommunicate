"""Offline regressions for a current 499 count versus an unachieved 500 target."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gemini_provider as gp
import stat_engine
import service as service_module
from tests import test_story_service as story_tests
from tests.test_gemini_provider import _completion, _Response


SOURCE = "검증된 시즌 499탈삼진, 2승. 아직 시즌은 진행 중이다."
TARGETS = stat_engine.upcoming_narrative_targets({"pit_K": 499})


class TargetProjectionTests(unittest.TestCase):
    def test_extends_existing_series_without_awarding_future_milestones(self):
        stats = {"pit_K": 499, "bat_HR": 139, "bat_H": 299, "pit_W": 29, "bat_SB": 139, "bat_RBI": 299}
        before = copy.deepcopy(stats)
        rows = stat_engine.upcoming_narrative_targets(stats)
        self.assertEqual([500, 30, 140, 140, 300, 300], [row["target"] for row in rows])
        self.assertTrue(all(row["remaining"] == 1 and row["scope"] == "season" for row in rows))
        self.assertEqual(before, stats)
        self.assertNotIn(("탈삼진", "시즌 500탈삼진 돌파"), stat_engine.new_milestones({}, stats))

    def test_does_not_invent_targets_for_rates_missing_counts_or_distant_marks(self):
        for value in (None, True, False, "499", -1, 0, 499.0, 451, 493):
            with self.subTest(value=value):
                self.assertEqual([], stat_engine.upcoming_narrative_targets({"pit_K": value}))
        self.assertEqual([], stat_engine.upcoming_narrative_targets({"bat_AVG": .499, "pit_IP": 499}))
        self.assertEqual(500, stat_engine.upcoming_narrative_targets({"pit_K": 495})[0]["target"])

    def test_exact_threshold_is_not_its_own_future_target(self):
        self.assertEqual([], stat_engine.upcoming_narrative_targets({"pit_K": 500}))
        self.assertEqual(550, stat_engine.upcoming_narrative_targets({"pit_K": 549})[0]["target"])

    def test_invalid_or_duplicate_proofs_are_rejected(self):
        self.assertEqual(TARGETS, stat_engine.validate_narrative_targets(TARGETS))
        for field, value in (("target", 999), ("remaining", 2), ("remaining", True), ("label", "홈런"), ("scope", "career")):
            altered = {**TARGETS[0], field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                stat_engine.validate_narrative_targets([altered])
        with self.assertRaises(ValueError):
            stat_engine.validate_narrative_targets(TARGETS * 2)


class ProspectiveValidationTests(unittest.TestCase):
    def validate(self, text, source=SOURCE):
        gp._validate_generated_text(text, source, prospective_targets=TARGETS)

    def test_reproduces_old_gate_without_prospective_context(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp._validate_generated_text("시즌 500탈삼진까지 1개 남았다.", SOURCE)
        self.assertIn("500", caught.exception.unsupported_numbers)

    def test_explicit_targets_and_exact_remaining_counts_pass(self):
        cases = (
            "시즌 500탈삼진까지 1개 남았다.",
            "시즌 **500탈삼진**까지 단 한 개만 남겨두고 있다.",
            "시즌500탈삼진을 눈앞에 두었다.",
            "500탈삼진이라는 목표를 향해 준비했다.",
            "500탈삼진 달성을 앞두고 있다.",
            "500탈삼진 돌파를 앞두고 있다.",
            "500탈삼진 달성까지 단 1개가 남아 있다.",
            "500탈삼진에 도전한다.",
            "500번째 탈삼진을 기다리는 팬들이 모였다.",
            "## 다음 목표\n\n500탈삼진 고지까지 1개가 남았다. 다음 경기를 준비했다.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.validate(text)

    def test_achieved_and_ambiguous_target_claims_stay_blocked(self):
        cases = (
            "시즌 500탈삼진을 기록했다.",
            "시즌 500탈삼진까지 달성했다.",
            "500탈삼진을 달성했다. 다음 목표를 준비한다.",
            "500탈삼진 달성의 순간이었다.",
            "500탈삼진이라는 목표를 달성했다.",
            "500탈삼진을 향한 기대가 컸고 결국 500탈삼진을 돌파했다.",
            "500탈삼진까지 1개가 남았다. 오늘 500탈삼진을 달성했다.",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_proof_cannot_license_different_metric_or_unrelated_numbers(self):
        for text in ("시즌 500홈런까지 1개 남았다.", "500승이 목표다.",
                     "500탈삼진까지 1개 남았지만 999승을 거뒀다.", "500명이 모였다.",
                     "-500탈삼진까지 1개 남았다.", "1500탈삼진까지 1개 남았다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_wrong_remaining_count_rejected_even_if_number_in_original_input(self):
        for text in ("500탈삼진까지 2개 남았다.", "500탈삼진까지 단 두 개만 남겨뒀다.",
                     "500탈삼진까지 2탈삼진만 남았다.", "500탈삼진 달성까지 2개 남았다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                self.validate(text)

    def test_historical_number_already_in_original_source_keeps_existing_semantics(self):
        self.validate("지난 시즌 500탈삼진을 기록했다.", SOURCE + " 지난 시즌 500탈삼진.")

    def test_higher_targets_and_formatted_thousands_use_same_arithmetic_rule(self):
        rows = stat_engine.upcoming_narrative_targets({"pit_K": 999, "bat_HR": 149})
        gp._validate_generated_text("1,000탈삼진까지 1개 남았다. 150홈런까지 1개가 남았다.",
                                    "시즌 999탈삼진, 149홈런", prospective_targets=rows)

    def test_healthy_target_completes_in_one_request_without_general_number_allowance(self):
        requests = []
        text = "시즌 500탈삼진까지 1개 남았다. 조용히 내일을 준비했다."
        def send(request, timeout):
            requests.append(json.loads(request.data))
            return _Response(_completion(text))
        result = gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS, urlopen=send)
        self.assertEqual(text, result.text)
        self.assertEqual(1, result.request_count)
        self.assertIn("아직 달성한 기록이 아님", requests[0]["contents"][0]["parts"][0]["text"])
        self.assertEqual(20, result.total_tokens)

    def test_invalid_achievement_is_repaired_with_progress_within_existing_budget(self):
        responses = iter([_completion("500탈삼진을 달성했다."), _completion("500탈삼진까지 1개 남았다.")])
        events = []
        result = gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                                  urlopen=lambda *_a, **_k: _Response(next(responses)), on_validation_retry=events.append)
        self.assertEqual(2, result.request_count)
        self.assertEqual(40, result.total_tokens)
        self.assertEqual(1, len(events))
        self.assertEqual((1, 1), (events[0]["retry"], events[0]["max_retries"]))
        self.assertEqual(["500"], events[0]["unsupported_numbers"])

    def test_prompt_target_does_not_license_false_achievement_even_after_retry(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp.generate_text("한국어 서사", SOURCE, api_key="fixture", prospective_targets=TARGETS,
                             urlopen=lambda *_a, **_k: _Response(_completion("500탈삼진을 달성했다.")))
        self.assertEqual(2, caught.exception.request_count)
        self.assertIn("500", caught.exception.unsupported_numbers)


class NarrativeTargetServiceTests(unittest.TestCase):
    def test_local_invalid_achievement_preserves_existing_text_and_reports_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(temp)
            event["snapshot"]["stats"]["pit_K"] = 499
            config["ai_provider"] = "local_only"
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            output = Path(config["output_dir"]) / "worlds" / world["world_id"]
            output.mkdir(parents=True)
            path = output / "narrative.md"
            path.write_text("이전 서사", encoding="utf-8")
            before = copy.deepcopy(ledger.state)
            with patch.object(app, "_ensure_local_llm", return_value=True), \
                    patch.object(service_module.narration_module, "narrate", return_value=("500탈삼진을 달성했다.", "local-fixture")), \
                    patch.object(service_module.presentation, "render_built_in_narrative") as render:
                with self.assertRaisesRegex(RuntimeError, "로컬 서사 검증") as caught:
                    app.generate_narrative(lambda *_args: None)
            self.assertNotIn("응답하지 않았", str(caught.exception))
            self.assertEqual("이전 서사", path.read_text(encoding="utf-8"))
            self.assertEqual(before, ledger.state)
            render.assert_not_called()

    def test_model_failure_never_builds_or_announces_discarded_builtin_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            _, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(temp)
            config.update(ai_provider="gemini", gemini_consent=True)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            output = Path(config["output_dir"]) / "worlds" / world["world_id"]
            output.mkdir(parents=True)
            (output / "narrative.md").write_text("이전 서사", encoding="utf-8")
            secret = Mock()
            secret.get_gemini_key.return_value = ("fixture", "fixture")
            logs = []
            before = copy.deepcopy(ledger.state)
            with patch.object(app, "_secret_store", return_value=secret), \
                    patch.object(app, "_ensure_local_llm", return_value=False), \
                    patch.object(gp, "generate_text", side_effect=gp.GeminiProviderError("UNSUPPORTED_NUMERIC_CLAIM", "fixture")), \
                    patch.object(service_module.presentation, "render_built_in_narrative") as render:
                result = app.generate_narrative(lambda *args: logs.append(args))
            render.assert_not_called()
            self.assertTrue(result["run"]["provider_failed"])
            self.assertEqual("이전 서사", result["narrative"])
            self.assertEqual(before, ledger.state)
            self.assertNotIn("재구성합니다", str(logs))

    def test_gemini_and_local_receive_current_bound_targets_and_keep_actual_writer(self):
        for provider in ("gemini", "local_only"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                _, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(temp)
                event["snapshot"]["stats"]["pit_K"] = 499
                config.update(ai_provider=provider, gemini_consent=True)
                app = service_module.StarModeService()
                app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
                secret = Mock()
                secret.get_gemini_key.return_value = ("fixture", "fixture")
                prose = "시즌 500탈삼진까지 1개 남았다. 다음 경기를 준비했다."
                logs = []
                original_generate = gp.generate_text
                def remote_call(system, prompt, **kwargs):
                    self.assertEqual(500, kwargs["prospective_targets"][0]["target"])
                    kwargs["on_validation_retry"]({"retry": 1, "max_retries": 1, "message": "숫자 교정"})
                    return original_generate(system, prompt, **{**kwargs, "urlopen": lambda *_a, **_k: _Response(_completion(prose))})
                with patch.object(app, "_secret_store", return_value=secret), \
                        patch.object(app, "_ensure_local_llm", return_value=True), \
                        patch.object(gp, "generate_text", side_effect=remote_call), \
                        patch.object(service_module.narration_module, "narrate", return_value=(prose, "local-fixture")) as local:
                    result = app.generate_narrative(lambda *args: logs.append(args))
                self.assertFalse(result["run"].get("provider_failed"))
                self.assertFalse(result["run"]["fallback"])
                self.assertIn("500탈삼진", result["narrative"])
                if provider == "gemini":
                    self.assertEqual("gemini", result["run"]["provider"])
                    self.assertIn("Gemini 응답 교정", str(logs))
                    local.assert_not_called()
                else:
                    self.assertEqual("local_llm", result["run"]["provider"])
                    self.assertIn("아직 달성한 기록이 아님", local.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
