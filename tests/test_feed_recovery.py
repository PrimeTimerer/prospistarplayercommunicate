#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recovery behaviour for sequential provider batches.

Written against the 2026-09-05 diagnosis of a community run that produced
nothing after four successful model responses: completed batches were thrown
away, a 5xx burst was abandoned after six seconds, the retry only asked for
less text instead of naming the mistake, one bad value cost a whole batch, and
no reason for any of it was recorded.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import gemini_provider
import narrate
import provider_feed
import service as service_module

try:
    from tests.test_provider_feed import event, feed, spotlight, valid_response
    from tests.test_feed_provider_service import _base_feed, _model_response, _snapshot, _spotlight
except ImportError:  # pragma: no cover - depends on the discovery root
    from test_provider_feed import event, feed, spotlight, valid_response
    from test_feed_provider_service import _base_feed, _model_response, _snapshot, _spotlight

from ledger_v2 import Ledger


def _fixture(root: str, *, provider: str):
    snap = _snapshot()
    ledger = Ledger(str(Path(root) / "ledger.json"), "world-one")
    ledger.commit(ledger.classify(snap))
    ledger_event = ledger.classify(snap)
    save_path = Path(root) / "save.dat"
    save_path.write_bytes(b"save")
    config = {
        "data_dir": str(Path(root) / "data"),
        "output_dir": str(Path(root) / "output"),
        "shots_dir": str(Path(root) / "shots"),
        "save_path": str(save_path),
        "ai_provider": provider,
        "gemini_consent": provider == "gemini",
        "gemini_model": gemini_provider.DEFAULT_MODEL,
        "mode": "standard",
    }
    world = {"world_id": "world-one", "generation": 1, "slot": "00"}
    canonical = provider_feed.prepare_canonical(_base_feed(), ledger_event, _spotlight(), universe_id="world-one")
    output = Path(config["output_dir"]) / "worlds" / "world-one"
    output.mkdir(parents=True)
    (output / "feed.canonical.json").write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    return ledger, ledger_event, config, world, output


def _app(ledger, ledger_event, config, world):
    app = service_module.StarModeService()
    app._prepare = lambda update=None: (config, {}, None, ledger, world, ledger_event)
    app._spotlight = lambda *_args, **_kwargs: _spotlight()
    app._dashboard = lambda _config, _diagnostics, _ledger, _world, _event, **kwargs: {
        "feed": kwargs.get("feed"),
        "run": kwargs.get("run"),
    }
    app._persist_current_capsule = lambda *_args, **_kwargs: {}
    return app


def _remote_result(user_text: str):
    return gemini_provider.ProviderResult(
        text=_model_response(user_text),
        model=gemini_provider.DEFAULT_MODEL,
        request_hash="hash",
        request_bytes=100,
        response_bytes=200,
        finish_reason="STOP",
        request_count=1,
        prompt_tokens=10,
        output_tokens=20,
        total_tokens=30,
        metered_responses=1,
    )


def _ordinals(mock_calls) -> list[int]:
    return [json.loads(call.args[1])["sequential_batch"]["ordinal"] for call in mock_calls]


