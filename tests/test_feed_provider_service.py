#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import personal_context
import provider_feed
import service as service_module
from ledger_v2 import Ledger


def _snapshot():
    return {
        "player": {"id": 7, "name": "Test Player", "team": "TEST", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
        "stats": {"pit_IP": 20, "pit_K": 40, "pit_H": 3, "pit_W": 2},
        "content_hash": "verified-feed-origin",
        "profile_fingerprint": "profile",
        "validation": {},
        "provenance": {},
    }


def _spotlight():
    return {
        "schema_version": 1,
        "tier": "club",
        "label": "구단의 중심",
        "drivers": [{"label": "확인된 시즌 흐름"}],
        "memory_anchors": [],
        "reaction_budget": {"mode": "standard", "expression_heat": 7},
    }


def _base_feed():
    return {
        "player": {"id": 7, "name": "Test Player", "team": "TEST", "pos": "투수"},
        "assess": {"tone": "고조", "line_pit": "안정적인 흐름", "line_bat": ""},
        "event": {
            "kind": "NO_CHANGE",
            "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
            "game_lines": [],
            "milestones": [],
        },
        "heat": 7,
        "persona": "",
        "_stats": {"pit_IP": 20, "pit_K": 40, "pit_W": 2},
        "media": [
            {
                "id": "article-one",
                "outlet": "전국 기록 데스크",
                "flag": "기록",
                "title": "Test Player의 시즌 흐름을 읽다",
                "sub": "공개된 기록이 만든 다음 경기의 질문",
                "body": ["Test Player의 누적 흐름이 관심을 모았다.", "다음 승부의 과정이 새로운 판단 근거가 된다."],
                "blocks": [{"text": "Test Player의 누적 흐름이 관심을 모았다."}, {"text": "다음 승부의 과정이 새로운 판단 근거가 된다."}],
                "context_visibility": "public_record_background",
                "provenance": "fictional_ambient_simulation",
            }
        ],
        "boards": [
            {
                "id": "board-one",
                "board": "구단 팬 포럼",
                "code": "club-pulse",
                "title": "Test Player의 다음 승부를 기다리며",
                "comments": [
                    {"post_id": "comment-one", "author": "기록노트", "text": "누적 흐름부터 차분히 보자.", "up": 7},
                    {"post_id": "comment-two", "author": "외야석", "text": "다음 경기의 과정도 중요하다.", "up": 3},
                ],
                "posts": [
                    {"post_id": "comment-one", "author": "기록노트", "text": "누적 흐름부터 차분히 보자."},
                    {"post_id": "comment-two", "author": "외야석", "text": "다음 경기의 과정도 중요하다."},
                ],
                "context_visibility": "public_record_background",
                "provenance": "fictional_ambient_simulation",
            }
        ],
        "editorial": {
            "binding": {"universe_id": "world-one", "protagonist_id": "7", "game_date": "2027-07-24"},
            "facts": [{"fact_id": "fact-one", "kind": "season_pitching", "text": "확인된 시즌 흐름"}],
        },
    }


def _model_response(user_text: str) -> str:
    packet = json.loads(user_text)
    value = packet["output_contract"]
    for article in value["articles"]:
        article["title"] = "Test Player, 마운드에서 쌓아 올린 신뢰"
        article["sub"] = "경기마다 달라지는 승부 속에서도 선명해진 존재감"
        article["body"] = [
            "Test Player를 향한 관심은 한 번의 장면보다 이어진 과정에서 커졌다.",
            "분석가들은 다음 승부에서 선택할 공과 팀의 대응을 차분히 지켜보고 있다.",
        ]
    for board in value["boards"]:
        board["title"] = "Test Player를 바라보는 외야석의 서로 다른 시선"
        board["comments"][0]["text"] = "지금까지 이어진 과정부터 살피자는 의견에 마음이 간다."
        board["comments"][1]["text"] = "기대가 커도 다음 경기의 호흡까지 본 뒤 말하고 싶다."
    post_words = ["빠른", "깊은", "차분한", "새로운", "넓은", "뜨거운"]
    reply_words = ["첫째", "둘째", "셋째", "넷째"]
    for index, post in enumerate(value["social"]):
        post["text"] = f"Test Player를 향한 {post_words[index]} 관점이 각자의 타임라인에서 이어졌다."
        for ordinal, reply in enumerate(post["replies"]):
            reply["text"] = f"{post_words[index]} 흐름의 {reply_words[ordinal]} 의견은 다음 승부의 과정을 더 보고 싶다는 쪽이다."
    return json.dumps(value, ensure_ascii=False)


class FeedProviderServiceTests(unittest.TestCase):
    def _fixture(self, root: str, *, provider: str):
        snap = _snapshot()
        ledger = Ledger(str(Path(root) / "ledger.json"), "world-one")
        ledger.commit(ledger.classify(snap))
        event = ledger.classify(snap)
        save_path = Path(root) / "save.dat"
        save_path.write_bytes(b"save")
        config = {
            "data_dir": str(Path(root) / "data"),
            "output_dir": str(Path(root) / "output"),
            "shots_dir": str(Path(root) / "shots"),
            "save_path": str(save_path),
            "ai_provider": provider,
            "gemini_consent": provider == "gemini",
            "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
            "mode": "standard",
        }
        world = {"world_id": "world-one", "generation": 1, "slot": "00"}
        canonical = provider_feed.prepare_canonical(_base_feed(), event, _spotlight(), universe_id="world-one")
        output = Path(config["output_dir"]) / "worlds" / "world-one"
        output.mkdir(parents=True)
        (output / "feed.canonical.json").write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
        return ledger, event, config, world, output

    @staticmethod
    def _app(ledger, event, config, world):
        app = service_module.StarModeService()
        app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
        app._spotlight = lambda *_args, **_kwargs: _spotlight()
        app._dashboard = lambda _config, _diagnostics, _ledger, _world, _event, **kwargs: {
            "feed": kwargs.get("feed"),
            "run": kwargs.get("run"),
        }
        app._persist_current_capsule = lambda *_args, **_kwargs: {}
        return app

    def test_local_model_renders_whole_bundle_and_never_receives_private_context(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="local_auto")
            personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "비공개 식사", "detail": "혼자만 아는 메뉴", "visibility": "private"},
                universe_id="world-one", protagonist_id=7, player_label="Test Player", game_date="2027-07-24",
            )
            personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "공개 루틴", "detail": "팬에게 공개한 준비 습관", "visibility": "public"},
                universe_id="world-one", protagonist_id=7, player_label="Test Player", game_date="2027-07-24",
            )
            app = self._app(ledger, event, config, world)

            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.narration_module,
                "structured_feed",
                side_effect=lambda _system, user, **_kwargs: (_model_response(user), "local-fixture"),
            ) as local:
                dashboard = app.enrich_feed(lambda *_args: None)

            sent = local.call_args.args[1]
            expected_batches = len(
                provider_feed.expression_batches(
                    json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
                )
            )
            self.assertEqual(expected_batches, local.call_count)
            self.assertIn("공개 루틴", sent)
            self.assertNotIn("비공개 식사", sent)
            self.assertEqual("local_llm_expression", dashboard["feed"]["reaction_bundle"]["renderer"])
            self.assertEqual("sequential_batches", dashboard["feed"]["reaction_bundle"]["generation_mode"])
            self.assertEqual(expected_batches, dashboard["feed"]["reaction_bundle"]["batch_count"])
            self.assertEqual(2, len(dashboard["feed"]["social"]))
            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            current = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual("deterministic", canonical["reaction_bundle"]["renderer"])
            self.assertEqual("local_llm_expression", current["reaction_bundle"]["renderer"])

    def test_gemini_failure_falls_back_to_the_same_local_bundle_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, _output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            failure = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "synthetic outage", retryable=True
            )
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "_ensure_local_llm", return_value=True
            ), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=failure
            ) as gemini, patch.object(
                service_module.narration_module,
                "structured_feed",
                side_effect=lambda _system, user, **_kwargs: (_model_response(user), "local-fixture"),
            ) as local:
                dashboard = app.enrich_feed(lambda *_args: None)

            # Each batch is written independently, so an outage costs one
            # attempt per batch and the local model then writes all of them.
            self.assertEqual(3, gemini.call_count)
            self.assertEqual(3, local.call_count)
            bundle = dashboard["feed"]["reaction_bundle"]
            self.assertEqual("local_llm_expression", bundle["renderer"])
            self.assertEqual("REMOTE_UNAVAILABLE", bundle["fallback_chain"][0]["code"])

    def test_gemini_writes_each_surface_batch_in_order_and_aggregates_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            packets = []

            def generate(_system, user, **_kwargs):
                packet = json.loads(user)
                packets.append(packet)
                ordinal = len(packets)
                return service_module.gemini_provider.ProviderResult(
                    text=_model_response(user),
                    model=service_module.gemini_provider.DEFAULT_MODEL,
                    request_hash=f"request-{ordinal}",
                    request_bytes=100 + ordinal,
                    response_bytes=200 + ordinal,
                    finish_reason="STOP",
                    request_count=1,
                    prompt_tokens=10,
                    output_tokens=20,
                    total_tokens=30,
                    metered_responses=1,
                )

            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            expected_batches = provider_feed.expression_batches(canonical)
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=generate
            ) as gemini, patch.object(app, "_record_gemini_generation") as record:
                dashboard = app.enrich_feed(lambda *_args: None)

            self.assertEqual(len(expected_batches), gemini.call_count)
            self.assertEqual(
                [row["kind"] for row in expected_batches],
                [packet["sequential_batch"]["surface"] for packet in packets],
            )
            self.assertEqual(
                list(range(1, len(expected_batches) + 1)),
                [packet["sequential_batch"]["ordinal"] for packet in packets],
            )
            for packet in packets:
                nonempty = [
                    key for key in ("articles", "boards", "social") if packet["output_contract"][key]
                ]
                self.assertEqual([packet["sequential_batch"]["surface"]], nonempty)
            aggregate = record.call_args.args[1]
            self.assertEqual(len(expected_batches), aggregate.request_count)
            self.assertEqual(10 * len(expected_batches), aggregate.prompt_tokens)
            self.assertEqual(20 * len(expected_batches), aggregate.output_tokens)
            bundle = dashboard["feed"]["reaction_bundle"]
            self.assertEqual("gemini_expression", bundle["renderer"])
            self.assertEqual("sequential_batches", bundle["generation_mode"])
            self.assertEqual(len(expected_batches), bundle["batch_count"])

    def test_article_and_community_gemini_jobs_are_independent_and_merge_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            calls = []

            def generate(_system, user, **_kwargs):
                packet = json.loads(user)
                calls.append(packet)
                return service_module.gemini_provider.ProviderResult(
                    text=_model_response(user),
                    model=service_module.gemini_provider.DEFAULT_MODEL,
                    request_hash=f"request-{len(calls)}",
                    request_bytes=100,
                    response_bytes=200,
                    finish_reason="STOP",
                    request_count=1,
                    prompt_tokens=10,
                    output_tokens=20,
                    total_tokens=30,
                    metered_responses=1,
                )

            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            article_batches = provider_feed.scoped_expression_batches(canonical, "articles")
            community_batches = provider_feed.scoped_expression_batches(canonical, "community")
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=generate
            ), patch.object(app, "_record_gemini_generation") as record:
                article_dashboard = app.enrich_feed(lambda *_args: None, {"surface_scope": "articles"})
                article_call_count = len(calls)
                community_dashboard = app.enrich_feed(lambda *_args: None, {"surface_scope": "community"})

            self.assertEqual(len(article_batches), article_call_count)
            self.assertEqual(len(article_batches) + len(community_batches), len(calls))
            self.assertEqual(
                ["articles"] * len(article_batches),
                [packet["sequential_batch"]["surface"] for packet in calls[:article_call_count]],
            )
            self.assertEqual(
                [row["kind"] for row in community_batches],
                [packet["sequential_batch"]["surface"] for packet in calls[article_call_count:]],
            )
            self.assertEqual("mixed_expression", article_dashboard["feed"]["reaction_bundle"]["renderer"])
            self.assertEqual(canonical["boards"], article_dashboard["feed"]["boards"])
            self.assertEqual(canonical["social"], article_dashboard["feed"]["social"])
            self.assertNotEqual(canonical["media"], article_dashboard["feed"]["media"])
            self.assertEqual(
                article_dashboard["feed"]["media"],
                community_dashboard["feed"]["media"],
            )
            self.assertEqual("gemini_expression", community_dashboard["feed"]["reaction_bundle"]["renderer"])
            self.assertEqual(
                ["reaction_articles", "reaction_community"],
                [call.args[2] for call in record.call_args_list],
            )

    def test_failed_scoped_gemini_job_preserves_the_existing_feed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            current = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            current["media"][0]["title"] = "Test Player, 이미 보존된 기사 제목"
            feed_path = output / "feed.json"
            feed_path.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
            before = feed_path.read_bytes()
            failure = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "synthetic outage", retryable=True
            )

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=failure
            ), patch.object(app, "_ensure_local_llm", return_value=False), patch.object(
                app, "_record_gemini_failure"
            ):
                dashboard = app.enrich_feed(lambda *_args: None, {"surface_scope": "community"})

            self.assertEqual(before, feed_path.read_bytes())
            self.assertEqual("Test Player, 이미 보존된 기사 제목", dashboard["feed"]["media"][0]["title"])
            self.assertFalse(dashboard["run"]["regenerated"])
            self.assertEqual("community", dashboard["run"]["surface_scope"])

    def test_only_truncated_gemini_batch_is_retried_with_compact_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            packets = []
            updates = []

            def generate(_system, user, **_kwargs):
                packet = json.loads(user)
                packets.append(packet)
                # Batches are written concurrently, so the truncation is bound
                # to one batch rather than to the first call that arrives.
                if packet["sequential_batch"]["ordinal"] == 1 and not packet["sequential_batch"]["compact_retry"]:
                    failure = service_module.gemini_provider.GeminiProviderError(
                        "TRUNCATED_RESPONSE",
                        "synthetic truncation",
                        usage={"prompt_tokens": 11, "output_tokens": 19, "total_tokens": 30},
                    )
                    failure.request_count = 1
                    failure.metered_responses = 1
                    raise failure
                ordinal = len(packets)
                return service_module.gemini_provider.ProviderResult(
                    text=_model_response(user),
                    model=service_module.gemini_provider.DEFAULT_MODEL,
                    request_hash=f"request-{ordinal}",
                    request_bytes=100,
                    response_bytes=200,
                    finish_reason="STOP",
                    request_count=1,
                    prompt_tokens=10,
                    output_tokens=20,
                    total_tokens=30,
                    metered_responses=1,
                )

            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            expected_batches = provider_feed.expression_batches(canonical)
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=generate
            ) as gemini, patch.object(app, "_record_gemini_generation") as record:
                dashboard = app.enrich_feed(lambda *args: updates.append(args))

            self.assertEqual(len(expected_batches) + 1, gemini.call_count)
            first = [row for row in packets if row["sequential_batch"]["ordinal"] == 1]
            self.assertEqual(2, len(first))
            self.assertEqual(
                first[0]["sequential_batch"]["batch_id"],
                first[1]["sequential_batch"]["batch_id"],
            )
            self.assertFalse(first[0]["sequential_batch"]["compact_retry"])
            self.assertTrue(first[1]["sequential_batch"]["compact_retry"])
            self.assertLess(
                first[1]["sequential_batch"]["visible_text_limits"]["paragraph_chars"],
                first[0]["sequential_batch"]["visible_text_limits"]["paragraph_chars"],
            )
            self.assertTrue(any("응답만 잘려" in str(args[0]) for args in updates))
            aggregate = record.call_args.args[1]
            self.assertEqual(len(expected_batches) + 1, aggregate.request_count)
            self.assertEqual("gemini_expression", dashboard["feed"]["reaction_bundle"]["renderer"])

    def test_partial_remote_batches_are_never_published_after_a_later_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            calls = 0

            def generate(_system, user, **_kwargs):
                nonlocal calls
                calls += 1
                if json.loads(user)["sequential_batch"]["ordinal"] == 2:
                    failure = service_module.gemini_provider.GeminiProviderError(
                        "REMOTE_UNAVAILABLE", "synthetic second-batch outage"
                    )
                    failure.request_count = 1
                    raise failure
                return service_module.gemini_provider.ProviderResult(
                    text=_model_response(user),
                    model=service_module.gemini_provider.DEFAULT_MODEL,
                    request_hash="first-batch",
                    request_bytes=100,
                    response_bytes=200,
                    finish_reason="STOP",
                )

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=generate
            ), patch.object(app, "_ensure_local_llm", return_value=False), patch.object(
                app, "_record_gemini_failure"
            ):
                dashboard = app.enrich_feed(lambda *_args: None)

            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            published = json.loads((output / "feed.json").read_text(encoding="utf-8"))
            self.assertEqual(3, calls)
            self.assertEqual("deterministic", dashboard["feed"]["reaction_bundle"]["renderer"])
            self.assertEqual(
                provider_feed.expression_contract(canonical),
                provider_feed.expression_contract(published),
            )

    def test_local_only_article_job_never_calls_gemini_and_uses_selected_language_level(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, event, config, world, output = self._fixture(temp, provider="gemini")
            app = self._app(ledger, event, config, world)
            packets = []

            def local_generate(_system, user, **_kwargs):
                packets.append(json.loads(user))
                return _model_response(user), "local-fixture"

            canonical = json.loads((output / "feed.canonical.json").read_text(encoding="utf-8"))
            expected = provider_feed.scoped_expression_batches(canonical, "articles")
            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.gemini_provider,
                "generate_text",
                side_effect=AssertionError("local-only jobs must not call Gemini"),
            ) as gemini, patch.object(
                service_module.narration_module,
                "structured_feed",
                side_effect=local_generate,
            ) as local:
                dashboard = app.enrich_feed(
                    lambda *_args: None,
                    {
                        "provider": "local_only",
                        "surface_scope": "articles",
                        "community_language_level": 5,
                    },
                )

            self.assertEqual(0, gemini.call_count)
            self.assertEqual(len(expected), local.call_count)
            self.assertTrue(
                all(packet["style_controls"]["community_language_level"] == 5 for packet in packets)
            )
            bundle = dashboard["feed"]["reaction_bundle"]
            self.assertEqual("local_llm", bundle["surface_sources"]["articles"]["provider"])
            self.assertEqual(5, bundle["community_language_level"])


if __name__ == "__main__":
    unittest.main()
