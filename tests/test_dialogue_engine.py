#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic no-LLM understanding: acts, entities, visibility, fallbacks."""

from __future__ import annotations

import statistics
import time
import unittest

import dialogue_engine as de


class LanguageAndTokenTests(unittest.TestCase):
    def test_language_identification_by_script_and_stopwords(self):
        self.assertEqual("ko", de.detect_language("오늘 감독한테 좀 서운했어"))
        self.assertEqual("ja", de.detect_language("あの九回の三振で、球場の空気が完全に変わった。"))
        self.assertEqual("en", de.detect_language("The manager pulled him after the seventh inning today."))
        self.assertEqual("es", de.detect_language("El lanzador quiere hablar con el equipo hoy."))
        self.assertEqual("zh-Hant", de.detect_language("這個投手今天的表現讓球迷非常興奮。"))
        self.assertEqual("unknown", de.detect_language("   "))

    def test_normalization_and_particle_stripping(self):
        self.assertEqual("감독한테 서운했어", de.normalize("  감독한테​   서운했어  "))
        self.assertIn("감독", de.stems("감독한테 서운했어"))
        self.assertIn("후배", de.stems("후배를 위로해"))
        self.assertEqual("감독", de.strip_particle("감독에게"))
        self.assertEqual("나", de.strip_particle("나는"))


class TenActTests(unittest.TestCase):
    CASES = [
        ("내 시즌 탈삼진 몇 개야?", "ask", None, None),
        ("오늘 감독한테 좀 서운했어", "vent", "manager", "private"),
        ("후배를 위로해 주고 싶다", "console", "rookie", "clubhouse"),
        ("포수를 칭찬하고 싶어. 오늘 리드가 훌륭했다고 말할래", "praise", "teammate", "clubhouse"),
        ("오늘 패배는 내 잘못이라고 인정할래", "criticize_self", "reporter", "national"),
        ("다음 등판에서 기록을 노린다고 선언할까?", "declare", "reporter", "national"),
        ("동료에게 어제 일은 미안하다고 사과하고 싶어", "apologize", "teammate", "clubhouse"),
        ("감독과 따로 면담을 잡아서 직접 묻고 싶어", "request_private_meeting", "manager", "private"),
        ("기자들 앞에서는 강하게 말할래", "give_public_quote", "reporter", "national"),
    ]

    def test_each_first_slice_act_is_recognised_with_target_and_visibility(self):
        for text, act, target, visibility in self.CASES:
            result = de.understand(text)
            self.assertEqual(act, result["primary_act"], text)
            if target is not None:
                self.assertEqual(target, result["target"], text)
            if visibility is not None:
                self.assertEqual(visibility, result["visibility_hint"], text)
            self.assertLessEqual(result["fallback_level"], 2, text)

    def test_ask_carries_fact_requirements(self):
        result = de.understand("내 시즌 탈삼진 몇 개야?")
        self.assertTrue(result["question"])
        self.assertIn("stats.pit_K", result["fact_requirements"])
        result = de.understand("지난 시즌 수상 기록 기억나?")
        self.assertIn("career.honors", result["fact_requirements"])
        self.assertIn("career.seasons", result["fact_requirements"])
        result = de.understand("우리 팀 지금 몇 위야?")
        self.assertIn("context.standings", result["fact_requirements"])

    def test_proposal_question_is_detected(self):
        self.assertTrue(de.understand("다음 등판에서 기록을 노린다고 선언할까?")["proposal_question"])
        self.assertFalse(de.understand("기자들 앞에서는 강하게 말할래")["proposal_question"])