class BatchResumeTests(unittest.TestCase):
    def test_batches_finished_before_an_outage_are_not_rewritten_by_the_local_model(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, ledger_event, config, world, output = _fixture(temp, provider="gemini")
            app = _app(ledger, ledger_event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            total = len(provider_feed.expression_batches(canonical))
            self.assertGreaterEqual(total, 3)
            def generate(_system, user, **_kwargs):
                if json.loads(user)["sequential_batch"]["ordinal"] == 2:
                    failure = gemini_provider.GeminiProviderError("REMOTE_UNAVAILABLE", "synthetic outage")
                    failure.request_count = 1
                    raise failure
                return _remote_result(user)

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "_ensure_local_llm", return_value=True
            ), patch.object(app, "_record_gemini_failure"), patch.object(
                gemini_provider, "generate_text", side_effect=generate
            ) as remote, patch.object(
                service_module.narration_module,
                "structured_feed",
                side_effect=lambda _system, user, **_kwargs: (_model_response(user), "local-fixture"),
            ) as local, patch.object(
                app, "_require_chat_origin", wraps=app._require_chat_origin
            ) as origin_guard:
                dashboard = app.enrich_feed(lambda *_args: None)

            # Gemini wrote every batch except the one that failed. The local
            # model writes only that one and never rewrites the others.
            self.assertEqual(total, remote.call_count)
            self.assertEqual(1, local.call_count)
            self.assertEqual([2], _ordinals(local.call_args_list))

            bundle = dashboard["feed"]["reaction_bundle"]
            self.assertEqual("mixed_expression", bundle["renderer"])
            audit = bundle["provider_audit"]
            self.assertEqual({"gemini", "local_llm"}, set(audit["contributors"]))
            self.assertEqual(total, audit["completed_batches"])
            self.assertEqual(
                {"gemini": total - 1, "local_llm": 1},
                {
                    name: sum(1 for row in audit["batches"] if row["provider"] == name)
                    for name in ("gemini", "local_llm")
                },
            )
            self.assertEqual(
                "public_local",
                origin_guard.call_args.args[3]["personal_context_scope"],
            )
            published = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual("mixed_expression", published["reaction_bundle"]["renderer"])

    def test_an_unfinishable_plan_keeps_the_current_feed_and_records_what_was_written(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, ledger_event, config, world, output = _fixture(temp, provider="gemini")
            app = _app(ledger, ledger_event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            current = copy.deepcopy(canonical)
            current["reaction_bundle"]["renderer"] = "gemini_expression"
            current["reaction_bundle"]["provider"] = "gemini"
            current["reaction_bundle"]["preservation_fixture"] = "keep-this-exact-feed"
            current_path = output / "feed.json"
            current_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            current_bytes = current_path.read_bytes()
            def generate(_system, user, **_kwargs):
                if json.loads(user)["sequential_batch"]["ordinal"] == 2:
                    raise gemini_provider.GeminiProviderError("REMOTE_UNAVAILABLE", "synthetic outage")
                return _remote_result(user)

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "_ensure_local_llm", return_value=False
            ), patch.object(app, "_record_gemini_failure"), patch.object(
                gemini_provider, "generate_text", side_effect=generate
            ):
                dashboard = app.enrich_feed(lambda *_args: None)

            self.assertEqual(current_bytes, current_path.read_bytes())
            self.assertEqual(current, dashboard["feed"])
            self.assertEqual("gemini_expression", dashboard["feed"]["reaction_bundle"]["renderer"])
            chain = dashboard["run"]["provider_attempts"]
            incomplete = [row for row in chain if row.get("status") == "incomplete"]
            self.assertEqual(1, len(incomplete))
            self.assertEqual(2, incomplete[0]["completed_batches"])
            self.assertIn("발행하지 않았습니다", incomplete[0]["message"])


class RejectionFeedbackTests(unittest.TestCase):
    def test_a_retry_prompt_carries_the_reason_the_previous_answer_was_refused(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batch = provider_feed.expression_batches(canonical)[0]
        system, user, _allowed = provider_feed.build_batch_prompt(
            canonical, batch, spotlight=spotlight(), repair_note="모델이 boards의 ID, 수 또는 순서를 바꿨습니다."
        )
        packet = json.loads(user)
        self.assertIn("ID, 수 또는 순서", system)
        self.assertIn("ID, 수 또는 순서", packet["sequential_batch"]["previous_attempt_rejected_because"])
        plain_system, plain_user, _ = provider_feed.build_batch_prompt(canonical, batch, spotlight=spotlight())
        self.assertNotIn("previous_attempt_rejected_because", json.loads(plain_user)["sequential_batch"])
        self.assertNotIn("직전 시도는", plain_system)

    def test_the_required_output_shape_is_named_instead_of_mirroring_the_whole_packet(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        system, user, _allowed = provider_feed.build_batch_prompt(
            canonical, provider_feed.expression_batches(canonical)[0], spotlight=spotlight()
        )
        # The packet holds several blocks; the instruction must point at one.
        self.assertGreater(len(json.loads(user)), 3)
        self.assertIn("output_contract와 완전히 같은 구조", system)
        self.assertIn("version, articles, boards, social", system)
        self.assertIn("batch_id", system)
        self.assertNotIn("입력과 동일한 JSON 구조", system)

    def test_the_service_repeats_the_refusal_reason_to_the_model_and_logs_it(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, ledger_event, config, world, _output = _fixture(temp, provider="local_auto")
            app = _app(ledger, ledger_event, config, world)
            seen: list[str] = []
            calls = 0

            def local_generate(system, user, **_kwargs):
                nonlocal calls
                calls += 1
                seen.append(system)
                if calls == 1:
                    # A shape the validator refuses: the wrong block of the prompt.
                    return json.dumps({"batch_id": "x", "surface": "boards", "items": []}), "local-fixture"
                return _model_response(user), "local-fixture"

            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.narration_module, "structured_feed", side_effect=local_generate
            ):
                with self.assertLogs(service_module.log, level="WARNING") as logs:
                    dashboard = app.enrich_feed(lambda *_args: None)

            self.assertIn("전체 반응 출력 버전", seen[1])
            self.assertTrue(any("rejected on attempt 1/3" in line for line in logs.output))
            self.assertEqual("local_llm_expression", dashboard["feed"]["reaction_bundle"]["renderer"])


class PromptGuidanceTests(unittest.TestCase):
    def test_the_prompt_says_which_items_must_name_the_protagonist(self):
        source = feed()
        source["boards"][0]["title"] = "시즌 148도루, 한 가지는 짚고 가자"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        system, user, _allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        packet = json.loads(user)
        rules = packet["naming_requirements"]
        self.assertEqual("Paul Skenes", rules["protagonist_name"])
        self.assertIn("board-a", rules["may_omit_name_ids"])
        self.assertIn("article-a", rules["must_name_ids"])
        self.assertEqual(
            {row["id"] for row in packet["output_contract"]["social"]},
            set(rules["must_name_ids"]) - {"article-a"},
        )
        self.assertIn("must_name_ids", system)
        self.assertIn("may_omit_name_ids", system)

    def test_the_prompt_states_the_exact_counts_a_reply_must_reproduce(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        system, user, _allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        packet = json.loads(user)
        shape = packet["shape_requirements"]
        contract = packet["output_contract"]
        self.assertEqual(
            {row["id"]: len(row["body"]) for row in contract["articles"]},
            shape["article_body_paragraphs"],
        )
        self.assertEqual(
            {row["id"]: len(row["comments"]) for row in contract["boards"]},
            shape["board_comments"],
        )
        self.assertEqual(
            {row["id"]: len(row["replies"]) for row in contract["social"]},
            shape["social_replies"],
        )
        self.assertIn("shape_requirements", system)

    def test_the_prompt_forbids_rounding_a_verified_number(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        system, _user, _allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        self.assertIn("반올림", system)

    def test_a_numeric_refusal_names_the_offending_values_on_retry(self):
        bodies = []

        def urlopen(request, timeout):  # noqa: ARG001 - signature mirrors urllib
            body = json.loads(request.data.decode("utf-8"))
            bodies.append(body)
            text = "서른두 경기 뒤의 기록이다." if len(bodies) > 1 else "시즌 999홈런을 넘겼다는 반응이다."

            class Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_exc):
                    return False

                @staticmethod
                def read(_limit=None):
                    return json.dumps(
                        {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
                    ).encode("utf-8")

            return Response()

        result = gemini_provider.generate_text(
            "시스템", "검증된 요약", api_key="secret", urlopen=urlopen, sleep_fn=lambda _d: None,
            random_fn=lambda: 0.0,
        )
        self.assertEqual("서른두 경기 뒤의 기록이다.", result.text)
        self.assertEqual(2, len(bodies))
        retry_system = bodies[1]["system_instruction"]["parts"][0]["text"]
        self.assertIn("999", retry_system)
        self.assertIn("반올림", retry_system)


class ProtagonistSpellingTests(unittest.TestCase):
    """A Korean board writes the Latin-spelled player in Hangul."""

    def _canonical(self, aliases=None):
        return provider_feed.prepare_canonical(
            feed(), event(), spotlight(), universe_id="world-a", protagonist_aliases=aliases
        )

    def test_a_hangul_rendering_is_refused_when_no_spelling_is_registered(self):
        canonical = self._canonical()
        variants = provider_feed._name_variants(canonical)
        self.assertTrue(provider_feed._focused("Paul Skenes 오늘 미쳤다", variants))
        self.assertFalse(provider_feed._focused("스켄스 오늘 미쳤다", variants))

    def test_a_registered_spelling_counts_as_the_protagonist(self):
        canonical = self._canonical(["스켄스"])
        variants = provider_feed._name_variants(canonical)
        self.assertTrue(provider_feed._focused("스켄스 오늘 진짜 미쳤다 ㄷㄷ", variants))
        self.assertTrue(provider_feed._focused("Skenes 다음 등판 언제냐", variants))
        self.assertFalse(provider_feed._focused("오늘 경기 하이라이트 정리", variants))
        self.assertEqual(["스켄스"], canonical["reaction_bundle"]["focus"]["protagonist_aliases"])

    def test_the_prompt_lists_every_accepted_spelling(self):
        canonical = self._canonical(["스켄스"])
        _system, user, _allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        rules = json.loads(user)["naming_requirements"]
        self.assertEqual(["Paul Skenes", "Skenes", "스켄스"], rules["accepted_spellings"])

    def test_a_hangul_post_survives_the_whole_bundle_check_with_a_registered_spelling(self):
        canonical = self._canonical(["스켄스"])
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["social"][0]["text"] = "스켄스 기록을 오늘도 다시 확인하게 되는 밤이다."
        rendered = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed,
        )
        self.assertEqual("스켄스 기록을 오늘도 다시 확인하게 되는 밤이다.", rendered["social"][0]["text"])

    def test_aliases_are_bounded_and_deduplicated(self):
        canonical = self._canonical(["스켄스", " 스켄스 ", "폴", "x" * 80, *[f"n{i}" for i in range(12)]])
        stored = canonical["reaction_bundle"]["focus"]["protagonist_aliases"]
        self.assertEqual(8, len(stored))
        self.assertEqual(["스켄스", "폴"], stored[:2])

    def test_a_missing_name_revert_records_what_was_written(self):
        canonical = self._canonical()
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["social"][0]["text"] = "스켄스 기록을 오늘도 다시 확인하게 되는 밤이다."
        rescued = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed, on_violation="revert_item",
        )
        reasons = rescued["reaction_bundle"]["reverted_items"][0]["reasons"]
        self.assertTrue(any("스켄스" in reason for reason in reasons), reasons)


class SpellingSuggestionTests(unittest.TestCase):
    def test_a_repeated_or_leading_hangul_token_is_offered_as_the_spelling(self):
        self.assertEqual(
            ["스켄스"],
            provider_feed.protagonist_spelling_candidates(
                "스켄스 오늘 진짜 미쳤다 ㄷㄷ 스켄스 다음 등판 언제냐", "Paul Skenes의 다음 경기"
            ),
        )
        self.assertEqual(
            ["스켄스"],
            provider_feed.protagonist_spelling_candidates("폴 스켄스 기록을 오늘도 확인한다", "Paul Skenes 기록"),
        )

    def test_nothing_is_offered_when_the_registered_spelling_was_used(self):
        self.assertEqual(
            [], provider_feed.protagonist_spelling_candidates("Paul Skenes 오늘 미쳤다", "Paul Skenes 기록")
        )

    def test_a_word_already_in_the_built_in_text_is_never_offered(self):
        self.assertEqual(
            [], provider_feed.protagonist_spelling_candidates("기록 기록 기록", "기록을 본다")
        )

    def test_the_run_reports_the_spelling_the_model_used(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, ledger_event, config, world, _output = _fixture(temp, provider="local_auto")
            app = _app(ledger, ledger_event, config, world)

            def local_generate(_system, user, **_kwargs):
                payload = json.loads(_model_response(user))
                # Only one post drops the registered spelling, so the batch
                # still has a surviving item and the revert is recorded.
                for row in (payload.get("social") or [])[:1]:
                    row["text"] = "스켄스 기록을 오늘도 다시 확인하게 되는 밤이다."
                return json.dumps(payload, ensure_ascii=False), "local-fixture"

            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.narration_module, "structured_feed", side_effect=local_generate
            ):
                dashboard = app.enrich_feed(lambda *_args: None)

            self.assertIn("스켄스", dashboard["run"]["protagonist_spelling_candidates"])


class LocalSchemaTests(unittest.TestCase):
    def test_the_batch_schema_pins_the_top_level_shape_and_the_fixed_identifiers(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batch = next(row for row in provider_feed.expression_batches(canonical) if row["kind"] == "boards")
        schema = provider_feed.batch_response_schema(canonical, batch)
        self.assertEqual(["version", "articles", "boards", "social"], schema["required"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("1", schema["properties"]["version"]["const"])
        boards = schema["properties"]["boards"]
        self.assertEqual(batch["item_count"], boards["minItems"])
        self.assertEqual(batch["item_count"], boards["maxItems"])
        # Each position pins one identifier. An enum of the batch identifiers
        # would let a constrained model emit the same id twice, which is what
        # the local model actually did on 2026-09-05.
        self.assertEqual(
            [str(value) for value in batch["item_ids"]],
            [row["properties"]["id"]["const"] for row in boards["prefixItems"]],
        )
        contract = provider_feed.expression_contract(provider_feed.expression_batch_feed(canonical, batch))
        self.assertEqual(
            [[c["id"] for c in row["comments"]] for row in contract["boards"]],
            [
                [c["properties"]["id"]["const"] for c in row["properties"]["comments"]["prefixItems"]]
                for row in boards["prefixItems"]
            ],
        )
        self.assertEqual(0, schema["properties"]["articles"]["maxItems"])
        self.assertEqual(0, schema["properties"]["social"]["maxItems"])
        self.assertNotIn("prefixItems", schema["properties"]["articles"])

    def test_the_schema_is_the_repair_tool_and_not_the_first_request(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, ledger_event, config, world, _output = _fixture(temp, provider="local_auto")
            app = _app(ledger, ledger_event, config, world)
            seen = []

            def local_generate(_system, user, **kwargs):
                seen.append((json.loads(user), kwargs.get("json_schema")))
                if len(seen) == 1:
                    return json.dumps({"batch_id": "x", "surface": "boards", "items": []}), "local-fixture"
                return _model_response(user), "local-fixture"

            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.narration_module, "structured_feed", side_effect=local_generate
            ):
                app.enrich_feed(lambda *_args: None)

            # Constrained decoding roughly halves local throughput, so the
            # first attempt runs free and the retry pins the shape.
            self.assertIsNone(seen[0][1])
            packet, schema = seen[1]
            surface = packet["sequential_batch"]["surface"]
            self.assertEqual(["version", "articles", "boards", "social"], schema["required"])
            self.assertGreater(schema["properties"][surface]["minItems"], 0)
            self.assertEqual(
                [str(row["id"]) for row in packet["output_contract"][surface]],
                [row["properties"]["id"]["const"] for row in schema["properties"][surface]["prefixItems"]],
            )

    def test_a_server_without_schema_support_degrades_to_plain_json_then_to_no_hint(self):
        bodies = []

        def urlopen(request, timeout):  # noqa: ARG001 - signature mirrors urllib
            body = json.loads(request.data.decode("utf-8"))
            bodies.append(body)
            if "response_format" in body:
                raise urllib.error.HTTPError(request.full_url, 400, "unsupported", {}, None)

            class Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_exc):
                    return False

                @staticmethod
                def read():
                    return json.dumps({"choices": [{"message": {"content": "{\"version\":\"1\"}"}}]}).encode("utf-8")

            return Response()

        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8080/v1/chat/completions", "m")):
            with patch.object(narrate.urllib.request, "urlopen", side_effect=urlopen):
                text, model = narrate.structured_feed("system", "user", json_schema={"type": "object"})

        self.assertEqual("m", model)
        self.assertIn("version", text)
        self.assertEqual(3, len(bodies))
        self.assertEqual("json_schema", bodies[0]["response_format"]["type"])
        self.assertEqual("json_object", bodies[1]["response_format"]["type"])
        self.assertNotIn("response_format", bodies[2])

    def test_a_dropped_connection_on_the_constrained_request_still_falls_back(self):
        bodies = []

        def urlopen(request, timeout):  # noqa: ARG001 - signature mirrors urllib
            body = json.loads(request.data.decode("utf-8"))
            bodies.append(body)
            if "response_format" in body:
                raise ConnectionResetError(10054, "connection forcibly closed")

            class Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_exc):
                    return False

                @staticmethod
                def read():
                    return json.dumps({"choices": [{"message": {"content": "{\"version\":\"1\"}"}}]}).encode("utf-8")

            return Response()

        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8080/v1/chat/completions", "m")):
            with patch.object(narrate.urllib.request, "urlopen", side_effect=urlopen):
                text, model = narrate.structured_feed("system", "user", json_schema={"type": "object"})

        # A grammar heavy enough to end the connection must never leave the
        # local path worse off than sending no schema at all.
        self.assertEqual("m", model)
        self.assertIn("version", text)
        self.assertEqual(3, len(bodies))
        self.assertNotIn("response_format", bodies[2])

    def test_a_dead_server_reports_a_message_instead_of_raising(self):
        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8080/v1/chat/completions", "m")):
            with patch.object(
                narrate.urllib.request, "urlopen", side_effect=ConnectionResetError(10054, "closed")
            ):
                text, message = narrate.structured_feed("system", "user", json_schema={"type": "object"})
        self.assertIsNone(text)
        self.assertIn("로컬 LLM 요청 실패", message)


class TransientBackoffTests(unittest.TestCase):
    def test_the_ladder_waits_long_enough_for_a_real_outage(self):
        delays = [
            gemini_provider._transient_retry_delay(number, random_fn=lambda: 0.0) for number in (1, 2, 3, 4)
        ]
        self.assertEqual([5.0, 15.0, 45.0, 45.0], delays)
        self.assertEqual(3, gemini_provider._TRANSIENT_RETRY_LIMITS["REMOTE_UNAVAILABLE"])
        # Roughly a minute of patience instead of roughly six seconds.
        self.assertGreater(sum(delays[:3]), 60.0)


class PerItemRecoveryTests(unittest.TestCase):
    def _poisoned(self, canonical):
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["comments"][0]["text"] = "검증 자료에 없는 9999승이라는 반응이 나왔다."
        return payload

    def test_one_bad_comment_costs_its_own_item_instead_of_the_whole_batch(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = self._poisoned(canonical)

        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
                allowed_number_source=allowed,
            )

        rescued = provider_feed.apply_expression(
            canonical,
            json.dumps(payload, ensure_ascii=False),
            provider="gemini",
            model="fixture",
            allowed_number_source=allowed,
            on_violation="revert_item",
        )
        reverted = rescued["reaction_bundle"]["reverted_items"]
        self.assertEqual(["board-a"], [row["id"] for row in reverted])
        self.assertTrue(any("숫자" in reason for reason in reverted[0]["reasons"]))
        # The offending board is back to the built-in text...
        self.assertEqual(canonical["boards"][0]["title"], rescued["boards"][0]["title"])
        self.assertNotIn("9999", json.dumps(rescued["boards"], ensure_ascii=False))
        self.assertEqual(
            [row["text"] for row in canonical["boards"][0]["comments"]],
            [row["text"] for row in rescued["boards"][0]["comments"]],
        )
        # ...and everything else keeps what the model wrote.
        self.assertEqual(payload["articles"][0]["title"], rescued["media"][0]["title"])
        self.assertEqual(payload["social"][0]["text"], rescued["social"][0]["text"])

    def test_a_reply_that_fails_everywhere_is_still_refused(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        for row in payload["articles"]:
            row["title"] = "검증 자료에 없는 9999승 기록"
        for row in payload["boards"]:
            row["title"] = "검증 자료에 없는 9999승 기록"
        for row in payload["social"]:
            row["text"] = "검증 자료에 없는 9999승 기록"
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
                allowed_number_source=allowed, on_violation="revert_item",
            )

    def test_structural_damage_is_never_downgraded_to_an_item_revert(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["id"] = "different"
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
                on_violation="revert_item",
            )


class FocusRuleTests(unittest.TestCase):
    def test_a_league_wide_board_may_keep_a_league_wide_headline(self):
        source = feed()
        source["boards"][0]["title"] = "시즌 148도루, 한 가지는 짚고 가자"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["title"] = "리그 전체 도루 흐름을 정리해 보자"
        rendered = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed,
        )
        self.assertEqual("리그 전체 도루 흐름을 정리해 보자", rendered["boards"][0]["title"])

    def test_a_board_the_engine_named_after_the_player_still_has_to_name_him(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        self.assertIn("Paul Skenes", canonical["boards"][0]["title"])
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["title"] = "리그 전체 도루 흐름을 정리해 보자"
        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "주인공이 빠진 게시판 제목"):
            provider_feed.apply_expression(
                canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
                allowed_number_source=allowed,
            )

    def test_a_foreign_outlet_keeps_its_own_language(self):
        source = feed()
        source["media"][0]["sub"] = "ひとつの記録からPaul Skenesのシーズンをたどる"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["articles"][0]["sub"] = "記録の重さをもう一度確かめる夜になった"
        rendered = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed,
        )
        self.assertEqual("記録の重さをもう一度確かめる夜になった", rendered["media"][0]["sub"])

    def test_a_korean_field_still_has_to_stay_korean(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["articles"][0]["sub"] = "A record-first look without any Korean"
        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "한국어 표현"):
            provider_feed.apply_expression(
                canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
                allowed_number_source=allowed,
            )

    def test_a_reverted_foreign_item_can_still_be_published(self):
        source = feed()
        source["media"][0]["sub"] = "ひとつの記録からPaul Skenesのシーズンをたどる"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["articles"][0]["body"][0] = "검증 자료에 없는 9999승을 기록했다는 분석이다."
        rescued = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed, on_violation="revert_item",
        )
        self.assertEqual(["article-a"], [row["id"] for row in rescued["reaction_bundle"]["reverted_items"]])
        self.assertEqual("ひとつの記録からPaul Skenesのシーズンをたどる", rescued["media"][0]["sub"])

    def test_a_scoped_merge_accepts_the_item_that_apply_expression_just_reverted(self):
        # The 2026-09-05 02:56 production failure: apply_expression reverted a
        # board to its deterministic text for a missing protagonist name, and
        # the scoped merge then refused that very text.
        source = feed()
        source["boards"][0]["title"] = "시즌 148도루, 한 가지는 짚고 가자"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["title"] = "리그 도루 흐름을 다시 본다"
        generated = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed, on_violation="revert_item",
        )
        merged = provider_feed.merge_scoped_expression(canonical, None, generated, "community")
        self.assertEqual("리그 도루 흐름을 다시 본다", merged["boards"][0]["title"])

    def test_a_scoped_merge_still_refuses_a_dropped_protagonist(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = json.loads(valid_response(canonical))
        generated = provider_feed.apply_expression(
            canonical, json.dumps(payload, ensure_ascii=False), provider="gemini", model="fixture",
            allowed_number_source=allowed,
        )
        generated["boards"][0]["title"] = "리그 도루 흐름을 다시 본다"
        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "주인공이 빠진"):
            provider_feed.merge_scoped_expression(canonical, None, generated, "community")

    def test_the_built_in_feed_passes_its_own_focus_rule(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        canonical["boards"][0]["title"] = "시즌 148도루, 한 가지는 짚고 가자"
        contract = provider_feed.expression_contract(canonical)
        rendered = provider_feed.apply_expression(
            canonical, json.dumps(contract, ensure_ascii=False), provider="builtin", model="deterministic",
            allowed_number_source=json.dumps(contract, ensure_ascii=False),
        )
        self.assertEqual(canonical["boards"][0]["title"], rendered["boards"][0]["title"])


class LanguageLevelTests(unittest.TestCase):
    def _payload(self, canonical):
        payload = json.loads(valid_response(canonical))
        payload["boards"][0]["comments"][0]["text"] = "ㄹㅇ 이건 기록으로만 봐도 흐름이 확실하다는 반응이다."
        return json.dumps(payload, ensure_ascii=False)

    def test_the_ceiling_comes_from_the_caller_not_from_the_stored_feed(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a", language_level=5)
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        payload = self._payload(canonical)

        allowed_at_five = provider_feed.apply_expression(
            canonical, payload, provider="gemini", model="fixture",
            allowed_number_source=allowed, language_level=5,
        )
        self.assertEqual(5, allowed_at_five["reaction_bundle"]["community_language_level"])

        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "언어 수위"):
            provider_feed.apply_expression(
                canonical, payload, provider="gemini", model="fixture",
                allowed_number_source=allowed, language_level=1,
            )

    def test_a_stored_feed_without_a_level_does_not_silently_tighten_the_ceiling(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a", language_level=5)
        stripped = copy.deepcopy(canonical)
        stripped.pop("community_language_level", None)
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        rendered = provider_feed.apply_expression(
            stripped, self._payload(canonical), provider="gemini", model="fixture",
            allowed_number_source=allowed, language_level=5,
        )
        self.assertEqual(5, rendered["community_language_level"])
        self.assertEqual(5, rendered["reaction_bundle"]["community_language_level"])


if __name__ == "__main__":
    unittest.main()
