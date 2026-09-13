"""Phase 5 regressions: fresh prose, bound evidence, and coherent replies."""

from __future__ import annotations

import copy
import re
import unittest
from unittest.mock import patch

import community
import editorial_engine as editorial
import game_narrative
import provider_feed
import realism_gate
import spotlight_engine
import stat_engine
import story_engine


def snapshot(*, name="Paul Skenes", player_id=99, day=24, digest="editorial", stats=None):
    return {
        "player": {"id": player_id, "name": name, "team": "Yokohama DeNA", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": day, "career_year": 2},
        "content_hash": digest,
        "profile_fingerprint": "editorial-fixture",
        "stats": stats if stats is not None else {
            "pit_IP": 153, "pit_TBF": 465, "pit_H": 4, "pit_K": 388, "pit_W": 17,
            "bat_AB": 277, "bat_H": 201, "bat_HR": 105, "bat_RBI": 223,
            "bat_R": 174, "bat_SO": 11, "bat_SB": 129, "bat_AVG": .726,
        },
    }


def event_for(snap, *, kind="NO_CHANGE", role="no_appearance", delta=None):
    return {"snapshot": snap, "kind": kind, "role": role, "delta": delta or {},
            "assess": stat_engine.assess(snap["stats"]), "milestones": [],
            "baseline_only": kind == "WORLD_INIT"}


CONFIG = {"mode": "standard", "heat": 10, "platforms": ["dc", "fmk", "mlb"]}
CAREER = {"player_grade": {"code": "EX", "label": "역사적 현상", "score": 100},
          "records": {"season": [{"label": label, "status": "broken"} for label in ("홈런", "타점", "득점", "도루")]}}


def spotlight(event):
    return spotlight_engine.evaluate(event["snapshot"], CAREER, event, CONFIG)


def all_text(feed):
    return "\n".join([
        *(text for article in feed.get("media", []) for text in [article["title"], article.get("sub", ""), *article["body"]]),
        *(text for board in feed.get("boards", []) for text in [board["title"], *[c["text"] for c in board["comments"]]]),
    ])


class EditorialProseTests(unittest.TestCase):
    def test_new_quiet_day_feed_has_no_meta_language_or_outlet_clones(self):
        event = event_for(snapshot())
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        report = realism_gate.audit_feed(feed, names=["Paul Skenes", "Skenes"])
        self.assertTrue(realism_gate.feed_is_clean(report), report)
        self.assertGreaterEqual(len(feed["media"]), 3)

    def test_public_button_articles_have_different_editorial_arguments(self):
        snap = snapshot()
        selection = story_engine.normalize({"category": "media", "situation": "praise_team",
                                            "target": "reporter", "visibility": "public"})
        event = story_engine.build_event(snap, 1, selection, spotlight=spotlight(event_for(snap)))
        report = realism_gate.audit_feed(event["reactions"], names=["Paul Skenes", "Skenes"])
        self.assertTrue(realism_gate.feed_is_clean(report), report)
        purposes = [row["editorial_purpose"] for row in event["reactions"]["media"]]
        self.assertEqual(len(purposes), len(set(purposes)))
        self.assertIn("공을 팀에 돌렸", all_text(event["reactions"]))

    def test_private_event_reactions_use_only_public_record_context(self):
        snap = snapshot()
        selection = story_engine.normalize({"category": "personal", "situation": "call_family",
                                            "target": "family", "visibility": "private",
                                            "user_text": "비밀 햄버거 약속은 엄마한테만 말했어"})
        event = story_engine.build_event(snap, 1, selection, spotlight=spotlight(event_for(snap)))
        public = all_text(event["reactions"])
        for secret in ("햄버거", "엄마", "가족", "통화", "비공개 행동", "EX", "세계선"):
            self.assertNotIn(secret, public)
        self.assertIn("388", public)
        for row in event["reactions"]["media"] + event["reactions"]["boards"]:
            self.assertEqual("public_record_background", row["context_visibility"])

    def test_article_blocks_and_reply_claims_remain_protagonist_bound(self):
        event = event_for(snapshot())
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        for article in feed["media"]:
            self.assertEqual("99", article["protagonist_id"])
            self.assertTrue(article["universe_id"])
            self.assertTrue(article["outline_signature"])
            self.assertTrue(article["corpus_manifest"])
            self.assertEqual(article["body"], [b["text"] for b in article["blocks"] if b["type"] == "paragraph"])
            for block in article["blocks"]:
                self.assertLessEqual(len(block["text"]), 650)
                self.assertTrue(block["template_id"])
                self.assertTrue(block["fact_ids"])
        for board in feed["boards"]:
            posts = board["posts"]
            self.assertIsNone(posts[0]["parent_post_id"])
            for reply in posts[1:]:
                self.assertEqual(posts[0]["post_id"], reply["parent_post_id"])
                self.assertEqual(posts[0]["claim_id"], reply["addressed_claim_id"])
                self.assertEqual("99", reply["protagonist_id"])
                self.assertTrue(reply["fact_ids"])
                self.assertTrue(reply["emotional_function"])

    def test_hitless_secondary_line_does_not_receive_batting_praise(self):
        event = event_for(snapshot(), kind="NEW_GAME", role="two_way",
                          delta={"pit_IP": 9, "pit_TBF": 27, "pit_K": 27, "pit_H": 0,
                                 "bat_AB": 4, "bat_H": 0, "bat_HR": 0, "bat_RBI": 0})
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        text = all_text(feed)
        self.assertIn("4타수 무안타", text)
        self.assertIn("27탈삼진", text)
        for invented in ("맹타", "타석에서도 빛", "퍼펙트", "완투", "완봉", "노히트노런"):
            self.assertNotIn(invented, text)

    def test_planning_is_pure_and_repeatable(self):
        event = event_for(snapshot())
        memory = {"recent_phrases": [], "memes": ["기존 기억"]}
        before = copy.deepcopy((event, memory))
        first = community.build_feed(event, CONFIG, memory, spotlight=spotlight(event))
        second = community.build_feed(event, CONFIG, memory, spotlight=spotlight(event))
        self.assertEqual(first, second)
        self.assertEqual(before, (event, memory))

    def test_visible_comments_include_the_claim_to_which_replies_respond(self):
        event = event_for(snapshot())
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        for board in feed["boards"]:
            self.assertEqual(board["posts"], board["comments"])
            self.assertIn(board["posts"][0]["post_id"], {p["parent_post_id"] for p in board["posts"][1:]})
        texts = [c["text"] for b in feed["boards"] for c in b["comments"]]
        self.assertEqual(len(texts), len(set(texts)))

    def test_foreign_articles_keep_authored_original_and_matching_korean(self):
        event = event_for(snapshot())
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        foreign = [a for a in feed["media"] if a["lang"] != "ko"]
        self.assertEqual({"en", "ja"}, {a["lang"] for a in foreign})
        for article in foreign:
            self.assertEqual("authored_bilingual", article["translation_method"])
            self.assertEqual(len(article["original_body"]), len(article["translation_ko"]))
            for index, (original, translation) in enumerate(zip(article["original_body"], article["translation_ko"])):
                source, block = article["blocks"][index * 2:index * 2 + 2]
                self.assertEqual(original, source["text"])
                self.assertEqual("한국어: " + translation, block["text"])
                self.assertEqual(source["bilingual_pair_id"], block["bilingual_pair_id"])
                self.assertEqual(re.findall(r"\d+", original), re.findall(r"\d+", translation))
                self.assertTrue(block["original_template_id"])
                self.assertTrue(block["fact_ids"])

    def test_count_predicates_and_particle_slots_are_grammatical(self):
        event = event_for(snapshot(name="폴"))
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event))
        text = all_text(feed)
        for wrong in ("폴가", "폴를", "17승다", "388탈삼진다", "타점를", "홈런가", "기여을", "기여으로", "피안타은"):
            self.assertNotIn(wrong, text)
        self.assertIn("17승이다", text)

    def test_public_intervention_uses_action_moves_not_a_numerical_record_opening(self):
        snap = snapshot()
        selection = story_engine.normalize({"category": "media", "situation": "praise_team", "target": "reporter", "visibility": "public"})
        event = story_engine.build_event(snap, 1, selection, spotlight=spotlight(event_for(snap)))
        for article in event["reactions"]["media"]:
            self.assertTrue(any(".public.opening." in row["template_id"] for row in article["blocks"]))
            self.assertIn("공을 팀에 돌렸다", " ".join(article["body"]))
            self.assertNotIn("공을 팀에 돌린다가", article["title"])

    def test_every_referenced_fact_resolves_to_the_same_world_player_date(self):
        event = event_for(snapshot())
        feed = community.build_feed(event, CONFIG, {}, spotlight=spotlight(event), universe_id="world-a")
        facts = {row["fact_id"]: row for row in feed["editorial"]["facts"]}
        for fact in facts.values():
            self.assertEqual(("world-a", "99", "2027-07-24"), (fact["universe_id"], fact["protagonist_id"], fact["game_date"]))
            self.assertTrue(fact["source_ids"])
        for item in feed["media"] + feed["boards"]:
            for row in [item, *item.get("blocks", []), *item.get("posts", [])]:
                self.assertTrue(row["fact_ids"])
                self.assertTrue(set(row["fact_ids"]) <= facts.keys())

    def test_unknown_rookie_stays_small_and_heat_changes_expression_not_facts(self):
        event = event_for(snapshot(stats={"bat_AB": 8, "bat_H": 1, "bat_HR": 0}))
        spot = spotlight_engine.evaluate(event["snapshot"], {}, event, CONFIG)
        rookie = community.build_feed(event, CONFIG, {}, spotlight=spot)
        self.assertEqual([], rookie["media"])
        self.assertEqual("developing", rookie["editorial"]["impact"])
        event = event_for(snapshot())
        low = editorial.build(event, budget={"boards": 3, "comments": 18}, heat=2)
        high = editorial.build(event, budget={"boards": 3, "comments": 18}, heat=10)
        self.assertEqual(low["editorial"]["facts"], high["editorial"]["facts"])
        self.assertNotEqual(all_text(low), all_text(high))
        self.assertEqual("exceptional", high["editorial"]["impact"])

    def test_dc_fmk_and_mlb_have_distinct_registers_and_fictional_forum_metadata(self):
        event = event_for(snapshot())
        high = editorial.build(
            event,
            budget={"boards": 3, "comments": 18},
            universe_id="register-world",
            platforms=("dc", "fmk", "mlb"),
            heat=10,
        )
        boards = {row["code"]: row for row in high["boards"]}
        self.assertEqual({"dc", "fmk", "mlb"}, set(boards))
        self.assertRegex(boards["dc"]["title"], r"(?:ㅋㅋ|ㄷㄷ|ㅇㅇ|실화|뭐냐|있냐|말해봄)")
        self.assertRegex(boards["fmk"]["title"], r"(?:\[[^]]+\]|포텐|하이라이트|타팀팬|불타는 중)")
        self.assertRegex(boards["mlb"]["title"], r"(?:나요|까요|습니다|지점|생각)")
        self.assertTrue(any(row["author"].startswith("ㅇㅇ(") for row in boards["dc"]["comments"]))
        self.assertTrue(any("포텐" in row["text"] or "움짤" in row["text"] for row in boards["fmk"]["comments"]))
        self.assertTrue(any(re.search(r"(?:습니다|봅니다|네요|겠습니다)", row["text"]) for row in boards["mlb"]["comments"]))
        dc_average = sum(len(row["text"]) for row in boards["dc"]["comments"]) / len(boards["dc"]["comments"])
        mlb_average = sum(len(row["text"]) for row in boards["mlb"]["comments"]) / len(boards["mlb"]["comments"])
        self.assertLess(dc_average, mlb_average)
        self.assertEqual([], provider_feed.platform_register_violations(high))
        for board in boards.values():
            self.assertTrue(board["thread_meta"]["fictional"])
            self.assertEqual(len(board["comments"]) - 1, board["thread_meta"]["reply_count"])
            self.assertTrue(all(row["display_metadata_fictional"] for row in board["comments"]))

        low = editorial.build(
            event,
            budget={"boards": 3, "comments": 18},
            universe_id="register-world",
            platforms=("dc", "fmk", "mlb"),
            heat=2,
        )
        self.assertEqual(
            [[row["post_id"] for row in board["comments"]] for board in low["boards"]],
            [[row["post_id"] for row in board["comments"]] for board in high["boards"]],
        )
        self.assertEqual(
            [[row["up"] for row in board["comments"]] for board in low["boards"]],
            [[row["up"] for row in board["comments"]] for board in high["boards"]],
        )
        self.assertEqual(
            [board["thread_meta"] for board in low["boards"]],
            [board["thread_meta"] for board in high["boards"]],
        )

    def test_language_level_five_is_raw_where_native_but_mlb_stays_formal(self):
        event = event_for(snapshot())
        value = editorial.build(
            event,
            budget={"boards": 3, "comments": 30},
            universe_id="language-world",
            platforms=("dc", "fmk", "mlb"),
            heat=10,
            language_level=5,
        )
        boards = {row["code"]: row for row in value["boards"]}
        dc_text = " ".join(row["text"] for row in boards["dc"]["comments"])
        fmk_text = " ".join(row["text"] for row in boards["fmk"]["comments"])
        mlb_text = " ".join(row["text"] for row in boards["mlb"]["comments"])

        self.assertRegex(dc_text, r"(?:씨발|병신|지랄)")
        self.assertRegex(fmk_text, r"(?:씨발|새끼|처읽고)")
        self.assertNotRegex(mlb_text, r"(?:씨발|병신|지랄|새끼)")
        self.assertRegex(mlb_text, r"(?:습니다|합니다|불쾌합니다)")
        self.assertEqual(5, value["editorial"]["community_language_level"])
        self.assertEqual(30, sum(len(row["comments"]) for row in value["boards"]))


