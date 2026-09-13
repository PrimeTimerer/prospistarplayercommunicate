#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ledger_v2 import Ledger
import service as service_module
from gemini_provider import ProviderResult


def snapshot():
    return {
        "player": {"id": 7, "name": "Test Player", "team": "TEST", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
        "stats": {
            "pit_IP": 20,
            "pit_TBF": 70,
            "pit_H": 3,
            "pit_K": 40,
            "pit_W": 2,
            "bat_AB": 0,
            "bat_H": 0,
            "bat_HR": 0,
            "bat_RBI": 0,
            "bat_R": 0,
            "bat_SO": 0,
            "bat_SB": 0,
        },
        "content_hash": "verified",
        "profile_fingerprint": "profile",
        "validation": {},
        "provenance": {},
    }


class StoryServiceTests(unittest.TestCase):
    def _fixture(self, root):
        snap = snapshot()
        ledger = Ledger(str(Path(root) / "ledger.json"), "world-one")
        ledger.commit(ledger.classify(snap))
        event = ledger.classify(snap)
        config = {
            "output_dir": str(Path(root) / "output"),
            "shots_dir": str(Path(root) / "shots"),
            "save_path": str(Path(root) / "save.dat"),
            "heat": 7,
            "mode": "standard",
            "platforms": ["dc"],
            "persona": "",
            "auto_narrate": False,
            "auto_capture": False,
            "theme": "dark",
        }
        Path(config["save_path"]).write_bytes(b"save")
        world = {"world_id": "world-one", "generation": 1, "slot": "00"}
        return snap, ledger, event, config, world

    def test_button_turn_updates_dashboard_feed_and_physical_capsule(self):
        with tempfile.TemporaryDirectory() as temp:
            snap, ledger, event, config, world = self._fixture(temp)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            result = app.create_story_event(
                {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
            )
            self.assertEqual("button", result["item"]["source"])
            self.assertEqual(1, result["dashboard"]["story"]["current"]["sequence"])
            self.assertEqual(1, result["dashboard"]["feed"]["story_overlay_count"])
            capsule = Path(config["output_dir"]) / "worlds" / "world-one" / "history" / "2027" / "07" / "24" / "capsule.json"
            self.assertTrue(capsule.is_file())

    def test_local_llm_turn_is_stored_in_same_session(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "비공개 루틴", "detail": "등판 전 재즈를 듣는다", "visibility": "private"},
                universe_id=world["world_id"], protagonist_id=7,
                player_label="Test Player", game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
                service_module.narration_module,
                "story_chat",
                return_value=("모델이 이어 쓴 장면", "local-test"),
            ) as local_chat:
                result = app.chat_story(
                    {
                        "category": "career",
                        "situation": "meet_agent",
                        "target": "agent",
                        "visibility": "private",
                        "user_text": "내 시즌 가치를 정직하게 말해줘.",
                    }
                )
            item = result["item"]
            self.assertEqual("llm", item["source"])
            self.assertEqual("llm_generated_fiction", item["provenance"])
            self.assertEqual("모델이 이어 쓴 장면", item["scene"]["response"])
            self.assertEqual(1, ledger.state["story_sessions"]["2027-07-24"]["sequence"])
            self.assertIn("비공개 루틴", local_chat.call_args.args[1])

    def test_local_cinematic_narrative_normalizes_inline_numbered_scenes_before_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            raw = "**1. 첫 장면.** 본문 하나. **2. 다음 장면.** 본문 둘."
            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                app, "_shots", return_value=[]
            ), patch.object(
                service_module.narration_module, "narrate", return_value=(raw, "local-test")
            ):
                dashboard = app.generate_narrative(lambda *_args: None)

            expected = "## 1. 첫 장면.\n\n본문 하나.\n\n## 2. 다음 장면.\n\n본문 둘."
            self.assertEqual(expected, dashboard["narrative"])
            stored = Path(config["output_dir"]) / "worlds" / world["world_id"] / "narrative.md"
            self.assertEqual(expected, stored.read_text(encoding="utf-8"))

    def test_gemini_turn_uses_explicit_consent_and_stays_in_the_bound_world(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "외부 전달 금지", "detail": "개인적인 루틴", "visibility": "private"},
                universe_id=world["world_id"], protagonist_id=7,
                player_label="Test Player", game_date="2027-07-24",
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "공개 독서 취향", "detail": "인터뷰에서도 말한 책", "visibility": "public", "remote_allowed": True},
                universe_id=world["world_id"], protagonist_id=7,
                player_label="Test Player", game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            remote = ProviderResult(
                text="Gemini가 이어 쓴 장면",
                model=service_module.gemini_provider.DEFAULT_MODEL,
                request_hash="hash",
                request_bytes=123,
                response_bytes=456,
                finish_reason="STOP",
            )
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", return_value=remote
            ) as generate, patch.object(
                service_module.narration_module, "story_chat"
            ) as local_chat:
                result = app.chat_story(
                    {
                        "category": "career",
                        "situation": "meet_agent",
                        "target": "agent",
                        "visibility": "private",
                        "user_text": "내 시즌 가치를 정직하게 말해줘.",
                        "renderer_preference": "gemini",
                    }
                )

            self.assertEqual("gemini", result["renderer"])
            self.assertEqual("Gemini가 이어 쓴 장면", result["item"]["scene"]["response"])
            self.assertEqual("gemini:" + remote.model, result["item"]["model"])
            self.assertEqual("hash", result["provider_audit"]["request_hash"])
            self.assertNotIn("test-secret", str(result))
            generate.assert_called_once()
            self.assertIn("공개 독서 취향", generate.call_args.args[1])
            self.assertNotIn("외부 전달 금지", generate.call_args.args[1])
            local_chat.assert_not_called()

    def test_gemini_turn_refuses_missing_consent_before_network_access(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update({"ai_provider": "gemini", "gemini_consent": False})
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            with patch.object(service_module.gemini_provider, "generate_text") as generate:
                with self.assertRaisesRegex(RuntimeError, "동의"):
                    app.chat_story(
                        {
                            "category": "career",
                            "situation": "meet_agent",
                            "target": "agent",
                            "visibility": "private",
                            "user_text": "이야기를 이어줘.",
                            "renderer_preference": "gemini",
                        }
                    )
            generate.assert_not_called()

    def test_gemini_chat_failure_uses_local_context_and_keeps_bounded_fallback_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {
                    "basis": "user_preference",
                    "label": "비공개 햄버거 약속",
                    "detail": "팀 동료와만 공유한다",
                    "visibility": "private",
                },
                universe_id=world["world_id"],
                protagonist_id=7,
                player_label="Test Player",
                game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            provider_error = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "upstream-private-detail", retryable=True
            )
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=provider_error
            ) as generate, patch.object(
                service_module, "_llm_reachable", return_value=True
            ), patch.object(
                service_module.narration_module,
                "story_chat",
                return_value=("로컬 모델이 이어 쓴 장면", "local-test"),
            ) as local_chat:
                result = app.chat_story(
                    {
                        "category": "career",
                        "situation": "meet_agent",
                        "target": "agent",
                        "visibility": "private",
                        "user_text": "오늘 이야기를 이어줘.",
                        "renderer_preference": "gemini",
                    }
                )

            self.assertEqual("llm", result["renderer"])
            self.assertEqual("로컬 모델이 이어 쓴 장면", result["item"]["scene"]["response"])
            self.assertEqual("local_llm", result["provider_audit"]["provider"])
            self.assertEqual(
                "REMOTE_UNAVAILABLE",
                result["provider_audit"]["fallback_chain"][0]["code"],
            )
            self.assertNotIn("비공개 햄버거 약속", generate.call_args.args[1])
            self.assertIn("비공개 햄버거 약속", local_chat.call_args.args[1])
            self.assertNotIn("upstream-private-detail", str(result))

    def test_gemini_chat_failure_without_local_model_uses_deterministic_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            provider_error = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "upstream-private-detail", retryable=True
            )
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=provider_error
            ), patch.object(
                service_module, "_llm_reachable", return_value=False
            ), patch.object(
                service_module, "_probe_llm_ports", return_value=False
            ):
                result = app.chat_story(
                    {
                        "category": "career",
                        "situation": "meet_agent",
                        "target": "agent",
                        "visibility": "private",
                        "user_text": "감독과 이야기를 이어가자.",
                        "renderer_preference": "gemini",
                    }
                )

            self.assertEqual("deterministic", result["renderer"])
            self.assertEqual("provider_chain_exhausted", result["fallback"]["reason"])
            self.assertEqual("NOT_REACHABLE", result["fallback"]["attempts"][-1]["code"])
            self.assertEqual(1, len(ledger.state["conversation_turns"]))
            self.assertNotIn("upstream-private-detail", str(result))

    def test_gemini_narrative_sends_text_only_and_records_bounded_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                    "auto_capture": True,
                }
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {"basis": "authored_background", "label": "비공개 성장 배경", "detail": "외부로 보내지 않음", "visibility": "private"},
                universe_id=world["world_id"], protagonist_id=7,
                player_label="Test Player", game_date="2027-07-24",
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {"basis": "user_preference", "label": "공개 음악 취향", "detail": "공개 인터뷰 소재", "visibility": "public", "remote_allowed": True},
                universe_id=world["world_id"], protagonist_id=7,
                player_label="Test Player", game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            remote = ProviderResult(
                text="검증 사실을 바탕으로 이어 쓴 장문 서사",
                model=service_module.gemini_provider.DEFAULT_MODEL,
                request_hash="request-hash",
                request_bytes=321,
                response_bytes=654,
                finish_reason="STOP",
            )
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "capture_game"
            ) as capture, patch.object(
                app, "_shots", return_value=[str(Path(temp) / "must-not-send.png")]
            ) as shots, patch.object(
                service_module.gemini_provider, "generate_text", return_value=remote
            ) as generate:
                dashboard = app.generate_narrative(lambda *_args: None)

            capture.assert_not_called()
            shots.assert_not_called()
            args, kwargs = generate.call_args
            self.assertEqual(2, len(args))
            self.assertNotIn("images", kwargs)
            self.assertEqual("test-secret", kwargs["api_key"])
            self.assertIn("공개 음악 취향", args[1])
            self.assertNotIn("비공개 성장 배경", args[1])
            self.assertEqual("gemini:" + remote.model, dashboard["run"]["narrative_model"])
            self.assertEqual(0, dashboard["run"]["provider_audit"]["images_sent"])
            self.assertNotIn("test-secret", str(dashboard))
            self.assertEqual(1, dashboard["config"]["gemini_usage"]["total"]["calls"])
            self.assertTrue((Path(temp) / "provider-usage.json").is_file())

    def test_gemini_narrative_surfaces_transient_retry_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            remote = ProviderResult(
                text="재시도 뒤 완성된 서사",
                model=config["gemini_model"],
                request_hash="retry-hash",
                request_bytes=300,
                response_bytes=100,
                finish_reason="STOP",
                request_count=2,
            )
            updates = []

            def generate(*_args, **kwargs):
                kwargs["on_transient_retry"](
                    {"retry": 1, "max_retries": 3, "code": "REMOTE_UNAVAILABLE", "delay_seconds": 0.75}
                )
                return remote

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=generate
            ):
                app.generate_narrative(lambda *args: updates.append(args))

            retry_updates = [row for row in updates if len(row) >= 3 and row[2] == "Gemini 자동 재시도"]
            self.assertEqual(1, len(retry_updates))
            self.assertIn("1/3", retry_updates[0][0])
            self.assertIn("0.8초 후", retry_updates[0][0])
            self.assertEqual(50, retry_updates[0][1])

    def test_gemini_narrative_failure_falls_back_to_local_with_private_context(self):
        with tempfile.TemporaryDirectory() as temp:
            _snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            service_module.personal_context.upsert_user_context(
                ledger.state,
                {
                    "basis": "user_preference",
                    "label": "비공개 경기 전 루틴",
                    "detail": "혼자 음악을 듣는다",
                    "visibility": "private",
                },
                universe_id=world["world_id"],
                protagonist_id=7,
                player_label="Test Player",
                game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            provider_error = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "upstream-private-detail", retryable=True
            )
            updates = []
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "_ensure_local_llm", return_value=True
            ), patch.object(
                app, "_shots", return_value=[]
            ), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=provider_error
            ) as generate, patch.object(
                service_module.narration_module,
                "narrate",
                return_value=("## 로컬 대체\n\n세계선은 계속 이어졌다.", "local-test"),
            ) as local_narrate:
                dashboard = app.generate_narrative(lambda *args: updates.append(args))

            self.assertEqual("local-test", dashboard["run"]["narrative_model"])
            self.assertEqual("local_llm", dashboard["run"]["provider_audit"]["provider"])
            self.assertEqual(
                "REMOTE_UNAVAILABLE",
                dashboard["run"]["provider_audit"]["fallback_chain"][0]["code"],
            )
            self.assertNotIn("비공개 경기 전 루틴", generate.call_args.args[1])
            self.assertIn("비공개 경기 전 루틴", local_narrate.call_args.args[0])
            self.assertTrue(any(len(row) >= 3 and row[2] == "로컬 서사 대체" for row in updates))
            self.assertNotIn("upstream-private-detail", str(dashboard))

    def test_gemini_and_local_narrative_failure_uses_built_in_feed_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            output_dir = Path(config["output_dir"]) / "worlds" / world["world_id"]
            output_dir.mkdir(parents=True)
            canonical = {
                "player": snap["player"],
                "event": {"date": snap["date"], "game_lines": ["9이닝 20탈삼진"], "milestones": []},
                "media": [
                    {
                        "outlet": "가상 야구일보",
                        "title": "검증 기록을 다시 보다",
                        "sub": "세계선 기록실",
                        "body": ["기록원은 저장된 수치만 놓고 다음 경기를 전망했다."],
                    }
                ],
                "boards": [
                    {
                        "board": "가상 게시판",
                        "title": "오늘 기록 어땠나",
                        "comments": [{"author": "익명7", "text": "다음 등판도 지켜보자."}],
                    }
                ],
                "social": [],
            }
            (output_dir / "feed.canonical.json").write_text(
                json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")
            provider_error = service_module.gemini_provider.GeminiProviderError(
                "REMOTE_UNAVAILABLE", "upstream-private-detail", retryable=True
            )
            updates = []
            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                app, "_ensure_local_llm", return_value=False
            ), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=provider_error
            ):
                dashboard = app.generate_narrative(lambda *args: updates.append(args))

            self.assertEqual("builtin:deterministic-feed", dashboard["run"]["narrative_model"])
            self.assertEqual("builtin", dashboard["run"]["provider_audit"]["provider"])
            self.assertIn("검증 기록을 다시 보다", dashboard["narrative"])
            self.assertIn("다음 등판도 지켜보자", dashboard["narrative"])
            self.assertTrue(any(len(row) >= 3 and row[2] == "내장 서사 대체" for row in updates))
            self.assertNotIn("upstream-private-detail", str(dashboard))

    def test_provider_response_is_rejected_after_same_date_snapshot_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            snap, ledger, event, config, world = self._fixture(temp)
            config.update(
                {
                    "ai_provider": "gemini",
                    "gemini_consent": True,
                    "gemini_model": service_module.gemini_provider.DEFAULT_MODEL,
                }
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
            secret_store = Mock()
            secret_store.get_gemini_key.return_value = ("test-secret", "windows_account")

            def changed_snapshot(*_args, **_kwargs):
                snap["content_hash"] = "same-date-corrected-save"
                return ProviderResult(
                    text="이전 사실로 만든 응답",
                    model=service_module.gemini_provider.DEFAULT_MODEL,
                    request_hash="request-hash",
                    request_bytes=10,
                    response_bytes=20,
                    finish_reason="STOP",
                )

            with patch.object(app, "_secret_store", return_value=secret_store), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=changed_snapshot
            ):
                with self.assertRaisesRegex(RuntimeError, "세이브 문맥이 바뀌어"):
                    app.generate_narrative(lambda *_args: None)

            narrative = Path(config["output_dir"]) / "worlds" / world["world_id"] / "narrative.md"
            self.assertFalse(narrative.exists())

    def test_provider_response_is_rejected_after_player_context_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            snap, ledger, event, config, world = self._fixture(temp)
            original = service_module.personal_context.upsert_user_context(
                ledger.state,
                {
                    "basis": "user_preference",
                    "label": "경기 전 음악",
                    "detail": "등판 전 재즈를 듣는다",
                    "visibility": "private",
                },
                universe_id=world["world_id"],
                protagonist_id=7,
                player_label="Test Player",
                game_date="2027-07-24",
            )
            app = service_module.StarModeService()
            app._prepare = lambda update=None: (config, {}, None, ledger, world, event)

            def change_context(_system_text, _user_text):
                service_module.personal_context.upsert_user_context(
                    ledger.state,
                    {
                        "context_id": original["context_id"],
                        "expected_revision": original["revision"],
                        "basis": "user_preference",
                        "label": "경기 전 음악",
                        "detail": "등판 전 클래식을 듣는다",
                        "visibility": "private",
                    },
                    universe_id=world["world_id"],
                    protagonist_id=7,
                    player_label="Test Player",
                    game_date="2027-07-24",
                )
                return "이전 설정으로 생성된 응답", "local-test"

            with patch.object(service_module, "_llm_reachable", return_value=True), patch.object(
                service_module.narration_module, "story_chat", side_effect=change_context
            ):
                with self.assertRaisesRegex(RuntimeError, "세이브 문맥이 바뀌어"):
                    app.chat_story(
                        {
                            "category": "career",
                            "situation": "meet_agent",
                            "target": "agent",
                            "tone": "honest",
                            "visibility": "private",
                            "user_text": "오늘 루틴을 이야기하자",
                            "renderer_preference": "llm",
                        }
                    )

            self.assertIsNone(ledger.current_session(snap))


