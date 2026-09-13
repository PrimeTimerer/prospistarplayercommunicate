"""Finite-expression, emotional-continuity, privacy and archive regressions."""

import copy
import json
import re
import unittest
from unittest.mock import patch

import interaction_voice as voice
import editorial_engine as editorial
import narrative_contracts as nc
import realism_gate
import star_interactions as interactions

try:
    from tests import test_star_interactions as fixtures
except ImportError:
    import test_star_interactions as fixtures


class VoicePlanTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"player": {"id": 123, "name": "가상 주인공", "team": "가상 구단"},
                         "date": {"year": 2027, "month": 7, "day": 24}, "stats": {}, "content_hash": "synthetic"}
        self.date, self.world = "2027-07-24", "voice-world"
        self.state = {}

    def start(self, **extra):
        proposal = interactions.start_plan(
            {"situation": "player_exchange", "target": "rookie", "participant_name": "가상 후배",
             "interaction_topic": "햄버거", **extra}, snapshot=self.snapshot,
            game_date=self.date, universe_id=self.world, state=self.state)
        self.commit(proposal)
        return proposal["interaction_id"]

    def proposal(self, ident, beat, line=""):
        return interactions._proposal(self.state[interactions.STORE][ident], beat, self.date, line=line, state=self.state)

    def commit(self, proposal):
        result = interactions.realized_event(self.state, proposal, snapshot=self.snapshot,
                                             game_date=self.date, universe_id=self.world, spotlight={})
        interactions.commit(self.state, proposal, snapshot=self.snapshot, game_date=self.date, universe_id=self.world,
                            event_id=f"voice-event-{len(self.state.get(interactions.EVENTS, []))}")
        return result

    def test_all_valid_actions_roles_and_beats_use_resolvable_provenance(self):
        for action in interactions.pack()["actions"]:
            for role in interactions.ROLES:
                if action in ("learning", "player_exchange") and role not in ("teammate", "rookie", "rival"):
                    continue
                ident = self.start(situation=action, target=role, participant_name="" if role == "self" else role)
                row = self.state[interactions.STORE][ident]
                for beat in interactions.BEATS:
                    if beat == "start":
                        continue
                    with self.subTest(action=action, role=role, beat=beat):
                        plan = self.proposal(ident, beat, "그 내용으로 약속하자")
                        blocks = interactions._blocks(row, plan, snapshot=self.snapshot)
                        self.assertEqual([], nc.validate_blocks(blocks))
                        self.assertIn("가상 주인공", nc.project_blocks(blocks))
                        for block in blocks:
                            self.assertLessEqual(len(block["text"]), 650)
                            self.assertNotRegex(block["text"], r"\{\w+(?::\w+)?\}")
                            if block.get("template_id"):
                                _, path = block["template_id"].split(":", 1)
                                self.assertIsInstance(voice.node(interactions.pack(), path), str)
                        if role == "self" or beat == "share":
                            self.assertFalse(any(block["type"] == "quote" for block in blocks))
                        else:
                            self.assertIn(f"/{role}/", plan["expression_plan"]["selections"]["reply"])

    def test_preview_is_pure_deterministic_and_matches_confirmation(self):
        ident = self.start()
        before = copy.deepcopy(self.state)
        a, b = self.proposal(ident, "joke", "농담"), self.proposal(ident, "joke", "농담")
        self.assertEqual(a, b)
        blocks = interactions._blocks(self.state[interactions.STORE][ident], a, snapshot=self.snapshot)
        self.assertEqual(before, self.state)
        self.assertEqual(blocks, self.commit(a)["blocks"])
        self.assertEqual(a["expression_plan"], self.state[interactions.EVENTS][-1]["expression"])

    def test_start_preview_freezes_stature_coda_even_if_attention_changes(self):
        response = interactions.chat_plan({}, {"category": "starplayer", "situation": "player_exchange", "target": "rookie",
                                             "participant_name": "후배", "user_text": "함께 이야기하고 싶어"},
                                          snapshot=self.snapshot, game_date=self.date, universe_id=self.world, sequence=1,
                                          spotlight={"tier_index": 5})
        proposal = response["proposed_events"][0]
        result = interactions.realized_event({}, proposal, snapshot=self.snapshot, game_date=self.date,
                                             universe_id=self.world, spotlight={"tier_index": 0})
        self.assertEqual(response["reply"]["blocks"][1:], result["blocks"])
        self.assertTrue(any(block.get("template_id", "").endswith(":stature_coda") for block in result["blocks"]))

    def test_jokes_do_not_erase_conflict_and_repair_needs_agreement(self):
        ident = self.start()
        self.commit(self.proposal(ident, "disagree"))
        joked = self.commit(self.proposal(ident, "joke"))
        self.assertEqual("strained", joked["provenance"]["expression"]["mood"])
        self.assertEqual(1, self.state[interactions.STORE][ident]["emotional_continuity"]["tension"])
        repaired = self.commit(self.proposal(ident, "reconcile"))
        self.assertEqual("repairing", repaired["provenance"]["expression"]["mood"])
        still_repairing = self.commit(self.proposal(ident, "joke"))
        self.assertEqual("repairing", still_repairing["provenance"]["expression"]["mood"])
        agreed = self.commit(self.proposal(ident, "agree"))
        self.assertEqual("warm", agreed["provenance"]["expression"]["mood"])

    def test_new_meeting_recalls_only_same_named_counterpart(self):
        ident = self.start()
        self.commit(self.proposal(ident, "disagree"))
        old = copy.deepcopy(self.state[interactions.STORE][ident])
        second = self.start(situation="outing_visit", interaction_topic="커피")
        trace = self.state[interactions.EVENTS][-1]["expression"]
        self.assertEqual("strained", trace["mood"])
        self.assertEqual("햄버거", trace["previous_topic"])
        self.assertIn("recall", trace["selections"])
        self.assertEqual(old, self.state[interactions.STORE][ident])
        other = self.start(participant_name="다른 후배")
        self.assertNotEqual(second, other)
        self.assertIsNone(self.state[interactions.EVENTS][-1]["expression"]["anchor"])

    def test_unnamed_roles_and_solo_do_not_merge_different_meetings(self):
        for settings in ({"participant_name": ""}, {"participant_name": "", "situation": "outing_walk", "target": "self"}):
            ident = self.start(**settings)
            self.commit(self.proposal(ident, "disagree"))
            self.start(**settings)
            self.assertEqual("neutral", self.state[interactions.EVENTS][-1]["expression"]["mood"])

    def test_private_history_is_not_recalled_into_a_clubhouse_meeting(self):
        ident = self.start(interaction_topic="비공개주제")
        self.commit(self.proposal(ident, "disagree"))
        self.start(interaction_topic="새로운주제", visibility="clubhouse")
        trace = self.state[interactions.EVENTS][-1]["expression"]
        self.assertIsNone(trace["anchor"])
        self.assertNotIn("비공개주제", json.dumps(trace, ensure_ascii=False))

    def test_other_world_player_and_future_events_are_excluded(self):
        ident = self.start()
        self.commit(self.proposal(ident, "disagree"))
        row = copy.deepcopy(self.state[interactions.STORE][ident])
        for key, value in (("universe_id", "other"), ("protagonist_id", "456")):
            foreign = dict(row, **{key: value})
            self.assertEqual([], voice.related_events(self.state, foreign, self.date))
        self.assertEqual([], voice.related_events(self.state, row, "2027-07-23"))
        bad = copy.deepcopy(self.state)
        for event in bad[interactions.EVENTS]:
            event["universe_id"] = "foreign"
        self.assertEqual([], voice.related_events(bad, row, self.date))

    def test_other_meeting_with_same_counterpart_invalidates_preview(self):
        first = self.start()
        second = self.start(interaction_topic="커피")
        pending = self.proposal(first, "joke")
        self.commit(self.proposal(second, "disagree"))
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "같은 상대"):
            self.commit(pending)
        self.assertEqual(before, self.state)

    def test_unrelated_counterpart_does_not_invalidate_frozen_preview(self):
        first = self.start()
        pending = self.proposal(first, "joke")
        second = self.start(participant_name="다른 후배")
        self.commit(self.proposal(second, "disagree"))
        self.commit(pending)

    def test_lru_reuse_is_reported_without_counter_or_date_padding(self):
        ident = self.start()
        traces, bodies = [], []
        for _ in range(12):
            result = self.commit(self.proposal(ident, "joke", "햄버거 농담"))
            traces.append(result["provenance"]["expression"])
            bodies.append(result["text"])
        self.assertEqual(6, len({trace["selections"]["setting"] for trace in traces[:6]}))
        self.assertEqual(2, len({trace["selections"]["reply"] for trace in traces[:2]}))
        self.assertIn("reply", traces[2]["reused_groups"])
        self.assertTrue(all(a != b for a, b in zip(bodies, bodies[1:])))
        self.assertNotRegex(" ".join(bodies), r"voice-event-|voice-world|synthetic")

    def test_hamburger_reply_requires_a_relevant_topic(self):
        burger = self.start()
        plain = self.start(participant_name="다른 후배", interaction_topic="음악")
        self.assertIn("/hamburger/", self.proposal(burger, "joke")["expression_plan"]["selections"]["reply"])
        self.assertIn("/replies/", self.proposal(plain, "joke")["expression_plan"]["selections"]["reply"])

    def test_common_joke_inflections_stay_in_the_bound_encounter(self):
        ident = self.start()
        for text in ("웃긴 얘기를 해 볼게", "좀 웃겨 볼까", "농담은 하지 마"):
            response = interactions.chat_plan(self.state, {"interaction_id": ident, "user_text": text},
                                              snapshot=self.snapshot, game_date=self.date, universe_id=self.world, sequence=1)
            self.assertEqual("refuse" if "하지 마" in text else "joke", response["proposed_events"][0]["beat"])

    def test_learning_never_acquires_ability_from_prose_or_emotion(self):
        ident = self.start(situation="learning", interaction_topic="커브", learning_progress="practice",
                           interaction_mode="user_confirmed", action_confirmed=True)
        for beat in ("confide", "reconcile", "agree", "joke"):
            self.commit(self.proposal(ident, beat))
        row = self.state[interactions.STORE][ident]
        self.assertEqual("practice", row["game_progress"])
        self.assertEqual("fictional_intervention", row["emotional_continuity"]["evidence_class"])

    def test_legacy_events_replay_without_rewriting_and_old_proposals_still_work(self):
        ident = self.start()
        self.commit(self.proposal(ident, "disagree"))
        for event in self.state[interactions.EVENTS]:
            event.pop("expression", None)
        self.state[interactions.STORE][ident].pop("emotional_continuity", None)
        before = copy.deepcopy(self.state)
        proposed = self.proposal(ident, "joke")
        self.assertEqual("strained", proposed["expression_plan"]["mood"])
        self.assertEqual(before, self.state)
        proposed.pop("expression_plan")
        self.commit(proposed)
        self.assertEqual(before[interactions.EVENTS], self.state[interactions.EVENTS][:-1])

    def test_solo_continuity_never_creates_a_counterpart(self):
        ident = self.start(situation="outing_walk", target="self", participant_name="")
        for beat in ("joke", "joke", "confide", "disagree", "reconcile", "agree"):
            result = self.commit(self.proposal(ident, beat))
            self.assertFalse(any(block["type"] == "quote" for block in result["blocks"]))
            self.assertNotRegex(result["text"], r"상대가 답할|마주 앉|서로의|동료가|후배가")

    def test_public_projection_has_no_private_emotion_topic_or_source_ids(self):
        ident = self.start(participant_name="비밀상대", interaction_topic="비밀주제", interaction_place="비밀장소")
        self.commit(self.proposal(ident, "confide"))
        self.commit(self.proposal(ident, "disagree"))
        result = self.commit(self.proposal(ident, "share"))
        public = result["text"] + json.dumps(result["reactions"], ensure_ascii=False)
        for secret in ("비밀상대", "비밀주제", "비밀장소", "voice-event-", "strained", "vulnerable"):
            self.assertNotIn(secret, public)
        self.assertEqual([], result["provenance"]["expression"]["source_event_ids"])
        self.assertEqual("private", self.state[interactions.STORE][ident]["visibility"])


