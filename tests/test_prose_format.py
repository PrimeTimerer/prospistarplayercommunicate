#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

import presentation
from prose_format import normalize_generated_markdown


class GeneratedProseFormatTests(unittest.TestCase):
    def test_inline_numbered_bold_scenes_become_real_sections_and_paragraphs(self):
        source = (
            "**1. 오후의 골목.**\u00a0선수는 조용히 가게로 향했다. "
            "**2. 가게 안의 공기.** 사람들의 일상적인 대화가 이어졌다. "
            "**12. 다음 복선.** 다음 경기를 위한 준비가 시작됐다."
        )

        result = normalize_generated_markdown(source)

        self.assertEqual(3, result.count("## "))
        self.assertIn("## 1. 오후의 골목.\n\n선수는 조용히 가게로 향했다.", result)
        self.assertIn("\n\n## 2. 가게 안의 공기.\n\n사람들의 일상적인 대화가 이어졌다.", result)
        self.assertTrue(result.endswith("다음 경기를 위한 준비가 시작됐다."))

    def test_verified_statistics_and_ordinary_bold_text_are_not_rewritten(self):
        source = "117홈런과 406탈삼진은 검증 기록이다. **다음 경기**도 준비한다."
        self.assertEqual(source, normalize_generated_markdown(source))

    def test_collapsed_atx_headings_and_bodies_are_recovered(self):
        source = (
            "## 영상실의 고요한 침묵 요코하마의 클럽하우스 안쪽 영상실에서 푸른 빛이 흘렀다. "
            "집중은 오래 이어졌다. ## 코칭스태프의 시선 문가에 기대어 화면을 보던 코치들은 말을 아꼈다. "
            "미세한 오차를 살폈다. ## 해외 팬덤의 번역된 화면 태평양 건너 팬들도 장면을 번역했다. "
            "반응은 빠르게 퍼졌다."
        )

        result = normalize_generated_markdown(source)

        self.assertEqual(3, result.count("## "))
        self.assertIn("## 영상실의 고요한 침묵\n\n요코하마의 클럽하우스", result)
        self.assertIn("\n\n## 코칭스태프의 시선\n\n문가에 기대어", result)
        self.assertIn("\n\n## 해외 팬덤의 번역된 화면\n\n태평양 건너", result)

    def test_well_formed_atx_sections_are_stable(self):
        source = "## 영상실의 침묵\n\n첫 문단이다.\n\n둘째 문단이다."
        self.assertEqual(source, normalize_generated_markdown(source))

    def test_mechanical_spacing_keeps_words_and_numeric_typography(self):
        source = "선수는\u202f\u202f돌아왔다.다음\u200b날도 준비했다.  타율 0.674와 1,000탈삼진이다."
        expected = "선수는 돌아왔다. 다음날도 준비했다. 타율 0.674와 1,000탈삼진이다."
        self.assertEqual(expected, normalize_generated_markdown(source))
        self.assertEqual(expected, normalize_generated_markdown(expected))

    def test_literal_links_and_code_do_not_receive_spelling_changes(self):
        source = "https://example.invalid/문장.다음 `값.다음  문장` 일반 문장이다.다음이다."
        self.assertEqual(source.replace("문장이다.다음", "문장이다. 다음"), normalize_generated_markdown(source))

    def test_long_quoted_paragraph_keeps_quotes_and_sentence_order(self):
        sentence = '"동료들은 경기가 끝난 뒤에도 서로의 준비를 지켜보며 조용히 다음 날의 계획을 이야기했다."'
        source = " ".join([sentence] * 6)
        result = normalize_generated_markdown(source)
        self.assertGreaterEqual(result.count("\n\n"), 2)
        self.assertEqual(source.split(), result.split())

    def test_no_space_sentences_reflow_without_guessing_internal_words(self):
        sentence = "라커룸에서는누군가가남긴농담이경기후에도계속이어졌고선수들은각자조용히웃으며다음날을준비했다."
        result = normalize_generated_markdown(sentence * 6)
        self.assertGreaterEqual(result.count("\n\n"), 2)
        self.assertEqual(sentence * 6, "".join(result.split()))

    def test_exceptionally_long_unbroken_prose_is_split_only_at_sentences(self):
        sentence = "영상실에서는 다음 경기를 준비하며 이미 확인된 장면을 다시 살폈다."
        source = " ".join([sentence] * 6)
        result = normalize_generated_markdown(source)
        self.assertGreaterEqual(result.count("\n\n"), 2)
        self.assertEqual(source.replace("  ", " ").split(), result.replace("\n", " ").split())

    def test_standalone_export_uses_the_same_compatibility_normalizer(self):
        html = presentation.render_narrative_html(
            "**1. 첫 장면.** 본문 하나. **2. 두 번째 장면.** 본문 둘.",
            "gemini:test-model",
        )
        self.assertEqual(2, html.count("<h2>"))
        self.assertIn("<p>본문 하나.</p>", html)
        self.assertIn("Google Gemini API 생성", html)
        self.assertIn("text-indent:1em", html)

    def test_built_in_fallback_projects_feed_with_real_paragraph_boundaries(self):
        feed = {
            "player": {"name": "가상 주인공", "team": "가상 구단"},
            "event": {"date": {"year": 2027, "month": 7, "day": 24}, "game_lines": ["9이닝 20탈삼진"]},
            "media": [
                {
                    "outlet": "가상 야구일보",
                    "title": "기록을 다시 보다",
                    "sub": "다음 등판을 향한 시선",
                    "body": ["첫 문단이다.", "둘째 문단이다."],
                }
            ],
            "social": [],
            "boards": [],
        }
        markdown = presentation.render_built_in_narrative(feed)
        self.assertIn("## 언론 기사", markdown)
        self.assertIn("### 가상 야구일보 · 기록을 다시 보다", markdown)
        self.assertIn("첫 문단이다.\n\n둘째 문단이다.", markdown)

        html = presentation.render_narrative_html(markdown, "builtin:deterministic-feed")
        self.assertIn("완전 오프라인 내장 서사 엔진", html)
        self.assertNotIn("오프라인 로컬 LLM 생성", html)


if __name__ == "__main__":
    unittest.main()
