#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger v5 contracts, SP.* ontology coverage, and v4 -> v5 migration."""

from __future__ import annotations

import glob
import json
import tempfile
import unittest
from pathlib import Path

import event_ontology
import narrative_contracts as nc
import story_engine
from ledger_v2 import Ledger


class EvidenceAndEventTests(unittest.TestCase):
    def test_legacy_provenance_resolves_to_v5_evidence_classes(self):
        self.assertEqual("save_verified", nc.evidence_class_of("save_verified_historical_season"))
        self.assertEqual("user_confirmed", nc.evidence_class_of("manual_confirmed"))
        self.assertEqual("derived_analysis", nc.evidence_class_of("derived_save_delta"))
        self.assertEqual("generated_fiction", nc.evidence_class_of("llm_generated_fiction"))
        self.assertEqual("fictional_intervention", nc.evidence_class_of("fictional_intervention"))
        self.assertEqual("unknown", nc.evidence_class_of("visual_hint"))
        self.assertEqual("unknown", nc.evidence_class_of("something-new"))

    def test_event_envelope_validates_and_rejects_unrelated(self):
        event = nc.new_event(
            event_type="SP.MEDIA.QUOTE",
            game_date="2027-07-24",
            universe_id="u1",
            protagonist_id=99,
            actor="protagonist",
            target="reporter",
            visibility="public",
            evidence_class="fictional_intervention",
            salience=1.7,
            emotional_valence=-3,
            risk_tags=["conflict"],
        )
        self.assertEqual([], nc.validate_event(event))
        self.assertEqual("national", event["visibility"])
        self.assertEqual(1.0, event["salience"])
        self.assertEqual(-1.0, event["emotional_valence"])
        with self.assertRaises(ValueError):
            nc.new_event(
                event_type="SP.MEDIA.QUOTE", game_date="2027-07-24", universe_id="u1", protagonist_id=99,
                actor="x", visibility="public", evidence_class="fictional_intervention",
                protagonist_relation="unrelated",
            )
        with self.assertRaises(ValueError):
            nc.new_event(
                event_type="NOT.AN.ID", game_date="2027-07-24", universe_id="u1", protagonist_id=99,
                actor="x", visibility="public", evidence_class="fictional_intervention",
            )
        with self.assertRaises(ValueError):
            nc.new_event(
                event_type="SP.MEDIA.QUOTE", game_date="2027-7-24", universe_id="u1", protagonist_id=99,
                actor="x", visibility="public", evidence_class="fictional_intervention",
            )

    def test_derived_facts_expose_their_formula(self):
        with self.assertRaises(ValueError):
            nc.new_fact(kind="k9", label="K/9", value=22.8, evidence_class="derived_analysis", game_date="2027-07-24")
        fact = nc.new_fact(
            kind="k9", label="K/9", value=22.8, evidence_class="derived_analysis",
            game_date="2027-07-24", formula="K*9/IP v1",
        )
        self.assertEqual(16, len(fact["fact_id"]))
        again = nc.new_fact(
            kind="k9", label="K/9", value=22.8, evidence_class="derived_analysis",
            game_date="2027-07-24", formula="K*9/IP v1",
        )
        self.assertEqual(fact["fact_id"], again["fact_id"])