class EditorialEvidenceTests(unittest.TestCase):
    def test_known_unchanged_hits_are_restored_as_zero_without_inventing_missing_hits(self):
        old = snapshot(stats={"bat_AB": 100, "bat_H": 30, "bat_HR": 5}, day=23, digest="old")
        new = snapshot(stats={"bat_AB": 104, "bat_H": 30, "bat_HR": 5})
        event = event_for(new, kind="NEW_GAME", role="batting", delta={"bat_AB": 4})
        ctx = editorial.context(event, previous=old, universe_id="a")
        self.assertIn("4타수 무안타", ctx["evidence"])
        self.assertEqual(0, ctx["delta"]["bat_H"])
        delta_facts = [f for f in ctx["facts"].values() if f["scope"] == "observation_interval"]
        self.assertEqual(["snapshot:old", "snapshot:editorial"], delta_facts[0]["source_ids"])
        unknown_old = copy.deepcopy(old)
        del unknown_old["stats"]["bat_H"]
        unknown_ctx = editorial.context(event, previous=unknown_old, universe_id="a")
        self.assertNotIn("무안타", unknown_ctx["evidence"])
        self.assertNotIn("bat_H", unknown_ctx["delta"])

    def test_two_out_delta_and_rate_use_baseball_innings(self):
        old = snapshot(stats={"pit_IP": 5.2, "pit_K": 10}, day=23, digest="old")
        new = snapshot(stats={"pit_IP": 6.1, "pit_K": 12})
        ctx = editorial.context(event_for(new, kind="NEW_GAME", role="pitching", delta={"pit_IP": .2, "pit_K": 2}), previous=old)
        self.assertEqual(.2, ctx["delta"]["pit_IP"])
        rate = next(f for f in ctx["facts"].values() if f["kind"] == "rate_pit_K")
        self.assertEqual(19, rate["values"]["pit_outs"])
        self.assertIn("17.05", rate["text"])
        self.assertEqual("pit_K*27/pit_outs/1.0.0", rate["formula"])

    def test_previous_player_year_career_or_unverified_delta_cannot_supply_facts(self):
        new = snapshot(stats={"bat_AB": 104, "bat_H": 31})
        old = snapshot(stats={"bat_AB": 100, "bat_H": 30}, day=23, digest="old")
        event = event_for(new, kind="NEW_GAME", role="batting", delta={"bat_AB": 4, "bat_H": 1})
        variants = []
        for group, key, value in (("player", "id", 100), ("date", "year", 2026), ("date", "career_year", 1), ("date", "day", 25), ("stats", "bat_H", 29)):
            variant = copy.deepcopy(old)
            variant[group][key] = value
            variants.append(variant)
        for prior in variants:
            with self.subTest(previous=prior):
                ctx = editorial.context(event, previous=prior, universe_id="a")
                self.assertEqual({}, ctx["delta"])
                self.assertTrue(ctx["rejected_inputs"])
                self.assertFalse(any("snapshot:old" in f["source_ids"] for f in ctx["facts"].values()))

    def test_multi_day_changes_are_an_interval_not_a_dated_single_game(self):
        old = snapshot(stats={"bat_AB": 100, "bat_H": 30}, day=20, digest="old")
        new = snapshot(stats={"bat_AB": 116, "bat_H": 33})
        event = event_for(new, kind="NEW_GAME", role="batting", delta={"bat_AB": 16, "bat_H": 3})
        ctx = editorial.context(event, previous=old, universe_id="a")
        self.assertIn("16타수 3안타", ctx["evidence"])
        self.assertIsNone(game_narrative.plan(event, old, universe_id="a"))
        self.assertIn("직전 확인 이후", ctx["evidence"])
        self.assertIsNone(ctx["game_instance_id"])

    def test_invalid_numbers_and_corrections_never_become_new_achievements(self):
        for stats in ({"bat_AB": 4, "bat_H": 5}, {"pit_IP": 6.3}, {"pit_K": float("nan")}, {"bat_HR": True}, {"bat_AB": 4, "bat_H": 1, "bat_HR": 2}):
            ctx = editorial.context(event_for(snapshot(stats=stats)))
            self.assertTrue(ctx["rejected_inputs"])
            self.assertEqual([], ctx["anchors"])
        ctx = editorial.context(event_for(snapshot(), kind="CORRECTION", delta={"pit_K": -1}))
        self.assertEqual({}, ctx["delta"])
        self.assertNotIn("변화분", ctx["evidence"])

    def test_stat_formulas_never_invent_war_salary_era_or_official_record_rank(self):
        ctx = editorial.context(event_for(snapshot()))
        formulas = [row.get("formula") or "" for row in ctx["facts"].values()]
        self.assertTrue(any("bat_H/bat_AB" in f for f in formulas))
        self.assertTrue(any("4*bat_HR" in f for f in formulas))
        checkpoint = next(row for row in ctx["facts"].values() if row["kind"] == "checkpoint_pit_K")
        self.assertIn("400탈삼진", checkpoint["text"])
        self.assertIn("12개", checkpoint["text"])
        self.assertEqual(400, checkpoint["values"]["target"])
        self.assertFalse(any(word in f.lower() for word in ("war", "salary", "era", "rank") for f in formulas))


