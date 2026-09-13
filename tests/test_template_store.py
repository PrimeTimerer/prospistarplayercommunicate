#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template packs: authored coverage, particles, conditions, scoring, diversity."""

from __future__ import annotations

import time
import unittest

import event_ontology
import personas
import realism_gate
import template_store as ts


class ParticleTests(unittest.TestCase):
    def test_korean_particles_follow_final_consonants(self):
        self.assertEqual("스킨스는", ts.attach("스킨스", "은는"))
        self.assertEqual("폴이", ts.attach("폴", "이가"))
        self.assertEqual("감독을", ts.attach("감독", "을를"))
        self.assertEqual("동료와", ts.attach("동료", "과와"))
        self.assertEqual("서울로", ts.attach("서울", "으로로"))
        self.assertEqual("부산으로", ts.attach("부산", "으로로"))
        self.assertEqual("1로", ts.attach("1", "으로로"))
        self.assertEqual("3은", ts.attach("3", "은는"))
        self.assertEqual("Skenes는", ts.attach("Skenes", "은는"))
        self.assertEqual("Paul과", ts.attach("Paul", "과와"))
        self.assertEqual("Tim이", ts.attach("Tim", "이가"))
        self.assertEqual("★은(는)", ts.attach("★", "은는"))

    def test_slot_rendering_and_missing_slots(self):
        context = {"slots": {"player": "스킨스", "target_label": "감독"}, "facts": {"stats": {"pit_K": 388}}}
        self.assertEqual("스킨스는 감독을 봤다. 388개.", ts.render_text("{player:은는} {target_label:을를} 봤다. {stats.pit_K}개.", context))
        with self.assertRaises(ts.MissingSlot):
            ts.render_text("{stats.bat_HR}홈런", context)
        self.assertEqual(".726", ts.render_text("{avg}", {"slots": {"avg": 0.726}}))


class ConditionTests(unittest.TestCase):
    def test_condition_grammar(self):
        context = {"tier_index": 3, "visibility": "private", "emotion": {"valence": -0.5}, "facts": {"honors_text": "MVP"}, "fact_requirements": []}
        self.assertTrue(ts.evaluate_condition("tier_index >= 2", context))
        self.assertFalse(ts.evaluate_condition("tier_index < 2", context))
        self.assertTrue(ts.evaluate_condition("visibility == private", context))
        self.assertTrue(ts.evaluate_condition("visibility in private,clubhouse", context))
        self.assertTrue(ts.evaluate_condition("emotion.valence < 0", context))
        self.assertTrue(ts.evaluate_condition("facts.honors_text has", context))
        self.assertTrue(ts.evaluate_condition("fact_requirements missing", context))
        self.assertTrue(ts.evaluate_condition("facts.records_text missing", context))
        with self.assertRaises(ts.TemplateError):
            ts.evaluate_condition("nonsense", context)


class PackCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = time.perf_counter()
        cls.store = ts.TemplateStore()
        cls.load_seconds = time.perf_counter() - start

    def test_packs_load_cleanly_and_quickly(self):
        self.assertEqual([], self.store.problems)
        self.assertLess(self.load_seconds, 2.0)
        self.assertEqual(16, len(self.store.manifest_hash()))
        self.assertIn("core-ko", self.store.packs)
        self.assertIn("baseball-ja", self.store.packs)
        for pack in self.store.packs.values():
            self.assertEqual("authored", pack["license_class"])
            self.assertTrue(pack["attribution"])

    def test_first_slice_authoring_targets(self):
        korean = [row for row in self.store.templates.values() if row["locale"] == "ko"]
        japanese = [row for row in self.store.templates.values() if row["locale"] == "ja"]
        self.assertGreaterEqual(len(korean), 300, f"korean templates {len(korean)}")
        self.assertGreaterEqual(len(japanese), 100, f"japanese templates {len(japanese)}")
        variants = sum(len(row["variants"]) for row in self.store.templates.values())
        self.assertGreater(variants, len(self.store.templates))

    def test_every_first_slice_act_has_responses_for_several_personas(self):
        for act in event_ontology.SLICE_ONE_DIALOGUE_ACTS:
            if act == "clarify":
                continue
            rows = [row for row in self.store.templates.values() if act in row["responds_to"] and row["locale"] == "ko"]
            self.assertGreaterEqual(len(rows), 5, act)
            self.assertGreaterEqual(len({row["persona"] for row in rows}), 3, act)

    def test_every_template_passes_the_realism_gate_and_references_registered_ids(self):
        for row in self.store.templates.values():
            self.assertTrue(row["persona"] == "narrator" or row["persona"] in personas.PERSONAS, row["id"])
            for event_type in (row.get("effects") or {}).get("proposed_events") or []:
                self.assertTrue(event_ontology.is_registered(event_type), f"{row['id']} -> {event_type}")
            if row["kind"] in ("scene", "reaction"):
                for target in row["responds_to"]:
                    ok = target == "*" or event_ontology.domain_of(target) is not None
                    self.assertTrue(ok, f"{row['id']} responds_to {target}")
            for variant in row["variants"]:
                for field in ("text", "translation_ko", "headline", "dek"):
                    text = variant.get(field)
                    if not text:
                        continue
                    audit = realism_gate.audit_public_prose(text, protagonist_names=["{player}"])
                    codes = [code for code in audit.codes if code != "unregistered_quote_attribution"]
                    self.assertEqual([], codes, f"{row['id']} {field}: {text[:40]}")
                if row["locale"] != "ko":
                    self.assertTrue(variant.get("translation_ko"), row["id"])

    def test_foreign_reactions_cover_the_slice_domains_with_translation(self):
        japanese = [row for row in self.store.templates.values() if row["locale"] == "ja"]
        for domain in ("SP.GAME.PITCH", "SP.GAME.BAT", "SP.MILESTONE", "SP.ROSTER", "SP.MEDIA", "SP.RELATION", "SP.GAME.POST"):
            rows = [row for row in japanese if any(target == "*" or domain.startswith(target) or target.startswith(domain) for target in row["responds_to"])]
            self.assertGreaterEqual(len(rows), 3, domain)
        stances = {row.get("stance") for row in japanese}
        self.assertTrue({"support", "oppose", "qualify", "ask", "joke", "correct", "redirect"} <= stances)

    def test_retrieval_scoring_and_deterministic_choice(self):
        context = {
            "primary_act": "vent",
            "secondary_acts": [],
            "target": "manager",
            "visibility": "private",
            "tier_index": 1,
            "emotion": {"valence": -0.6, "arousal": 0.5},
            "preferred_personas": ["inner_voice", "veteran_teammate"],
            "locale": "ko",
            "slots": {"player": "스킨스", "target_label": "감독", "date_text": "7월 24일"},
            "facts": {},
            "fact_requirements": [],
            "recent_template_ids": [],
            "recent_signatures": [],
        }
        candidates = self.store.candidates(responds_to="vent", target="manager", visibility="private", locale="ko", context=context)
        self.assertGreaterEqual(len(candidates), 4)
        first = self.store.choose(candidates, context, seed="seed-a")
        second = self.store.choose(candidates, context, seed="seed-a")
        self.assertEqual(first["template"]["id"], second["template"]["id"])
        self.assertEqual(first["variant"]["text"], second["variant"]["text"])
        realized = self.store.realize(first, context)
        self.assertTrue(realized["text"])
        self.assertEqual("authored", realized["license_class"])
        self.assertIn("template_id", realized["signature"])
        self.assertNotIn("{player", realized["text"])
        score = first["score"]
        self.assertEqual(1.0, score["parts"]["act"])
        self.assertGreater(score["total"], 0.5)

    def test_fact_slots_make_templates_ineligible_when_facts_are_missing(self):
        context = {"primary_act": "ask", "fact_requirements": ["stats.pit_K"], "facts": {}, "slots": {"date_text": "7월 24일"}, "emotion": {"valence": 0}, "locale": "ko"}
        rows = self.store.candidates(responds_to="ask", locale="ko", context=context)
        self.assertFalse(any(row["id"] == "ko.ask.stats.pit_K.record" for row in rows))
        context["facts"] = {"stats": {"pit_K": 388}}
        rows = self.store.candidates(responds_to="ask", locale="ko", context=context)
        self.assertTrue(any(row["id"] == "ko.ask.stats.pit_K.record" for row in rows))
        chosen = self.store.choose(rows, context, seed="k")
        self.assertIn("388", self.store.realize(chosen, context)["text"])

    def test_diversity_filter_blocks_recent_openings(self):
        context = {"primary_act": "vent", "target": "manager", "visibility": "private", "emotion": {"valence": -0.5}, "locale": "ko", "slots": {"player": "P", "target_label": "감독"}, "facts": {}}
        candidates = self.store.candidates(responds_to="vent", target="manager", visibility="private", locale="ko", context=context)
        first = candidates[0]
        signature = {"persona": first["persona"], "opening": (first.get("signature") or {}).get("opening"), "head": realism_gate.normalize_text(first["variants"][0]["text"])[:8]}
        filtered = self.store.diversity_filter(candidates, [signature])
        self.assertNotIn(first["id"], [row["id"] for row in filtered])

    def test_fts_search_finds_meeting_templates(self):
        ids = self.store.fts_ids("면담 감독", locale="ko")
        self.assertTrue(ids)
        self.assertTrue(any("meeting" in template_id for template_id in ids))


if __name__ == "__main__":
    unittest.main()