class ConcurrentMutationTests(unittest.TestCase):
    def test_overlapping_story_turns_are_all_kept(self):
        import threading
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as temp:
            snap = snapshot()
            snap["source"] = {"file": "StarPlayer.dat", "slot": "00"}
            snap["validation"] = {"container": "verified", "verified_chunks": 51, "chunk_count": 51}
            config = {
                "output_dir": str(Path(temp) / "output"),
                "shots_dir": str(Path(temp) / "shots"),
                "data_dir": str(Path(temp) / "data"),
                "ledger_path": None,
                "save_path": str(Path(temp) / "save.dat"),
                "heat": 7,
                "mode": "standard",
                "platforms": ["dc"],
                "persona": "",
                "auto_narrate": False,
                "auto_capture": False,
                "theme": "dark",
            }
            Path(config["save_path"]).write_bytes(b"save")
            app = service_module.StarModeService()
            payload = {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
            errors: list[BaseException] = []

            def worker():
                try:
                    for _ in range(4):
                        app.create_story_event(payload)
                except BaseException as exc:  # pragma: no cover - reported below
                    errors.append(exc)

            with _patch.object(
                service_module.config_module, "load_with_diagnostics", return_value=(config, {})
            ), _patch.object(service_module.save_reader, "read_snapshot", return_value=snap), _patch.object(
                service_module, "_llm_reachable", return_value=False
            ):
                app.check_save(lambda *_args: None)
                threads = [threading.Thread(target=worker) for _ in range(3)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)
                self.assertEqual([], errors)
                dashboard = app.bootstrap()

            self.assertEqual(12, dashboard["story"]["current"]["sequence"])
            # The ledger on disk must agree with the in-memory result.
            world_id = dashboard["world"]["world_id"]
            ledger = Ledger(str(Path(config["data_dir"]) / "worlds" / world_id / "ledger.json"), world_id)
            self.assertEqual(12, ledger.state["story_sessions"]["2027-07-24"]["sequence"])


if __name__ == "__main__":
    unittest.main()
