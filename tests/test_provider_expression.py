#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contracts for provider expression-only rendering of deterministic plans."""

from __future__ import annotations

import copy
import unittest

import narrative_contracts as nc
import provider_expression


class ProviderExpressionTests(unittest.TestCase):
    def response(self) -> dict:
        scene = [
            nc.block("paragraph", "두 사람은 조용한 가게에서 햄버거 이야기를 꺼냈다."),
            nc.block(
                "quote",
                "다음에도 여기서 만나자.",
                speaker="가상 주인공",
                origin="user_explicit",
            ),
            nc.block(
                "fact_callout",
                "확정 전에는 약속이 생기지 않습니다.",
                label="확정 경계",
                evidence_class="fictional_intervention",
                fact_ids=["fixture-boundary"],
            ),
        ]
        proposal = {
            "proposal_id": "proposal-one",
            "type": "SP.USER.INTERACTION.JOKE",
            "beat": "joke",
            "visibility": "private",
            "line": "햄버거 농담을 건넨다",
        }
        return {
            "renderer": "deterministic",
            "reply": {"text": nc.project_blocks(scene), "blocks": scene},
            "proposed_events": [proposal],
            "understanding": {"primary_act": "interaction_joke", "prop_acts": []},
            "provenance": {"renderer": "deterministic"},
        }

    def test_inline_numbered_provider_text_becomes_separate_typed_paragraphs(self):
        blocks = provider_expression.generated_blocks(
            "**1. 가게 앞.** 먼저 문을 열었다. **2. 테이블.** 잠시 웃으며 자리에 앉았다."
        )
        projected = nc.project_blocks(blocks)
        self.assertIn("## 1. 가게 앞.", projected)
        self.assertIn("\n\n", projected)
        self.assertGreaterEqual(len(blocks), 4)
        self.assertEqual([], nc.validate_blocks(blocks))

    def test_remote_prose_cannot_change_plan_or_locked_blocks(self):
        original = self.response()
        original_proposal = copy.deepcopy(original["proposed_events"][0])
        result = provider_expression.apply_to_response(
            original,
            "가게 안에 고소한 향이 번졌다.\n\n둘은 짧은 농담을 주고받으며 긴장을 풀었다.",
            model="gemini-test",
            provider_audit={"request_hash": "bounded"},
        )

        proposal = result["proposed_events"][0]
        self.assertEqual(original_proposal, {key: value for key, value in proposal.items() if key != "provider_expression"})
        self.assertEqual("gemini_expression", result["renderer"])
        self.assertIn("다음에도 여기서 만나자.", result["reply"]["text"])
        self.assertIn("확정 전에는 약속이 생기지 않습니다.", result["reply"]["text"])

        realized = {
            "renderer": "deterministic",
            "blocks": copy.deepcopy(original["reply"]["blocks"]),
            "text": original["reply"]["text"],
            "provenance": {},
        }
        committed = provider_expression.apply_to_realized(realized, proposal)
        self.assertEqual("gemini_expression", committed["renderer"])
        self.assertIn("고소한 향", committed["text"])
        self.assertIn("확정 전에는 약속이 생기지 않습니다.", committed["text"])

    def test_changed_proposal_is_rejected_before_expression_commit(self):
        response = provider_expression.apply_to_response(
            self.response(),
            "두 사람은 잠시 웃었다.\n\n대화는 조용히 이어졌다.",
            model="gemini-test",
            provider_audit={},
        )
        proposal = copy.deepcopy(response["proposed_events"][0])
        proposal["visibility"] = "public"
        realized = {
            "renderer": "deterministic",
            "blocks": copy.deepcopy(self.response()["reply"]["blocks"]),
            "text": "",
            "provenance": {},
        }
        with self.assertRaisesRegex(ValueError, "구조가 달라졌습니다"):
            provider_expression.apply_to_realized(realized, proposal)

    def test_local_fallback_expression_keeps_the_same_frozen_plan(self):
        original = self.response()
        result = provider_expression.apply_to_response(
            original,
            "가게 문이 닫히자 두 사람의 어깨에서 힘이 빠졌다.\n\n농담은 짧았지만 분위기는 한결 편해졌다.",
            model="local-test",
            provider="local_llm",
            provider_audit={"provider": "local_llm", "network_used": False},
        )

        proposal = result["proposed_events"][0]
        self.assertEqual("local_llm_expression", result["renderer"])
        self.assertEqual("local_llm", proposal["provider_expression"]["provider"])
        self.assertEqual("local_llm_expression", result["provenance"]["renderer"])

        realized = {
            "renderer": "deterministic",
            "blocks": copy.deepcopy(original["reply"]["blocks"]),
            "text": original["reply"]["text"],
            "provenance": {},
        }
        committed = provider_expression.apply_to_realized(realized, proposal)
        self.assertEqual("local_llm_expression", committed["renderer"])
        self.assertIn("어깨에서 힘", committed["text"])

    def test_legacy_gemini_expression_version_remains_readable(self):
        result = provider_expression.apply_to_response(
            self.response(),
            "두 사람은 잠시 웃었다.\n\n대화는 조용히 이어졌다.",
            model="gemini-test",
            provider_audit={},
        )
        proposal = result["proposed_events"][0]
        proposal["provider_expression"]["version"] = "1.0.0"
        realized = {
            "renderer": "deterministic",
            "blocks": copy.deepcopy(self.response()["reply"]["blocks"]),
            "text": "",
            "provenance": {},
        }
        committed = provider_expression.apply_to_realized(realized, proposal)
        self.assertEqual("gemini_expression", committed["renderer"])


if __name__ == "__main__":
    unittest.main()