class ThreadAndRelationshipTests(unittest.TestCase):
    def test_thread_cannot_jump_from_seeded_to_resolved(self):
        thread = nc.new_thread(
            thread_type="role_competition", universe_id="u1", protagonist_id=99,
            label="선발 자리 경쟁", game_date="2027-07-24",
        )
        with self.assertRaisesRegex(ValueError, "cannot move"):
            nc.transition_thread(thread, "resolved", game_date="2027-07-24", evidence_event_id="e1")
        with self.assertRaisesRegex(ValueError, "evidence"):
            nc.transition_thread(thread, "noticed", game_date="2027-07-24")
        row = nc.transition_thread(thread, "noticed", game_date="2027-07-25", evidence_event_id="e1")
        self.assertEqual(("seeded", "noticed"), (row["from_state"], row["to_state"]))
        nc.transition_thread(thread, "developing", game_date="2027-07-26", authorized_intervention=True)
        nc.transition_thread(thread, "pressure", game_date="2027-07-27", evidence_event_id="e2")
        nc.transition_thread(thread, "decision", game_date="2027-07-28", evidence_event_id="e3")
        nc.transition_thread(thread, "consequence", game_date="2027-07-29", evidence_event_id="e4")
        nc.transition_thread(thread, "resolved", game_date="2027-07-30", evidence_event_id="e5")
        self.assertEqual("resolved", thread["state"])
        self.assertEqual(7, len(thread["state_history"]))

    def test_relationship_edges_are_event_sourced_and_multidimensional(self):
        edge = nc.new_edge("protagonist", "manager")
        row = nc.apply_edge_delta(
            edge, {"respect": 0.4, "rivalry": 0.3, "trust": -0.2},
            game_date="2027-07-24", event_id="e1", explanation="역할 요청 뒤 존중은 남고 신뢰는 흔들렸다",
        )
        self.assertEqual(0.4, edge["respect"])
        self.assertEqual(0.3, edge["rivalry"])
        self.assertEqual(-0.2, edge["trust"])
        self.assertEqual({"respect": 0.0, "rivalry": 0.0, "trust": 0.0}, row["before"])
        with self.assertRaises(ValueError):
            nc.apply_edge_delta(edge, {"friendship": 1}, game_date="2027-07-24", event_id="e2", explanation="x")
        nc.apply_edge_delta(edge, {"trust": -5}, game_date="2027-07-25", event_id="e3", explanation="clamp")
        self.assertEqual(-1.0, edge["trust"])


class PropTests(unittest.TestCase):
    def test_prop_beat_machine(self):
        prop = nc.new_prop(
            universe_id="u1", protagonist_id=99, name="햄버거", prop_type="food",
            game_date="2027-07-24", participants=["teammate"], emotional_roles=["comic"],
        )
        self.assertEqual("seed", prop["state"])
        with self.assertRaises(ValueError):
            nc.transition_prop(prop, "payoff", game_date="2027-07-24")
        nc.transition_prop(prop, "establish", game_date="2027-07-24")
        nc.transition_prop(prop, "callback", game_date="2027-07-30", event_id="e1")
        nc.transition_prop(prop, "escalation", game_date="2027-08-02", event_id="e2")
        nc.transition_prop(prop, "payoff", game_date="2027-08-10", event_id="e3")
        self.assertEqual("delivered", prop["payoff_state"])
        nc.transition_prop(prop, "retired", game_date="2027-08-11")
        with self.assertRaises(ValueError):
            nc.transition_prop(prop, "callback", game_date="2027-08-12")
        self.assertEqual(3, len(prop["callbacks"]))