class EditorialMemoryTests(unittest.TestCase):
    def test_new_memory_is_bounded_scoped_and_does_not_modify_other_fields(self):
        memory = {"nicknames": ["기존 별명"], "private": {"do_not_touch": True}}
        for i in range(15):
            event = event_for(snapshot(digest=f"day-{i}"))
            result = editorial.build(event, budget={"media": 6, "boards": 4, "comments": 20}, memory=memory, universe_id="a")
            self.assertTrue(realism_gate.feed_is_clean(result["editorial"]["audit"]), result["editorial"]["audit"])
            editorial.remember(memory, result)
        self.assertLessEqual(len(memory[editorial.MEMORY_KEY]["articles"]), 50)
        self.assertLessEqual(len(memory[editorial.MEMORY_KEY]["phrases"]), 180)
        self.assertEqual(["기존 별명"], memory["nicknames"])
        self.assertEqual({"do_not_touch": True}, memory["private"])

    def test_recent_headline_and_body_shapes_are_not_reissued_with_new_numbers(self):
        memory, saved = {}, []
        for i in range(7):
            snap = snapshot(digest=f"new-{i}")
            snap["stats"]["pit_K"] += i
            event = event_for(snap)
            result = editorial.build(event, budget={"media": 6, "boards": 3, "comments": 15}, memory=memory, universe_id="a")
            self.assertTrue(result["media"])
            before = memory.get(editorial.MEMORY_KEY, {}).get("articles", [])
            for article in result["media"]:
                self.assertNotIn(article["headline_skeleton"], [r["skeleton"] for r in before])
                for prior in before:
                    self.assertFalse(editorial._near_body(article["body_signature"], prior["body_signature"]))
            saved.extend(result["media"])
            editorial.remember(memory, result)
        self.assertEqual(8, len({a["editorial_purpose"] for a in saved}))

    def test_saturated_pack_reports_capacity_instead_of_padding_repeated_text(self):
        event = event_for(snapshot())
        first = editorial.build(event, budget={"media": 8}, universe_id="a")
        memory = {}
        editorial.remember(memory, first)
        with patch.object(editorial, "_title", return_value=None):
            result = editorial.build(event, budget={"media": 8, "boards": 5, "comments": 20}, memory=memory, universe_id="a")
        self.assertTrue(result["editorial"]["capacity_limited"])
        self.assertEqual([], result["media"])
        self.assertEqual([], result["boards"])

    def test_foreign_world_memory_cannot_introduce_other_player_text(self):
        old = editorial.build(event_for(snapshot(name="FOREIGN_PLAYER", player_id=7)), budget={"media": 6}, universe_id="old")
        memory = {"recent_phrases": ["외부 문장"]}
        editorial.remember(memory, old)
        new = editorial.build(event_for(snapshot(name="CURRENT_PLAYER")), budget={"media": 6, "boards": 4, "comments": 20}, memory=memory, universe_id="new")
        self.assertNotIn("FOREIGN_PLAYER", all_text(new))
        self.assertNotIn("외부 문장", all_text(new))
        self.assertEqual("new", new["editorial"]["binding"]["universe_id"])

    def test_duplicate_commit_is_idempotent_and_archived_payload_stays_unchanged(self):
        memory = {}
        result = editorial.build(event_for(snapshot()), budget={"media": 6, "boards": 3, "comments": 15}, universe_id="a")
        archive = copy.deepcopy(result)
        editorial.remember(memory, result)
        before = copy.deepcopy(memory)
        editorial.remember(memory, result)
        self.assertEqual(before, memory)
        self.assertEqual(archive, result)

    def test_new_overlay_deduplicates_only_the_matching_game_bundle(self):
        event = event_for(snapshot(), kind="NEW_GAME", role="pitching", delta={"pit_K": 27, "pit_IP": 9})
        bundle = {"instance_id": "i", "universe_id": "a", "protagonist_id": "99", "source_hash": "editorial", "game_date": "2027-07-24",
                  "dominant": {"lead": {"verified": True, "label": "27탈삼진"}}, "reactions": {"media": [{"title": "legacy recap"}]}}
        feed = editorial.build(event, universe_id="a", budget={"media": 6}, game_bundle=bundle)
        combined = game_narrative.overlay(feed, bundle)
        self.assertEqual(feed["media"], combined["media"])
        for key, value in (("universe_id", "b"), ("protagonist_id", "100"), ("game_date", "2027-07-23")):
            foreign = dict(bundle, **{key: value})
            self.assertEqual(feed, game_narrative.overlay(feed, foreign))
        legacy = {"media": [], "boards": []}
        self.assertEqual(bundle["reactions"]["media"], game_narrative.overlay(legacy, bundle)["media"])


if __name__ == "__main__":
    unittest.main()
