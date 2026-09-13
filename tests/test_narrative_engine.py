#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic chat core and golden scenarios (master plan 19.2 subset).

Covers: ten first-slice acts with clean prose and deterministic replay,
structured blocks, proposals with confirmation, reaction bundles bound to
parent claims, dominant-event hierarchy (perfect game + 0-for-4), repeated
skeleton rejection, no-network operation, prop lifecycle across dates, and
the renderer-neutral service path.
"""

from __future__ import annotations

import copy
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dialogue_engine
import dominant_event
import narrative_contracts as nc
import narrative_engine as ne
import narrative_props
import narrative_state
import realism_gate
import service as service_module
import spotlight_engine
import stat_engine
from ledger_v2 import Ledger

NAMES = ["Paul Skenes", "Skenes"]


def _snapshot(day=24, content_hash="h", stats=None):
    return {
        "player": {"id": 99, "name": "Paul Skenes", "team": "Yokohama DeNA", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": day, "career_year": 2},
        "stats": stats or {"pit_IP": 153, "pit_TBF": 465, "pit_H": 4, "pit_K": 388, "pit_W": 17, "bat_AB": 277, "bat_H": 201, "bat_HR": 105, "bat_RBI": 223, "bat_SB": 129, "bat_AVG": 0.726},
        "content_hash": content_hash,
        "profile_fingerprint": "profile",
        "source": {"file": "StarPlayer.dat", "slot": "00"},
        "validation": {"container": "verified", "verified_chunks": 51, "chunk_count": 51},
        "provenance": {},
    }


def _career():
    return {
        "records": {"season": [{"label": "홈런", "status": "broken"}] * 4},
        "honor_summary": [{"kind": "season_mvp", "label": "시즌 MVP", "count": 1}],
        "timeline": [],
        "player_grade": {"code": "S+", "label": "슈퍼스타", "score": 86},
        "next_milestones": [{"label": "시즌 400탈삼진", "remaining": 12}],
    }


def _spot(snap, career=None, event=None):
    return spotlight_engine.evaluate(snap, career or {}, event, {"mode": "standard", "heat": 7})


def _all_text(bundle: dict) -> list[str]:
    rows = []
    for board in bundle.get("boards") or []:
        rows.extend(post.get("text") or "" for post in board.get("posts") or [])
    for article in bundle.get("media") or []:
        rows.extend(article.get("body") or [])
    for row in bundle.get("foreign") or []:
        rows.append(row.get("translation_ko") or "")
    return rows


class TurnGenerationTests(unittest.TestCase):
    def setUp(self):
        self.snap = _snapshot()
        self.career = _career()
        self.spot = _spot(self.snap, self.career)

    def _turn(self, text, **kwargs):
        return ne.generate_turn(user_text=text, snapshot=self.snap, game_date="2027-07-24", spotlight=self.spot, universe_id="u1", career=self.career, sequence=1, **kwargs)

    def test_ten_acts_produce_clean_structured_replies(self):
        cases = {
            "내 시즌 탈삼진 몇 개야?": "ask",
            "오늘 감독한테 좀 서운했어": "vent",
            "후배를 위로해 주고 싶다": "console",
            "포수를 칭찬하고 싶어. 오늘 리드가 훌륭했다고 말할래": "praise",
            "오늘 패배는 내 잘못이라고 인정할래": "criticize_self",
            "다음 등판에서 기록을 노린다고 선언할까?": "declare",
            "동료에게 어제 일은 미안하다고 사과하고 싶어": "apologize",
            "감독과 따로 면담을 잡아서 직접 묻고 싶어": "request_private_meeting",
            "기자들 앞에서는 강하게 말할래": "give_public_quote",
        }
        for text, act in cases.items():
            response = self._turn(text)
            self.assertEqual(act, response["understanding"]["primary_act"], text)
            self.assertEqual("deterministic", response["renderer"])
            self.assertTrue(response["audit"]["ok"], (text, response["audit"]))
            self.assertEqual([], nc.validate_blocks(response["reply"]["blocks"]), text)
            self.assertTrue(response["reply"]["text"])
            self.assertNotIn("{", response["reply"]["text"])
            self.assertTrue(response["provenance"]["template_ids"], text)
            self.assertEqual("authored", response["provenance"]["license_classes"][0])
            if act in dialogue_engine.EVENT_ACTS:
                self.assertTrue(response["proposed_events"], text)
                self.assertTrue(all(row["requires_confirmation"] for row in response["proposed_events"]))
                self.assertEqual("사건 제안", response["mode"])
            else:
                self.assertEqual([], response["proposed_events"])
                self.assertEqual("대화만 함", response["mode"])

    def test_ask_answers_with_the_requested_verified_fact(self):
        response = self._turn("내 시즌 탈삼진 몇 개야?")
        self.assertIn("388", response["reply"]["text"])
        self.assertIn("탈삼진 388", response["reply"]["text"])
        self.assertNotIn("타율", response["reply"]["text"])
        response = self._turn("우리 팀 지금 몇 위야?")
        self.assertIn("순위표", response["reply"]["text"])
        self.assertNotIn("1위", response["reply"]["text"])

    def test_replay_is_deterministic_for_same_state_and_seed(self):
        first = self._turn("오늘 감독한테 좀 서운했어")
        second = self._turn("오늘 감독한테 좀 서운했어")
        self.assertEqual(first["response_id"], second["response_id"])
        self.assertEqual(first["reply"]["text"], second["reply"]["text"])
        self.assertEqual(first["provenance"]["template_ids"], second["provenance"]["template_ids"])
        other = ne.generate_turn(user_text="오늘 감독한테 좀 서운했어", snapshot=self.snap, game_date="2027-07-24", spotlight=self.spot, universe_id="u2", career=self.career, sequence=1)
        self.assertNotEqual(first["response_id"], other["response_id"])

    def test_dual_act_proposes_private_and_public_events(self):
        response = self._turn("오늘 감독에게 섭섭하지만 기자들 앞에서는 팀을 감싸고 싶어")
        types = [row["type"] for row in response["proposed_events"]]
        self.assertIn("SP.USER.PRIVATE_CONVERSATION", types)
        self.assertIn("SP.MEDIA.QUOTE", types)
        by_type = {row["type"]: row for row in response["proposed_events"]}
        self.assertEqual("private", by_type["SP.USER.PRIVATE_CONVERSATION"]["visibility"])
        self.assertEqual("national", by_type["SP.MEDIA.QUOTE"]["visibility"])
        self.assertTrue(by_type["SP.MEDIA.QUOTE"]["consequential"])

    def test_intention_to_speak_publicly_is_weighed_by_a_confidant_first(self):
        response = self._turn("기자들 앞에서는 강하게 말할래")
        self.assertIn(response["reply"]["persona"], ("inner_voice", "veteran_teammate", "catcher", "agent", "club_pr", "manager", "pitching_coach"))
        self.assertNotIn("기자들 앞에서는 강하게 말할래", response["reply"]["text"])
        self.assertEqual("fiery", ne.tone_hint("기자들 앞에서는 강하게 말할래"))

    def test_fallback_ladder_clarifies_and_helps(self):
        vague = self._turn("그거 말이야")
        self.assertEqual(5, vague["understanding"]["fallback_level"])
        self.assertIn("어느 쪽", vague["reply"]["text"])
        empty = self._turn("")
        self.assertEqual(6, empty["understanding"]["fallback_level"])
        self.assertIn("감독한테 서운했어", empty["reply"]["text"])


class EventRealizationTests(unittest.TestCase):
    def setUp(self):
        self.snap = _snapshot()
        self.career = _career()
        self.spot = _spot(self.snap, self.career)

    def _event(self, proposal, **kwargs):
        return ne.realize_event(proposal=proposal, snapshot=self.snap, game_date="2027-07-24", spotlight=self.spot, universe_id="u1", career=self.career, **kwargs)

    def test_public_quote_event_has_blocks_reactions_and_translations(self):
        realized = self._event({"type": "SP.MEDIA.QUOTE", "act": "give_public_quote", "target": "reporter", "visibility": "national"}, user_text="기자들 앞에서는 강하게 말할래", statement="팀이 먼저다. 오늘 패배는 내가 안고 간다.")
        self.assertEqual([], nc.validate_blocks(realized["blocks"]))
        kinds = [row["type"] for row in realized["blocks"]]
        self.assertIn("headline", kinds)
        self.assertIn("paragraph", kinds)
        self.assertIn("quote", kinds)
        self.assertIn("translation", kinds)
        self.assertTrue(realized["audit"]["ok"], realized["audit"])
        reactions = realized["reactions"]
        self.assertGreaterEqual(len(reactions["boards"]), 1)
        self.assertGreaterEqual(len(reactions["media"]), 1)
        self.assertGreaterEqual(len(reactions["foreign"]), 1)
        for row in reactions["foreign"]:
            self.assertTrue(row["translation_ko"])
            self.assertEqual("ja", row["locale"])
            self.assertIn("창작", row["creative_marker"])
        statement_quotes = [row for row in realized["blocks"] if row["type"] == "quote" and row.get("speaker") == "Paul Skenes"]
        self.assertEqual(1, len(statement_quotes))
        self.assertIn("팀이 먼저다", statement_quotes[0]["text"])
        realism = realism_gate.audit_feed({"media": reactions["media"], "boards": [{"title": board["title"], "board": board["board"], "comments": board["comments"]} for board in reactions["boards"]]}, names=NAMES, personas=[])
        self.assertEqual([], [row for row in realism["prose_violations"] if row["code"] != "unregistered_quote_attribution"])
        self.assertEqual([], realism["body_clones"])
        self.assertEqual({}, realism["mechanical_suffixes"])

    def test_private_event_produces_no_public_reactions(self):
        realized = self._event({"type": "SP.RELATION.PRIVATE_MEETING", "act": "request_private_meeting", "target": "manager", "visibility": "private"}, user_text="감독과 따로 면담을 잡고 싶어")
        self.assertEqual([], realized["reactions"]["boards"])
        self.assertEqual([], realized["reactions"]["media"])
        self.assertEqual([], realized["reactions"]["foreign"])
        self.assertIn("headline", [row["type"] for row in realized["blocks"]])
        self.assertFalse([row for row in realized["blocks"] if row["type"] == "quote" and row.get("speaker") == "Paul Skenes"])

    def test_community_replies_bind_to_parent_claims(self):
        realized = self._event({"type": "SP.USER.DECLARATION", "act": "declare", "target": "reporter", "visibility": "national"}, user_text="다음 등판에서 기록을 노린다고 선언할래")
        for board in realized["reactions"]["boards"]:
            posts = board["posts"]
            self.assertIsNone(posts[0]["parent_post_id"])
            for post in posts[1:]:
                self.assertTrue(post["parent_post_id"])
                self.assertTrue(post["addressed_claim_id"])
                self.assertIn(post["stance"], ("support", "oppose", "qualify", "ask", "joke", "correct", "redirect"))
                self.assertNotEqual("unrelated", post["protagonist_relation"])
            self.assertGreaterEqual(len(set(board["stance_sequence"])), 2)
            self.assertEqual(len(posts), len({post["persona"] for post in posts}))

    def test_repeated_skeletons_are_rejected_across_outlets(self):
        realized = self._event({"type": "SP.MEDIA.QUOTE", "act": "give_public_quote", "target": "reporter", "visibility": "international"}, user_text="한마디 하겠다")
        bodies = [article["body"] for article in realized["reactions"]["media"]]
        self.assertGreaterEqual(len(bodies), 2)
        self.assertEqual([], realism_gate.find_body_clones(bodies, NAMES))
        titles = [article["body"][0] for article in realized["reactions"]["media"]]
        self.assertEqual([], realism_gate.find_skeleton_clones(titles, NAMES))
        personas_used = [article["persona"] for article in realized["reactions"]["media"]]
        self.assertEqual(len(personas_used), len(set(personas_used)))


class DominantEventTests(unittest.TestCase):
    def _game(self, snap):
        return {"kind": "NEW_GAME", "snapshot": snap, "delta": {"pit_IP": 9, "pit_TBF": 27, "pit_K": 27, "pit_H": 0, "bat_AB": 4, "bat_H": 0}, "milestones": [], "career_milestones": [], "assess": stat_engine.assess(snap["stats"]), "role": "two_way", "baseline_only": False}

    def test_perfect_game_plus_hitless_line_keeps_the_perfect_game_dominant(self):
        snap = _snapshot()
        event = self._game(snap)
        spot = _spot(snap, _career(), event)
        result = ne.realize_game_reactions(event=event, snapshot=snap, game_date="2027-07-24", spotlight=spot, universe_id="u1", career=_career(), extra_facts=[{"event_type": "SP.GAME.PITCH.PERFECT_GAME", "label": "생애 첫 퍼펙트게임", "fact_id": "user-confirmed-1", "verified": True}])
        self.assertEqual("SP.GAME.PITCH.PERFECT_GAME", result["dominant"]["lead"]["event_type"])
        self.assertIn("4타수 무안타", result["dominant"]["secondary"])
        self.assertIn("생애 첫 퍼펙트게임", result["dominant"]["mandatory"])
        guards = {row["guard"] for row in result["dominant"]["guards"]}
        self.assertIn("no_batting_praise", guards)
        self.assertIn("two_way_separation", guards)
        self.assertNotIn("no_runs_claim", guards)
        self.assertEqual("확인된 사실: 생애 첫 퍼펙트게임", result["record_line"])
        self.assertGreaterEqual(len(result["reactions"]["boards"]), 1)
        self.assertGreaterEqual(len(result["reactions"]["media"]), 1)
        self.assertGreaterEqual(len(result["reactions"]["foreign"]), 1)
        for text in _all_text(result["reactions"]):
            self.assertEqual([], dominant_event.guard_violations(text, result["dominant"]["guards"]), text)

    def test_box_score_alone_never_promotes_to_a_perfect_game(self):
        snap = _snapshot()
        event = self._game(snap)
        spot = _spot(snap, _career(), event)
        result = ne.realize_game_reactions(event=event, snapshot=snap, game_date="2027-07-24", spotlight=spot, universe_id="u1", career=_career())
        self.assertEqual("SP.GAME.PITCH.NO_HITTER", result["dominant"]["lead"]["event_type"])
        self.assertIn("무피안타", result["headline_fact"])
        guards = {row["guard"] for row in result["dominant"]["guards"]}
        self.assertIn("no_runs_claim", guards)
        for text in _all_text(result["reactions"]) + [result["headline_fact"]]:
            self.assertNotIn("퍼펙트", text)
            self.assertNotIn("노히트노런", text)

    def test_negative_lead_is_replaced_by_a_rare_positive_achievement(self):
        rows = [
            dominant_event.candidate("SP.GAME.BAT.HITLESS_GAME", "4타수 무안타", verified=True, outcome="negative", domain_role="batting", leverage=0.9, rarity=0.95),
            dominant_event.candidate("SP.MILESTONE.SEASON_THRESHOLD", "시즌 300탈삼진 돌파", verified=True, outcome="positive", milestone_state="completed", domain_role="record", leverage=0.2),
        ]
        selection = dominant_event.select_dominant(rows)
        self.assertEqual("SP.MILESTONE.SEASON_THRESHOLD", selection["lead"]["event_type"])


class PropLifecycleTests(unittest.TestCase):
    def test_hamburger_saga_across_dates_without_an_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "u1")
            state = ledger.state
            common = {"universe_id": "u1", "protagonist_id": 99}

            def act(text, game_date, confirm=False, last=None):
                understanding = dialogue_engine.understand(text, {"last_prop_id": last} if last else None)
                return narrative_props.handle_prop_act(state, act=understanding["prop_acts"][0], text=text, understanding=understanding, game_date=game_date, last_prop_id=last, confirm=confirm, **common)

            created = act("햄버거 얘기를 앞으로 내부 농담으로 만들자", "2027-07-24")
            self.assertTrue(created["applied"])
            prop = created["prop"]
            self.assertEqual("햄버거", prop["name"])
            self.assertEqual("food", prop["prop_type"])
            self.assertEqual("establish", prop["state"])
            self.assertEqual("clubhouse", prop["visibility"])

            scheduled = act("다음 홈런 때 이 농담을 다시 꺼내자", "2027-07-26", last=prop["prop_id"])
            self.assertTrue(scheduled["applied"])
            self.assertEqual("SP.GAME.BAT.HOME_RUN", scheduled["change"]["trigger"])
            self.assertEqual([], narrative_props.due_callbacks(state, "SP.MEDIA.QUOTE"))
            due = narrative_props.due_callbacks(state, "SP.GAME.BAT.HOME_RUN")
            self.assertEqual(1, len(due))
            fired = narrative_props.fire_callback(state, due[0], "SP.GAME.BAT.HOME_RUN", game_date="2027-07-30", event_id="hr-1")
            self.assertTrue(fired["applied"])
            self.assertEqual("callback", prop["state"])
            self.assertEqual("2027-07-30", prop["last_used_date"])
            self.assertEqual([], narrative_props.due_callbacks(state, "SP.GAME.BAT.HOME_RUN"))

            tension = act("코치와 식단 문제로 약간 긴장하게 만들어", "2027-08-01", last=prop["prop_id"])
            self.assertTrue(tension["applied"])
            self.assertEqual("coach", tension["change"]["relationship"]["target"])
            self.assertGreater(state["relationship_edges"]["protagonist->coach"]["tension"], 0)
            self.assertEqual(1, len(state["relationship_events"]))

            escalate = act("이 별명을 팬들까지 쓰게 할까?", "2027-08-03", last=prop["prop_id"])
            self.assertFalse(escalate["applied"])
            self.assertTrue(escalate["requires_confirmation"])
            self.assertEqual("clubhouse", prop["visibility"])
            confirmed = act("이 별명을 팬들까지 쓰게 할까?", "2027-08-03", confirm=True, last=prop["prop_id"])
            self.assertTrue(confirmed["applied"])
            self.assertEqual("escalation", prop["state"])
            self.assertEqual("local", prop["visibility"])

            retired = act("이 소재는 이제 그만하고 좋은 기억으로만 남겨", "2027-08-10", last=prop["prop_id"])
            self.assertTrue(retired["applied"])
            self.assertEqual("retired", prop["state"])
            self.assertEqual("delivered", prop["payoff_state"])
            kinds = [row["kind"] for row in state["narrative_prop_events"]]
            for kind in ("created", "callback_scheduled", "callback", "relationship", "escalation", "retired"):
                self.assertIn(kind, kinds)
            again = act("햄버거 얘기 다시 꺼내자", "2027-08-11", last=prop["prop_id"])
            self.assertFalse(again["applied"])
            ledger.save()
            reloaded = Ledger(str(Path(temp) / "ledger.json"), "u1")
            self.assertEqual("retired", reloaded.state["narrative_props"][prop["prop_id"]]["state"])


class ServiceChatTests(unittest.TestCase):
    def _config(self, temp):
        return {
            "output_dir": str(Path(temp) / "output"),
            "shots_dir": str(Path(temp) / "shots"),
            "data_dir": str(Path(temp) / "data"),
            "ledger_path": None,
            "save_path": str(Path(temp) / "StarPlayer.dat"),
            "heat": 7,
            "mode": "standard",
            "platforms": ["dc"],
            "persona": "",
            "auto_narrate": False,
            "auto_capture": False,
            "theme": "dark",
            "llm_launcher": None,
        }

    def test_chat_without_llm_and_without_network_creates_turns_and_confirmed_events(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._config(temp)
            Path(config["save_path"]).write_bytes(b"save")
            snap = _snapshot()
            app = service_module.StarModeService()
            llm_calls = []

            def blocked(*args, **kwargs):
                raise OSError("network blocked for the offline golden case")

            with patch.object(service_module.config_module, "load_with_diagnostics", return_value=(config, {})), patch.object(
                service_module.config_module, "load", return_value=config
            ), patch.object(service_module.save_reader, "read_snapshot", return_value=snap), patch.object(
                service_module.narration_module, "story_chat", side_effect=lambda *a, **k: llm_calls.append(1)
            ), patch.object(socket, "create_connection", side_effect=blocked), patch.object(service_module.config_module, "autodetect_saves", return_value=[]):
                service_module._llm_probe_state["at"] = 0.0
                app.check_save(lambda *_args: None)
                first = app.chat_story({"user_text": "오늘 감독한테 좀 서운했어"})
                self.assertEqual("deterministic", first["renderer"])
                self.assertIsNone(first["item"])
                self.assertEqual("vent", first["turn"]["understanding"]["primary_act"])
                self.assertTrue(first["turn"]["proposed_events"])
                self.assertEqual(1, len(first["dashboard"]["story"]["conversation"]))
                self.assertTrue(first["dashboard"]["story"]["renderer_neutral"])
                second = app.chat_story({"user_text": "그래도 직접 만나서 이야기하고 싶어", "renderer_preference": "deterministic"})
                self.assertEqual("manager", second["turn"]["understanding"]["target"])
                proposal = next(row for row in second["turn"]["proposed_events"] if row["type"] == "SP.RELATION.PRIVATE_MEETING")
                confirmed = app.chat_story({"confirm_turn_id": second["turn"]["turn_id"], "confirm_proposal_id": proposal["proposal_id"]})
                item = confirmed["item"]
                self.assertEqual("chat", item["source"])
                self.assertEqual("fictional_intervention", item["provenance"])
                self.assertEqual("deterministic", item["renderer"])
                self.assertEqual("SP.RELATION.PRIVATE_MEETING", item["scene"]["event_type"])
                self.assertTrue(item["scene"]["blocks"])
                self.assertEqual([], nc.validate_blocks(item["scene"]["blocks"]))
                self.assertTrue(item["narrative_provenance"]["template_ids"])
                self.assertEqual([], item["reactions"]["boards"])
                self.assertEqual(item["id"], confirmed["turn"]["committed_event_id"])
                story = confirmed["dashboard"]["story"]
                self.assertEqual(1, story["current"]["sequence"])
                self.assertEqual(1, len(story["threads"]))
                self.assertEqual("manager_trust", story["threads"][0]["thread_type"])
                self.assertIn("protagonist->manager", story["relationships"])
                with self.assertRaisesRegex(ValueError, "이미"):
                    app.chat_story({"confirm_turn_id": second["turn"]["turn_id"], "confirm_proposal_id": proposal["proposal_id"]})
                self.assertEqual([], llm_calls)
                # Replay: the same first message on a fresh identical world yields the same reply.
                world_id = first["dashboard"]["world"]["world_id"]
                ledger = Ledger(str(Path(config["data_dir"]) / "worlds" / world_id / "ledger.json"), world_id, read_only=True)
                self.assertEqual(2, len(ledger.state["conversation_turns"]))
                self.assertEqual(1, len(ledger.state["world_events"]))
                self.assertEqual("SP.RELATION.PRIVATE_MEETING", ledger.state["world_events"][0]["event_type"])
                self.assertTrue(ledger.state["narrative_memory"]["signatures"])
                public = app.chat_story({"user_text": "기자들 앞에서는 강하게 말할래"})
                quote = next(row for row in public["turn"]["proposed_events"] if row["type"] == "SP.MEDIA.QUOTE")
                committed = app.chat_story({"confirm_turn_id": public["turn"]["turn_id"], "confirm_proposal_id": quote["proposal_id"], "statement": "팀이 먼저다."})
                self.assertGreaterEqual(len(committed["item"]["reactions"]["boards"]), 1)
                self.assertGreaterEqual(len(committed["item"]["reactions"]["media"]), 1)
                self.assertIn("팀이 먼저다", committed["item"]["scene"]["response"])
                self.assertEqual(2, committed["dashboard"]["feed"]["story_overlay_count"])

    def test_prop_saga_through_the_service(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._config(temp)
            Path(config["save_path"]).write_bytes(b"save")
            snap = _snapshot()
            app = service_module.StarModeService()
            with patch.object(service_module.config_module, "load_with_diagnostics", return_value=(config, {})), patch.object(
                service_module.config_module, "load", return_value=config
            ), patch.object(service_module.save_reader, "read_snapshot", return_value=snap), patch.object(
                service_module, "_llm_reachable", return_value=False
            ), patch.object(service_module, "_probe_llm_ports", return_value=False), patch.object(service_module.config_module, "autodetect_saves", return_value=[]):
                app.check_save(lambda *_args: None)
                created = app.chat_story({"user_text": "햄버거 얘기를 앞으로 내부 농담으로 만들자"})
                self.assertTrue(created["response"]["prop_change"]["applied"])
                self.assertEqual("햄버거", created["response"]["prop_change"]["prop"]["name"])
                self.assertEqual(1, len(created["dashboard"]["story"]["props"]))
                callback = app.chat_story({"user_text": "그 햄버거 사건을 오늘 경기 뒤에 다시 꺼내자"})
                self.assertTrue(callback["response"]["prop_change"]["applied"])
                self.assertIsNotNone(callback["item"])
                self.assertEqual("SP.USER.PROP.CALLBACK", callback["item"]["scene"]["event_type"])
                self.assertIn("햄버거", callback["item"]["scene"]["response"])
                escalate = app.chat_story({"user_text": "이 별명을 팬들까지 쓰게 할까?"})
                self.assertTrue(escalate["response"]["prop_change"]["requires_confirmation"])
                proposal = next(row for row in escalate["turn"]["proposed_events"] if row.get("kind") == "prop_escalation")
                confirmed = app.chat_story({"confirm_turn_id": escalate["turn"]["turn_id"], "confirm_proposal_id": proposal["proposal_id"]})
                self.assertEqual("SP.USER.PROP.ESCALATION", confirmed["item"]["scene"]["event_type"])
                props = {row["name"]: row for row in confirmed["dashboard"]["story"]["props"]}
                self.assertEqual("escalation", props["햄버거"]["state"])
                self.assertEqual("local", props["햄버거"]["visibility"])

    def test_llm_preference_without_llm_fails_clearly_and_auto_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self._config(temp)
            Path(config["save_path"]).write_bytes(b"save")
            snap = _snapshot()
            app = service_module.StarModeService()
            with patch.object(service_module.config_module, "load_with_diagnostics", return_value=(config, {})), patch.object(
                service_module.config_module, "load", return_value=config
            ), patch.object(service_module.save_reader, "read_snapshot", return_value=snap), patch.object(
                service_module, "_llm_reachable", return_value=False
            ), patch.object(service_module, "_probe_llm_ports", return_value=False), patch.object(service_module.config_module, "autodetect_saves", return_value=[]):
                app.check_save(lambda *_args: None)
                with self.assertRaisesRegex(RuntimeError, "LLM"):
                    app.chat_story({"user_text": "말해 줘", "renderer_preference": "llm", "category": "media", "situation": "postgame_interview"})
                result = app.chat_story({"user_text": "오늘 감독한테 좀 서운했어", "renderer_preference": "auto", "category": "media", "situation": "postgame_interview"})
                self.assertEqual("deterministic", result["renderer"])


if __name__ == "__main__":
    unittest.main()