class VoiceServiceTests(unittest.TestCase):
    setUp = fixtures.InteractionServiceTests.setUp
    ledger = fixtures.InteractionServiceTests.ledger
    row = fixtures.InteractionServiceTests.row
    start = fixtures.InteractionServiceTests.start
    chat = fixtures.InteractionServiceTests.chat
    confirm = fixtures.InteractionServiceTests.confirm
    advance_home_run = fixtures.InteractionServiceTests.advance_home_run

    def test_realized_publication_keeps_and_commits_its_editorial_memory(self):
        ident = self.start()["interaction_id"]
        earlier = {row["id"] for row in self.ledger().state["community_memory"][editorial.MEMORY_KEY]["articles"]}
        result = self.confirm(self.chat(ident, "기자들에게 공개하자"))
        reactions = result["item"]["reactions"]
        self.assertTrue("editorial" in reactions, "realized editorial routing metadata was lost")
        stored = self.ledger().state["community_memory"][editorial.MEMORY_KEY]
        self.assertTrue(stored["articles"])
        self.assertEqual(self.world_id, stored["binding"]["universe_id"])
        self.assertEqual(earlier | {article["id"] for article in reactions["media"]}, {row["id"] for row in stored["articles"]})

    def test_repeated_public_share_preserves_old_text_and_avoids_old_headlines(self):
        ident = self.start()["interaction_id"]
        first = self.confirm(self.chat(ident, "기자들에게 공개하자"))["item"]
        titles = {row["title"] for row in first["reactions"]["media"]}
        second = self.confirm(self.chat(ident, "기자들에게 공개하자"))["item"]
        self.assertTrue(second["reactions"]["media"])
        self.assertFalse(titles & {row["title"] for row in second["reactions"]["media"]})
        stored = self.ledger().state["story_sessions"]["2027-07-24"]["turns"]
        self.assertEqual(first, next(row for row in stored if row["id"] == first["id"]))

    def test_service_preserves_preview_blocks_and_persists_expression_only_on_confirm(self):
        ident = self.start()["interaction_id"]
        old = self.ledger().state[interactions.EVENTS]
        reply = self.chat(ident, "햄버거 계산 농담")
        self.assertEqual(old, self.ledger().state[interactions.EVENTS])
        expected = reply["response"]["reply"]["blocks"][1:]
        result = self.confirm(reply)
        self.assertEqual(expected, result["item"]["scene"]["blocks"])
        self.assertEqual(reply["response"]["provenance"]["expression"],
                         result["item"]["narrative_provenance"]["expression"])

    def test_next_day_restart_retains_tension_and_sealed_text(self):
        ident = self.start()["interaction_id"]
        self.confirm(self.chat(ident, "그 생각에는 반대야"))
        self.snap["date"]["day"] = 25
        self.snap["content_hash"] = "voice-next-day"
        self.app.check_save(lambda *_args: None)
        sealed = copy.deepcopy(self.ledger().state["story_sessions"]["2027-07-24"])
        self.app = type(self.app)()
        with patch.object(self.app, "_read_snapshot", return_value=copy.deepcopy(self.snap)):
            result = self.confirm(self.chat(ident, "다시 이어가자"))
        self.assertEqual("strained", result["item"]["narrative_provenance"]["expression"]["mood"])
        self.assertEqual(sealed, self.ledger().state["story_sessions"]["2027-07-24"])

    def test_cross_encounter_stale_confirmation_leaves_file_unchanged(self):
        first = self.start()["interaction_id"]
        second = self.start(interaction_topic="커피")["interaction_id"]
        pending = self.chat(first, "농담을 건넨다")
        self.confirm(self.chat(second, "그 생각에는 반대야"))
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "같은 상대"):
            self.confirm(pending)
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_two_promises_with_same_person_receive_fresh_callback_plans(self):
        first = self.start()["interaction_id"]
        self.confirm(self.chat(first, "다음 홈런 치면 햄버거 사줄게 약속"))
        second = self.start(interaction_topic="커피")["interaction_id"]
        self.confirm(self.chat(second, "다음 홈런 치면 커피 사줄게 약속"))
        self.advance_home_run()
        self.app.check_save(lambda *_args: None)
        for ident in (first, second):
            self.assertEqual("reminded", self.row(ident)["promise"]["status"])
        callbacks = [event for event in self.ledger().state[interactions.EVENTS] if event["beat"] == "callback"]
        self.assertEqual(2, len(callbacks))
        self.assertEqual(callbacks[0]["event_id"], callbacks[1]["expression"]["anchor"])
        self.assertIn("/rookie/callback/", callbacks[0]["expression"]["selections"]["reply"])