class DualActAndContextTests(unittest.TestCase):
    def test_private_vent_plus_public_defend_splits_into_two_targets(self):
        result = de.understand("오늘 감독에게 섭섭하지만 기자들 앞에서는 팀을 감싸고 싶어")
        acts = {row["act"] for row in result["acts"]}
        self.assertIn("vent", acts)
        self.assertIn("give_public_quote", acts)
        self.assertEqual(2, len(result["dual_acts"]), result["dual_acts"])
        by_act = {row["act"]: row for row in result["dual_acts"]}
        self.assertEqual("manager", by_act["vent"]["target"])
        self.assertEqual("private", by_act["vent"]["visibility"])
        self.assertEqual("reporter", by_act["give_public_quote"]["target"])
        self.assertEqual("national", by_act["give_public_quote"]["visibility"])

    def test_ellipsis_resolves_target_from_conversation_state(self):
        first = de.understand("오늘 감독한테 좀 서운했어")
        second = de.understand("그래도 직접 만나서 이야기하고 싶어", {"last_target": first["target"], "last_act": "vent"})
        self.assertEqual("manager", second["target"])
        self.assertTrue(second["resolved_from_context"])
        self.assertEqual("request_private_meeting", second["primary_act"])

    def test_risk_and_urgency_and_emotion(self):
        result = de.understand("지금 당장 트레이너한테 어깨가 아프다고 말해야 할까. 걱정돼.")
        self.assertTrue(result["urgency"])
        self.assertIn("injury", result["risk_tags"])
        self.assertIn("anxious", result["emotion"]["labels"])
        self.assertLess(result["emotion"]["valence"], 0)
        self.assertEqual("medical", result["target"])


class FallbackLadderTests(unittest.TestCase):
    def test_low_confidence_yields_clarification_and_empty_yields_help(self):
        vague = de.understand("그거 말이야")
        self.assertGreaterEqual(vague["fallback_level"], 4)
        self.assertTrue(de.clarification_question(vague))
        empty = de.understand("")
        self.assertEqual(6, empty["fallback_level"])
        self.assertGreaterEqual(len(de.help_suggestions()), 5)

    def test_emotion_with_active_persona_reaches_level_three(self):
        result = de.understand("너무 지쳤다", {"active_persona": "family"})
        self.assertLessEqual(result["fallback_level"], 3)

    def test_clarification_for_ambiguous_private_versus_public(self):
        result = de.understand("서운한 말을 기자들 앞에서 해도 될까")
        text = de.clarification_question(result)
        self.assertIn("비공개 면담", text)
        self.assertIn("공개 발언", text)


class PropCommandTests(unittest.TestCase):
    def test_prop_control_commands_map_to_deterministic_acts(self):
        cases = [
            ("이걸 앞으로 내부 농담으로 만들자", "prop_create"),
            ("햄버거 얘기는 팀 안에서만 돌게 해", "prop_set_visibility"),
            ("코치와 식단 문제로 약간 긴장하게 만들어", "prop_attach_relationship"),
            ("다음 홈런 때 이 농담을 다시 꺼내자", "prop_schedule_callback"),
            ("이번에는 웃기지 말고 진지한 화해 장면으로 이어가자", "prop_change_role"),
            ("이 별명을 팬들까지 쓰게 할까?", "prop_escalate"),
            ("이 소재는 이제 그만하고 좋은 기억으로만 남겨", "prop_retire"),
        ]
        for text, act in cases:
            result = de.understand(text)
            self.assertIn(act, result["prop_acts"], text)


class LatencyTests(unittest.TestCase):
    def test_understanding_meets_section_8_8_budget(self):
        samples = []
        text = "오늘 감독에게 섭섭하지만 기자들 앞에서는 팀을 감싸고 싶어. 후배도 위로해 주고 싶다."
        for _ in range(200):
            start = time.perf_counter()
            de.understand(text, {"last_target": "manager"})
            samples.append((time.perf_counter() - start) * 1000.0)
        ordered = sorted(samples)
        p95 = ordered[int(0.95 * (len(ordered) - 1))]
        self.assertLess(p95, 40.0, f"p95 {p95:.2f} ms, median {statistics.median(samples):.2f} ms")


if __name__ == "__main__":
    unittest.main()
