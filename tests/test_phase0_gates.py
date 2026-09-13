#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 gates: forbidden public language, skeleton clones, source policy.

The legacy generators are frozen negative design cases. These tests prove the
gate *locates* their artificial phrases; they do not change legacy output.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import community
import corpus_policy
import realism_gate
import spotlight_engine
import stat_engine
import story_engine

ROOT = Path(__file__).resolve().parent.parent
NAMES = ["Paul Skenes", "Skenes"]


def _snapshot(stats, content_hash="gate"):
    return {
        "player": {"id": 99, "name": "Paul Skenes", "team": "Yokohama DeNA", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
        "stats": stats,
        "content_hash": content_hash,
        "profile_fingerprint": "profile",
        "source": {"file": "StarPlayer.dat", "slot": "00"},
    }


STAR = {
    "pit_IP": 153, "pit_TBF": 465, "pit_H": 4, "pit_K": 388, "pit_W": 17,
    "bat_HR": 105, "bat_RBI": 223, "bat_R": 174, "bat_SO": 11, "bat_SB": 129,
    "bat_AB": 277, "bat_H": 201, "bat_AVG": 0.726,
}


def _global_spotlight(snap, event):
    career = {
        "records": {"season": [{"label": "홈런", "status": "broken"}] * 4},
        "honor_summary": [],
        "timeline": [],
        "player_grade": {"code": "S+", "label": "슈퍼스타", "score": 86},
    }
    return spotlight_engine.evaluate(snap, career, event, {"mode": "standard", "heat": 9})


class ForbiddenLanguageTests(unittest.TestCase):
    def test_game_language_is_flagged_in_immersive_layer(self):
        text = "이 새끼 핵 쓰는거 아니냐고. 라스트보스 그 자체. 리그 밸런스 붕괴수준."
        audit = realism_gate.audit_public_prose(text)
        self.assertIn("game_system_language", audit.codes)
        self.assertGreaterEqual(len(audit.violations), 3)

    def test_grade_and_score_language_is_layer_dependent(self):
        prose = "모든 기자가 그를 EX 선수라고 불렀다. 세계 주목도 100의 하루였다."
        self.assertIn("grade_badge_in_prose", realism_gate.audit_public_prose(prose).codes)
        self.assertIn("numeric_score_in_prose", realism_gate.audit_public_prose(prose).codes)
        badge = "[EX] 역사적인 시즌 흐름 · 근거: 리그 1위 항목 4개"
        self.assertTrue(realism_gate.audit_public_prose(badge, layer="analysis").ok)
        self.assertTrue(realism_gate.audit_public_prose(prose, layer="diagnostic").ok)

    def test_natural_replacements_pass(self):
        for line in (
            "개막 두 달 만에 리그 전체의 취재 동선을 바꿔 놓았다.",
            "원정 구단도 선발 등판일에 맞춰 전력분석 인력을 늘리고 있다.",
            "월간 MVP 경쟁을 넘어 시즌 기록표의 어느 줄까지 바꿀지가 화제가 됐다.",
            "지역 팬의 기대가 전국 단위의 논쟁으로 번졌다.",
        ):
            self.assertTrue(realism_gate.audit_public_prose(line).ok, line)

    def test_unsupported_record_claim_requires_a_record_fact(self):
        claim = "NPB 최초의 기록이 나왔다. 그는 역사를 다시 썼다."
        self.assertIn("unsupported_record_claim", realism_gate.audit_public_prose(claim).codes)
        supported = realism_gate.audit_public_prose(
            claim, facts=[{"kind": "npb_reference_exceeded", "id": "fact-1"}]
        )
        self.assertNotIn("unsupported_record_claim", supported.codes)

    def test_quote_attribution_must_be_protagonist_or_fictional_persona(self):
        text = "야마다 감독은 \"오늘은 그의 날이었다\"라고 말했다."
        audit = realism_gate.audit_public_prose(text, personas=["요코하마 현장 취재진"])
        self.assertIn("unregistered_quote_attribution", audit.codes)
        allowed = realism_gate.audit_public_prose(text, personas=["야마다 감독"])
        self.assertNotIn("unregistered_quote_attribution", allowed.codes)
        own = realism_gate.audit_public_prose(
            "Skenes는 \"팀이 먼저다\"라고 말했다.", protagonist_names=NAMES
        )
        self.assertNotIn("unregistered_quote_attribution", own.codes)


class LegacyNegativeCaseTests(unittest.TestCase):
    """The gate must locate the artificial phrases of the frozen generators."""

    def test_legacy_community_feed_leaks_game_language(self):
        snap = _snapshot(STAR)
        delta = {"pit_IP": 9, "pit_TBF": 27, "pit_K": 27, "pit_H": 0}
        event = {
            "kind": "NEW_GAME",
            "snapshot": snap,
            "delta": delta,
            "milestones": [],
            "assess": stat_engine.assess(STAR),
            "role": "pitching",
            "baseline_only": False,
        }
        spot = _global_spotlight(snap, event)
        mem = {"nicknames": [], "memes": [], "open_loops": [], "recent_phrases": []}
        # Freeze the original builders, not the now-repaired public entry point.
        # Their signatures and outputs remain as negative evidence; the active
        # path is required to pass the same gate by test_editorial.
        rng = random.Random(42)
        feed = {"media": community.gen_media(event, rng, spotlight=spot),
                "boards": [community.gen_dc(event, 9, rng, mem)]}
        report = realism_gate.audit_feed(feed, names=NAMES)
        codes = {row["code"] for row in report["prose_violations"]}
        self.assertIn("game_system_language", codes)
        self.assertIn("internal_signal_language", codes)
        self.assertFalse(realism_gate.feed_is_clean(report))

    def test_legacy_story_articles_share_headline_skeleton_and_body(self):
        snap = _snapshot(STAR)
        event = {
            "kind": "NO_CHANGE", "snapshot": snap, "delta": {}, "milestones": [],
            "assess": stat_engine.assess(STAR), "role": "no_appearance", "baseline_only": False,
        }
        spot = _global_spotlight(snap, event)
        selection = story_engine.normalize(
            {"category": "media", "situation": "postgame_interview", "target": "reporter", "visibility": "public"}
        )
        title = "미디어 · 경기 뒤 인터뷰에 선다"
        reactions = {"media": story_engine._media_reaction(selection, "Paul Skenes", title, "기자단은 한 문장을 서로 다른 제목으로 옮겼다.", spot),
                     "boards": story_engine._board_reactions(random.Random(42), title, "public", spot)}
        self.assertGreaterEqual(len(reactions["media"]), 3)
        report = realism_gate.audit_feed(reactions, names=NAMES)
        # `{player}, {situation}…{angle}에 남은 질문` differs by one noun only.
        self.assertTrue(report["headline_clones"], report)
        # Every outlet publishes the same body with a shared disclaimer.
        self.assertTrue(report["body_clones"], report)
        # ` · {anchor}에서 이어진 반응` is appended to every non-first board.
        self.assertTrue(report["mechanical_suffixes"], report)
        self.assertIn("shell_notice_in_prose", {row["code"] for row in report["prose_violations"]})

    def test_skeleton_detector_ignores_genuinely_different_headlines(self):
        titles = [
            "Skenes, 9회 마지막 타자를 삼진으로 돌려세우다",
            "요코하마의 밤을 바꾼 한 구: 포수가 본 결정구",
            "기록실 노트 — 이번 시즌 388탈삼진의 자리",
        ]
        self.assertEqual([], realism_gate.find_skeleton_clones(titles, NAMES))
        clones = [
            "Skenes, 경기 뒤 인터뷰에 선다…현장에 남은 질문",
            "Skenes, 경기 뒤 인터뷰에 선다…기록에 남은 질문",
        ]
        self.assertEqual([(0, 1)], realism_gate.find_skeleton_clones(clones, NAMES))

    def test_mechanical_suffix_and_ending_reuse_detection(self):
        comments = [
            "이건 다음 경기에서 보여줘야 · 시즌 110홈런에서 이어진 반응",
            "오래 남을 장면 · 시즌 110홈런에서 이어진 반응",
            "다시 소환될 듯 · 시즌 110홈런에서 이어진 반응",
        ]
        self.assertTrue(realism_gate.mechanical_suffixes(comments))
        natural = ["포수 사인이 두 번 바뀐 뒤 결정구가 나왔다", "9회 2아웃에서 관중이 일어섰다", "내일 선발 예고가 궁금하다"]
        self.assertEqual({}, realism_gate.mechanical_suffixes(natural))


class SourcePolicyGateTests(unittest.TestCase):
    def _record(self, **overrides):
        template = json.loads((ROOT / "corpus" / "registry" / "source_record.template.json").read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc)
        record = dict(template)
        record.update(
            {
                "source_id": "test-source",
                "source_class": "A",
                "robots_checked_at": (now - timedelta(days=1)).isoformat(),
                "terms_checked_at": (now - timedelta(days=1)).isoformat(),
                "allowed_storage": "full_text",
                "allowed_transformation": True,
                "allowed_redistribution": True,
                "reviewer": "tester",
                "decision": "approved",
                "decision_reason": "public domain item with recorded evidence",
                "policy_review_expires_at": (now + timedelta(days=30)).isoformat(),
                "retention_days": 365,
            }
        )
        record.update(overrides)
        return record

    def test_template_records_are_complete_and_pending(self):
        template = json.loads((ROOT / "corpus" / "registry" / "source_record.template.json").read_text(encoding="utf-8"))
        problems = corpus_policy.validate_source_record(template)
        self.assertEqual([], [row for row in problems if row.startswith("missing field")])
        self.assertEqual("pending", template["decision"])
        self.assertFalse(corpus_policy.evaluate(template, "fetch_text").allowed)

    def test_approved_class_a_source_may_fetch_store_transform(self):
        record = self._record()
        for purpose in ("discover", "fetch_metadata", "fetch_text", "store", "transform", "redistribute"):
            self.assertTrue(corpus_policy.evaluate(record, purpose).allowed, purpose)

    def test_pending_denied_expired_and_class_e_are_blocked_before_fetch(self):
        self.assertFalse(corpus_policy.evaluate(self._record(decision="pending"), "fetch_text").allowed)
        self.assertFalse(corpus_policy.evaluate(self._record(decision="denied"), "fetch_text").allowed)
        expired = self._record(policy_review_expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.assertFalse(corpus_policy.evaluate(expired, "fetch_text").allowed)
        self.assertFalse(corpus_policy.evaluate(self._record(source_class="E"), "discover").allowed)

    def test_class_d_supplies_metadata_only_and_media_is_rejected(self):
        record = self._record(source_class="D", allowed_storage="metadata_only", allowed_transformation=False, allowed_redistribution=False)
        self.assertTrue(corpus_policy.evaluate(record, "fetch_metadata").allowed)
        self.assertFalse(corpus_policy.evaluate(record, "fetch_text").allowed)
        self.assertFalse(corpus_policy.evaluate(record, "transform").allowed)
        with self.assertRaises(corpus_policy.SourcePolicyError):
            corpus_policy.reject_media_payload("image/png")
        corpus_policy.reject_media_payload("text/html; charset=utf-8")

    def test_registry_gate_blocks_unregistered_and_tombstoned_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / "registry"
            (registry / "sources").mkdir(parents=True)
            tombstones = Path(temp) / "tombstones"
            tombstones.mkdir()
            gate = corpus_policy.PolicyGate(registry, tombstones)
            with self.assertRaises(corpus_policy.SourcePolicyError):
                gate.assert_allowed("unknown", "fetch_text")
            record = self._record()
            (registry / "sources" / "test-source.json").write_text(json.dumps(record), encoding="utf-8")
            self.assertTrue(gate.assert_allowed("test-source", "fetch_text").allowed)
            stone = corpus_policy.tombstone(record, "owner requested deletion", affected_pack_ids=["core-ko-1.0.0"])
            (tombstones / "test-source.json").write_text(json.dumps(stone), encoding="utf-8")
            with self.assertRaises(corpus_policy.SourcePolicyError):
                gate.assert_allowed("test-source", "fetch_text")
            self.assertTrue(stone["rebuild_required"])
            self.assertEqual(3, len(gate.log))


if __name__ == "__main__":
    unittest.main()