class PublicActionTests(unittest.TestCase):
    def build(self, action, *, memory=None, ident="public-event", world="public-world", visibility="public"):
        snapshot = {"player": {"id": 123, "name": "가상 주인공", "team": "가상 구단"},
                    "date": {"year": 2027, "month": 7, "day": 24}, "stats": {}, "content_hash": "public-fixture"}
        return editorial.build({"snapshot": snapshot, "kind": "STORY", "role": "no_appearance", "delta": {}},
                               budget={"media": 6, "boards": 8, "comments": 40}, memory=memory,
                               universe_id=world, story={"situation": action, "visibility": visibility,
                               "user_text": "비밀계획", "participant_name": "비밀상대"}, story_id=ident)

    def test_four_public_actions_have_action_specific_claims_and_titles(self):
        for action, profile in editorial.packs()[0]["public_actions"].items():
            result = self.build(action)
            self.assertEqual(6, len(result["media"]))
            self.assertEqual(8, len(result["boards"]))
            self.assertEqual(8, len({board["claim"] for board in result["boards"]}))
            for board in result["boards"]:
                self.assertIn("discussion.public", board["comments"][0]["template_id"])
                self.assertNotRegex(board["title"], r"성적표|기록표|숫자")
                self.assertTrue(all("discussion.public" in comment["template_id"] for comment in board["comments"]))
            for article in result["media"]:
                self.assertEqual(action, article["public_action_id"])
                self.assertTrue(any(f".{action}.reading." in block["template_id"] for block in article["blocks"]))
            self.assertTrue(realism_gate.feed_is_clean(result["editorial"]["audit"]), result["editorial"]["audit"])

    def test_action_discussion_never_imports_private_input_or_numeric_claims(self):
        for action in editorial.packs()[0]["public_actions"]:
            result = self.build(action)
            public = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("비밀계획", public)
            self.assertNotIn("비밀상대", public)
            comments = " ".join(comment["text"] for board in result["boards"] for comment in board["comments"])
            self.assertNotRegex(comments, r"성적표|기록표|타율|\d+(?:홈런|탈삼진)")

    def test_action_evidence_does_not_repeat_reading_or_use_numeric_boilerplate(self):
        for action in editorial.packs()[0]["public_actions"]:
            result = self.build(action)
            for article in result["media"]:
                evidence = next(block["text"] for block in article["blocks"] if ".evidence." in block["template_id"])
                reading = next(block["text"] for block in article["blocks"] if ".reading." in block["template_id"])
                self.assertNotIn(reading, evidence)
                self.assertNotRegex(evidence, r"이 숫자|다른 단위|가상 주인공가|가상 주인공를")

    def test_new_authored_packs_use_typed_korean_particles(self):
        writing, discussion, _hash = editorial.packs()
        payload = json.dumps({"voice": interactions.pack()["continuity"], "actions": writing["public_actions"],
                              "lenses": writing["public_lenses"], "prose": writing["public_prose"],
                              "discussion": discussion["public"]}, ensure_ascii=False)
        self.assertEqual([], re.findall(r"\{(?:player|partner|anchor|topic)\}(?:은|는|이|가|을|를|과|와)(?=\s|[,.!?])", payload))

    def test_public_action_facts_resolve_without_game_fact_promotion(self):
        result = self.build("share_learning")
        facts = {fact["fact_id"]: fact for fact in result["editorial"]["facts"]}
        action_facts = [fact for fact in facts.values() if fact["kind"] == "public_action"]
        self.assertEqual(1, len(action_facts))
        self.assertEqual("fictional_intervention", action_facts[0]["evidence_class"])
        for item in result["media"] + result["boards"]:
            for row in [item, *item.get("blocks", []), *item.get("posts", [])]:
                self.assertTrue(set(row["fact_ids"]) <= facts.keys())
                self.assertEqual("public-world", row["universe_id"])
                self.assertEqual("123", row["protagonist_id"])
        for board in result["boards"]:
            for comment in board["comments"][1:]:
                self.assertEqual(board["comments"][0]["claim_id"], comment["addressed_claim_id"])

    def test_finite_public_pack_saturation_is_reported_not_padded(self):
        memory, limited = {}, []
        for index in range(16):
            result = self.build("share_outing_walk", memory=memory, ident=f"share-{index}")
            limited.append(result["editorial"]["capacity_limited"])
            self.assertTrue(realism_gate.feed_is_clean(result["editorial"]["audit"]), result["editorial"]["audit"])
            editorial.remember(memory, result)
        self.assertTrue(any(limited))

    def test_private_visibility_never_selects_public_action_discussion(self):
        result = self.build("share_learning", visibility="private")
        for board in result["boards"]:
            self.assertNotIn("discussion.public", board["comments"][0]["template_id"])
            self.assertEqual("public_record_background", board["context_visibility"])

    def test_changed_world_does_not_reuse_other_world_routing_memory(self):
        memory = {}
        editorial.remember(memory, self.build("share_outing_visit", world="first"))
        self.assertEqual(self.build("share_outing_visit", world="second"),
                         self.build("share_outing_visit", memory=memory, world="second"))


if __name__ == "__main__":
    unittest.main()
