"""Disposable regressions for received-but-rejected provider completions."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gemini_provider as gp
import provider_feed as pf
import service as service_module
import presentation
from tests import test_provider_feed as feed_tests
from tests import test_feed_provider_service as service_tests
from tests import test_story_service as story_tests
from tests.test_gemini_provider import _completion, _Response


def repeated_canonical():
    canonical = pf.prepare_canonical(feed_tests.feed(), feed_tests.event(), feed_tests.spotlight(), universe_id="world-a")
    article = canonical["media"][0]
    article["body"] = ["확인된 시즌 성적만 나란히 놓고 비교했다."] * 3
    article["blocks"] = [{"text": text, "fact_ids": ["fact-a"]} for text in article["body"]]
    return canonical


class CompletionValidationTests(unittest.TestCase):
    def test_existing_factual_repeats_survive_community_assembly_and_merge(self):
        canonical = repeated_canonical()
        saved = copy.deepcopy(canonical)
        contract = json.loads(feed_tests.valid_response(canonical))
        contract["articles"] = pf.expression_contract(canonical)["articles"]
        result = pf.finalize_batched_expression(canonical, contract, provider="gemini", model="fixture",
                                               batches=pf.scoped_expression_batches(canonical, "community"))
        merged = pf.merge_scoped_expression(canonical, saved, result, "community")
        self.assertEqual(saved["media"], merged["media"])
        self.assertEqual(contract["boards"], pf.expression_contract(merged)["boards"])
        self.assertEqual(saved, canonical)

    def test_new_cross_batch_repetition_is_still_rejected(self):
        canonical = repeated_canonical()
        result = copy.deepcopy(canonical)
        for post in result["social"][:3]:
            post["replies"][0]["text"] = "모든 게시판에서 새로 복제된 똑같은 긴 반응이다."
        with self.assertRaisesRegex(pf.ProviderFeedError, "지나치게 반복"):
            pf._validate_repetition(canonical, result)

    def test_existing_phrase_copied_into_new_positions_is_not_exempt(self):
        canonical = repeated_canonical()
        result = copy.deepcopy(canonical)
        for post in result["social"][:2]:
            post["replies"][0]["text"] = canonical["media"][0]["body"][0]
        with self.assertRaises(pf.ProviderFeedError):
            pf._validate_repetition(canonical, result)

    def test_new_primary_clone_is_still_rejected(self):
        canonical = repeated_canonical()
        result = copy.deepcopy(canonical)
        result["social"][1]["text"] = result["social"][0]["text"]
        with self.assertRaisesRegex(pf.ProviderFeedError, "핵심 문장"):
            pf._validate_repetition(canonical, result)

    def test_both_provider_jobs_publish_with_untouched_repeated_articles(self):
        for provider in ("gemini", "local_only"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                helper = service_tests.FeedProviderServiceTests()
                ledger, event, config, world, output = helper._fixture(temp, provider=provider)
                canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
                canonical["media"][0]["body"] = ["확인된 시즌 성적만 나란히 놓고 비교했다."] * 3
                canonical["media"][0]["blocks"] = [{"text": text} for text in canonical["media"][0]["body"]]
                (output / "feed.canonical.json").write_text(json.dumps(canonical), encoding="utf-8")
                (output / "feed.json").write_text(json.dumps(canonical), encoding="utf-8")
                app = helper._app(ledger, event, config, world)
                secret = Mock()
                secret.get_gemini_key.return_value = ("fixture-only", "fixture")
                def remote(_system, prompt, **_kwargs):
                    return gp.ProviderResult(service_tests._model_response(prompt), gp.DEFAULT_MODEL, "hash", 1, 1, "STOP")
                def local(_system, prompt, **_kwargs):
                    return service_tests._model_response(prompt), "local-fixture"
                with patch.object(app, "_secret_store", return_value=secret), patch.object(app, "_ensure_local_llm", return_value=True), \
                        patch.object(gp, "generate_text", side_effect=remote), \
                        patch.object(service_module.narration_module, "structured_feed", side_effect=local):
                    result = app.enrich_feed(lambda *_args: None, {"surface_scope": "community"})
                self.assertFalse(result["run"].get("provider_failed"))
                self.assertEqual(canonical["media"], result["feed"]["media"])


class NarrativeNumberTests(unittest.TestCase):
    def test_date_typography_and_list_ordinals_are_not_new_statistics(self):
        gp._validate_generated_text("## 2027.9.8\n\n1. 조용한 영상실\n2. 내일을 준비한다.", "날짜: 2027년 9월 8일")
        gp._validate_generated_text("타율 .730을 기록했다.", "타율 0.730")

    def test_inline_and_list_body_invented_numbers_remain_rejected(self):
        for text in ("투수는 999승을 거뒀다.", "1. 투수는 999승을 거뒀다.", "타율 .74였다.", "3. 근거 없는 번호다."):
            with self.subTest(text=text), self.assertRaises(gp.GeminiProviderError):
                gp._validate_generated_text(text, "타율 .730 · 20승")

    def test_numeric_retry_repairs_the_draft_but_never_licenses_its_numbers(self):
        calls = []
        draft = "영상실에서 999승을 거뒀다고 떠올렸다.\n\n조용히 다음 경기를 준비했다."
        payloads = iter([_completion(draft), _completion("조용히 다음 경기를 준비했다.")])
        def send(request, timeout):
            calls.append(json.loads(request.data))
            return _Response(next(payloads))
        result = gp.generate_text("사실만 쓴다.", "검증된 20승", api_key="fixture", urlopen=send)
        self.assertEqual(2, result.request_count)
        self.assertIn(draft, calls[1]["contents"][0]["parts"][0]["text"])
        self.assertNotIn(draft, calls[0]["contents"][0]["parts"][0]["text"])
        self.assertNotIn("999", result.text)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp.generate_text("사실만 쓴다.", "검증된 20승", api_key="fixture",
                             urlopen=lambda *_args, **_kwargs: _Response(_completion(draft)))
        self.assertEqual("UNSUPPORTED_NUMERIC_CLAIM", caught.exception.code)
        self.assertEqual(2, caught.exception.request_count)

    def test_safe_failure_detail_never_uses_upstream_error_text(self):
        error = gp.GeminiProviderError("UNSUPPORTED_NUMERIC_CLAIM", "PRIVATE_KEY_SENTINEL")
        error.unsupported_numbers = ["999", "PRIVATE_KEY_SENTINEL"]
        detail = gp.public_failure_detail(error)
        self.assertEqual(["999"], detail["unsupported_numbers"])
        self.assertNotIn("PRIVATE_KEY_SENTINEL", str(detail))

    def test_oversized_numeric_draft_keeps_bounded_instruction_only_retry(self):
        requests = []
        payloads = iter([_completion("999승" + '"' * 9000), _completion("다음 경기를 준비했다.")])
        def send(request, timeout):
            requests.append(request)
            return _Response(next(payloads))
        with patch.object(gp, "MAX_PROMPT_BYTES", 4096):
            result = gp.generate_text("한국어 서사", "검증된 20승", api_key="fixture", urlopen=send)
        self.assertEqual(2, result.request_count)
        self.assertLess(len(requests[1].data), 4096)
        self.assertNotIn("직전 응답 초안", requests[1].data.decode("utf-8"))


class NarrativeCompletionTests(unittest.TestCase):
    def test_model_failure_preserves_existing_narrative_and_explains_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            snap, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(temp)
            config.update(ai_provider="gemini", gemini_consent=True)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            output = Path(config["output_dir"]) / "worlds" / world["world_id"]
            output.mkdir(parents=True)
            path = output / "narrative.md"
            path.write_text("이전 모델의 온전한 서사", encoding="utf-8")
            html = output / "narrative.html"
            html.write_text("<p>이전 모델의 온전한 서사</p>", encoding="utf-8")
            before = path.read_bytes(), html.read_bytes(), copy.deepcopy(ledger.state)
            secret = Mock()
            secret.get_gemini_key.return_value = ("fixture", "fixture")
            error = gp.GeminiProviderError("UNSUPPORTED_NUMERIC_CLAIM", "PRIVATE_KEY_SENTINEL")
            error.unsupported_numbers = ["999"]
            logs = []
            with patch.object(app, "_secret_store", return_value=secret), patch.object(app, "_ensure_local_llm", return_value=False), \
                    patch.object(gp, "generate_text", side_effect=error):
                result = app.generate_narrative(lambda *args: logs.append(args))
            self.assertTrue(result["run"]["provider_failed"])
            self.assertIn("검증", result["run"]["message"])
            self.assertIn("999", str(logs))
            self.assertNotIn("PRIVATE_KEY_SENTINEL", str(result) + str(logs))
            self.assertEqual(before, (path.read_bytes(), html.read_bytes(), ledger.state))

    def test_saved_writer_comes_from_exact_world_player_and_text_not_settings(self):
        snap = story_tests.snapshot()
        world = {"world_id": "world-one"}
        ledger = Mock(state={"chronicle": {"cinematics": [
            {"world_id": "world-one", "player_id": 7, "text": "기록", "model": "builtin:deterministic-feed"},
            {"world_id": "other-world", "player_id": 7, "text": "기록", "model": "gemini:other"},
            {"world_id": "world-one", "player_id": 8, "text": "기록", "model": "gemini:other"},
        ]}})
        source = service_module.StarModeService._narrative_source(ledger, world, snap, "기록")
        self.assertEqual("builtin", source["provider"])
        self.assertEqual({"provider": "unknown"}, service_module.StarModeService._narrative_source(ledger, world, snap, "다른 기록"))

    def test_builtin_projection_does_not_call_interval_delta_a_single_game(self):
        text = presentation.render_built_in_narrative({"event": {"game_lines": ["45이닝 73탈삼진"]}})
        self.assertIn("직전 확인 이후 변화분", text)
        self.assertNotIn("오늘 기록:", text)
        self.assertNotIn("모델이 응답하지 않아", text)