class BlockTests(unittest.TestCase):
    def test_projection_uses_existing_markdown_renderer_vocabulary(self):
        blocks = [
            nc.block("eyebrow", "팀 내부"),
            nc.block("headline", "동료를 따로 격려한다"),
            nc.block("dek", "조용한 면담이 다음 경기의 분위기를 바꿨다."),
            nc.block("section_heading", "침묵이 길어진 이유"),
            nc.block("paragraph", "첫 문단."),
            nc.block("quote", "말보다 시간을 냈다.", speaker="베테랑 동료"),
            nc.block("translation", "9회의 그 삼진 하나로 분위기가 바뀌었다.", label="한국어 번역"),
            nc.block("fact_callout", "시즌 388탈삼진", fact_ids=["f1"]),
        ]
        self.assertEqual([], nc.validate_blocks(blocks))
        text = nc.project_blocks(blocks)
        self.assertIn("## 동료를 따로 격려한다", text)
        self.assertIn("### 침묵이 길어진 이유", text)
        self.assertIn("> 말보다 시간을 냈다. — 베테랑 동료", text)
        self.assertIn("[한국어 번역] 9회의", text)
        self.assertIn("**검증 사실** 시즌 388탈삼진", text)

    def test_block_validation_rejects_inline_numbering_and_long_paragraphs(self):
        bad = [nc.block("paragraph", "1. 조용한 방 안의 공백이 길었다. 2. 상대팀 레전드의 침묵과 동요가 이어졌다.")]
        self.assertTrue(any("numbered" in row for row in nc.validate_blocks(bad)))
        long = [nc.block("paragraph", "가" * 700)]
        self.assertTrue(any("exceeds" in row for row in nc.validate_blocks(long)))
        self.assertTrue(nc.block_warnings([nc.block("paragraph", "나" * 500)]))
        self.assertTrue(any("speaker" in row for row in nc.validate_blocks([nc.block("quote", "x")])))

    def test_legacy_paragraphizer_splits_numbered_sections_but_never_statistics(self):
        raw = (
            "타율 .730과 K/9 22.80은 2027.07.24 기준이다. 6.1이닝을 던졌다. "
            "1. 조용한 방 안의 공백. 그는 오래 앉아 있었다. "
            "2. 상대팀 레전드의 침묵과 동요. 더그아웃은 조용했다. "
            "3. 다음 등판을 향한 약속. 아무도 먼저 말하지 않았다."
        )
        repaired = nc.paragraphize_legacy(raw)
        kinds = [row["type"] for row in repaired["blocks"]]
        self.assertEqual(3, kinds.count("section_heading"))
        self.assertEqual(raw, repaired["original_text"])
        headings = [row["text"] for row in repaired["blocks"] if row["type"] == "section_heading"]
        self.assertEqual("1. 조용한 방 안의 공백.", headings[0])
        lead = repaired["blocks"][0]
        self.assertEqual("paragraph", lead["type"])
        self.assertIn(".730", lead["text"])
        self.assertIn("22.80", lead["text"])
        self.assertIn("6.1이닝", lead["text"])
        # Words survive the split: joining the blocks reproduces every token.
        joined = " ".join(row["text"].replace("1. ", "").replace("2. ", "").replace("3. ", "") for row in repaired["blocks"])
        for token in ("조용한 방 안의 공백", "더그아웃은 조용했다", "아무도 먼저 말하지 않았다", "2027.07.24"):
            self.assertIn(token, joined)

    def test_paragraphizer_golden_corpus_has_no_false_splits(self):
        corpus = [
            "타율 .730에 22.80의 K/9, 2027.07.24 기준 6.1이닝 153.2이닝 누적.",
            "No. 1 선발은 7.2이닝 동안 12개의 삼진을 잡았다. 4.50에서 1.85로 내려갔다.",
            "3연승 뒤 2. 5할 승률이라는 표현은 쓰지 않는다.",
            "1회부터 9회까지 27명. 27개 아웃. 0개 안타.",
        ]
        for text in corpus:
            repaired = nc.paragraphize_legacy(text)
            self.assertEqual(["paragraph"], [row["type"] for row in repaired["blocks"]], text)
            self.assertEqual(text, repaired["blocks"][0]["text"])

    def test_blank_lines_are_paragraph_boundaries(self):
        repaired = nc.paragraphize_legacy("첫 문단.\n\n둘째 문단.\r\n\r\n셋째 문단.")
        self.assertEqual(3, len(repaired["blocks"]))


class OntologyTests(unittest.TestCase):
    def test_every_domain_from_the_plan_is_registered_with_families(self):
        expected = {
            "SP.IDENTITY", "SP.ROSTER", "SP.ROLE", "SP.GAME.PRE", "SP.GAME.PITCH", "SP.GAME.BAT",
            "SP.GAME.FIELD", "SP.GAME.RUN", "SP.GAME.POST", "SP.TRAINING", "SP.HEALTH", "SP.RELATION",
            "SP.PRIVATE", "SP.ROMANCE", "SP.MEDIA", "SP.PUBLIC", "SP.CONTRACT", "SP.CALENDAR",
            "SP.AWARD", "SP.MILESTONE", "SP.STANDINGS", "SP.AGING", "SP.LEGACY", "SP.USER",
        }
        self.assertEqual(expected, set(event_ontology.DOMAINS))
        for domain_id, definition in event_ontology.DOMAINS.items():
            self.assertTrue(definition["families"], domain_id)
            self.assertTrue(definition["owners"], domain_id)
        self.assertGreaterEqual(len(event_ontology.all_event_types()), 200)
        self.assertEqual("SP.GAME.PITCH", event_ontology.domain_of("SP.GAME.PITCH.PERFECT_GAME"))
        self.assertEqual("SP.GAME.POST", event_ontology.domain_of("SP.GAME.POST.MISSION"))
        self.assertIn("퍼펙트게임", event_ontology.label_of("SP.GAME.PITCH.PERFECT_GAME"))

    def test_every_official_faq_mechanic_has_a_mapping(self):
        self.assertEqual([], event_ontology.faq_coverage_problems())
        self.assertGreaterEqual(len(event_ontology.FAQ_CHECKLIST), 36)
        mechanical = [row for row in event_ontology.FAQ_CHECKLIST if event_ontology.MECHANICAL_ONLY in row["mapping"]]
        self.assertGreaterEqual(len(mechanical), 3)

    def test_all_twenty_eight_legacy_situations_resolve_to_registered_types(self):
        catalog = story_engine.catalog()
        count = 0
        for category in catalog["categories"]:
            for situation in category["situations"]:
                row = event_ontology.resolve_legacy(category["id"], situation["id"])
                self.assertTrue(event_ontology.is_registered(row["event_type"]), row)
                self.assertIn(row["thread_type"], nc.THREAD_TYPES)
                count += 1
        self.assertEqual(28, count)
        self.assertEqual(28, len(event_ontology.LEGACY_SITUATION_MAP))
        with self.assertRaises(KeyError):
            event_ontology.resolve_legacy("media", "unknown")

    def test_slice_one_dialogue_acts_map_to_registered_types(self):
        self.assertEqual(10, len(event_ontology.SLICE_ONE_DIALOGUE_ACTS))
        for act in event_ontology.SLICE_ONE_DIALOGUE_ACTS:
            row = event_ontology.DIALOGUE_ACT_EVENTS[act]
            if row["creates_event"]:
                self.assertTrue(event_ontology.is_registered(row["event_type"]), act)


class LedgerMigrationTests(unittest.TestCase):
    def test_v4_ledger_migrates_additively_with_backup_and_log(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.json"
            v4 = {
                "schema_version": 4,
                "world_id": "w",
                "history": [{"kind": "NEW_GAME", "date": {"year": 2027, "month": 7, "day": 24}, "source_hash": "a"}],
                "story_sessions": {"2027-07-24": {"schema_version": 1, "turns": [{"id": "t1"}], "status": "open"}},
                "daily_archive": [{"id": "arc", "game_date": "2027-07-24"}],
                "community_memory": {"recent_phrases": ["x"]},
            }
            path.write_text(json.dumps(v4, ensure_ascii=False), encoding="utf-8")
            ledger = Ledger(str(path), "w")
            self.assertEqual(5, ledger.state["schema_version"])
            self.assertEqual(v4["history"], ledger.state["history"])
            self.assertEqual(v4["story_sessions"], ledger.state["story_sessions"])
            self.assertEqual(v4["daily_archive"], ledger.state["daily_archive"])
            self.assertEqual(["x"], ledger.state["community_memory"]["recent_phrases"])
            for key in ("conversation_turns", "world_events", "reaction_instances", "memory_pins", "narrative_prop_events"):
                self.assertEqual([], ledger.state[key])
            for key in ("world_entities", "relationship_edges", "narrative_threads", "fact_registry", "narrative_props"):
                self.assertEqual({}, ledger.state[key])
            self.assertEqual([], ledger.state["narrative_memory"]["signatures"])
            backups = glob.glob(str(Path(temp) / "ledger.v4-backup-*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(4, json.loads(Path(backups[0]).read_text(encoding="utf-8"))["schema_version"])
            self.assertEqual(1, len(ledger.state["migration_log"]))
            self.assertEqual(4, ledger.state["migration_log"][0]["from_schema"])
            # A second load before the first save must not create another backup.
            Ledger(str(path), "w")
            self.assertEqual(1, len(glob.glob(str(Path(temp) / "ledger.v4-backup-*.json"))))
            ledger.save()
            reloaded = Ledger(str(path), "w")
            self.assertEqual(5, reloaded.state["schema_version"])
            self.assertEqual(1, len(reloaded.state["migration_log"]))
            self.assertEqual(1, len(glob.glob(str(Path(temp) / "ledger.v4-backup-*.json"))))

    def test_fresh_ledger_has_no_backup_or_migration_log(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(str(Path(temp) / "ledger.json"), "w")
            self.assertEqual([], ledger.state["migration_log"])
            self.assertIsNone(ledger.migration_backup)


if __name__ == "__main__":
    unittest.main()
