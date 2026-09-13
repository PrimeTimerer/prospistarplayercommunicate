"""World-local identity and player-context preservation contracts."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import narrative_contracts as nc
import personal_context
import service as service_module
import world_identity
from ledger_v2 import Ledger

try:
    from tests import test_attachments as fixtures
except ImportError:
    import test_attachments as fixtures


class WorldbookContractTests(unittest.TestCase):
    def setUp(self):
        self.world = "world-a"
        self.player = "player-7"
        self.date = "2027-07-24"
        self.state = {"world_entities": {}, "star_interactions": {}, "fact_registry": {}}

    def context(self, **extra):
        payload = {
            "basis": "authored_background",
            "label": "피츠버그에서 건너온 투수",
            "detail": "낯선 리그에서 동료들과 신뢰를 쌓아 가는 세계선 설정",
            "visibility": "private",
            **extra,
        }
        return personal_context.upsert_user_context(
            self.state,
            payload,
            universe_id=self.world,
            protagonist_id=self.player,
            player_label="가상 주인공",
            game_date=self.date,
        )

    def counterpart(self, **extra):
        payload = {
            "canonical_name": "가상 후배",
            "role": "rookie",
            "aliases": "막내, 햄버거 동료",
            "note": "첫 원정에서 알게 된 후배",
            **extra,
        }
        return world_identity.create_counterpart(
            self.state,
            payload,
            universe_id=self.world,
            protagonist_id=self.player,
            game_date=self.date,
        )

    def test_context_is_nested_under_the_world_protagonist_and_never_writes_stats(self):
        before = copy.deepcopy(self.state)
        row = self.context()
        self.assertEqual("fictional_intervention", row["evidence_class"])
        self.assertNotIn("personal_context", self.state)
        entity_id = world_identity.protagonist_entity_id(self.world, self.player)
        self.assertIn(row["context_id"], self.state["world_entities"][entity_id]["personal_context"]["items"])
        self.assertEqual(before["fact_registry"], self.state["fact_registry"])
        self.assertFalse(personal_context.view(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date,
        )["game_ability_writes"])

    def test_background_preference_and_hobby_keep_distinct_provenance(self):
        background = self.context()
        preference = self.context(
            basis="user_preference", label="치즈버거", detail="원정 전날 즐겨 찾는 메뉴",
        )
        hobby = self.context(
            basis="fictional_experience", label="재즈 피아노", detail="휴일에 가볍게 연주함",
            proficiency="skilled", visibility="clubhouse",
        )
        self.assertEqual("fictional_intervention", background["evidence_class"])
        self.assertEqual("user_confirmed", preference["evidence_class"])
        self.assertEqual("fictional_intervention", hobby["evidence_class"])
        self.assertEqual("skilled", hobby["proficiency"])
        with self.assertRaises(ValueError):
            self.context(basis="user_preference", proficiency="expert")

    def test_update_retire_and_historical_projection_are_append_only(self):
        row = self.context()
        updated = self.context(
            context_id=row["context_id"], expected_revision=1,
            label="새 리그에 정착한 투수", detail="동료들과 신뢰를 쌓아 정착한 세계선 설정",
            world_origin={"universe_id": self.world, "protagonist_id": self.player, "game_date": self.date},
        )
        self.assertEqual(2, updated["revision"])
        with self.assertRaisesRegex(ValueError, "다른 창"):
            self.context(context_id=row["context_id"], expected_revision=1)
        retired = personal_context.retire_user_context(
            self.state,
            {"context_id": row["context_id"], "expected_revision": 2},
            universe_id=self.world,
            protagonist_id=self.player,
            game_date="2027-07-26",
        )
        self.assertEqual("retired", retired["status"])
        old = personal_context.view(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date="2027-07-24",
        )["items"][0]
        current = personal_context.view(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date="2027-07-26",
        )["items"][0]
        self.assertEqual("새 리그에 정착한 투수", old["label"])
        self.assertEqual("active", old["status"])
        self.assertEqual("retired", current["status"])
        self.assertEqual(3, current["history_count"])

    def test_remote_prompt_includes_only_explicit_public_opt_in(self):
        self.context(label="비밀 배경")
        self.context(basis="user_preference", label="팀 내부 취향", detail="동료만 아는 메뉴", visibility="clubhouse")
        allowed = self.context(
            basis="user_preference", label="공개된 취향", detail="인터뷰에서도 밝힌 독서 취향",
            visibility="public", remote_allowed=True,
        )
        local = personal_context.prompt_section(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, remote=False,
        )
        remote = personal_context.prompt_section(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, remote=True,
        )
        self.assertIn("비밀 배경", local)
        self.assertNotIn("비밀 배경", remote)
        self.assertNotIn("팀 내부 취향", remote)
        self.assertIn(allowed["label"], remote)
        with self.assertRaises(ValueError):
            self.context(remote_allowed=True, visibility="private")

    def test_scene_references_follow_the_scene_visibility_boundary(self):
        private = self.context(label="비공개 햄버거", detail="둘만 아는 메뉴")
        clubhouse = self.context(
            basis="user_preference", label="팀 내부 음악", detail="라커룸에서 듣는 곡",
            visibility="clubhouse",
        )
        public = self.context(
            basis="user_preference", label="공개 독서", detail="인터뷰에서도 말한 책",
            visibility="public",
        )
        private_refs = personal_context.scene_references(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, query="햄버거", audience="private",
        )
        clubhouse_refs = personal_context.scene_references(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, query="음악", audience="clubhouse",
        )
        public_refs = personal_context.scene_references(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, query="햄버거", audience="national",
        )
        self.assertEqual(private["context_id"], private_refs[0]["context_id"])
        self.assertEqual(clubhouse["context_id"], clubhouse_refs[0]["context_id"])
        self.assertEqual(public["context_id"], public_refs[0]["context_id"])
        self.assertNotIn(private["context_id"], {row["context_id"] for row in public_refs})

    def test_referenced_context_revision_and_visibility_are_revalidated(self):
        row = self.context()
        reference = personal_context.scene_references(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, query=row["label"], audience="private",
        )
        personal_context.validate_references(
            self.state, reference, universe_id=self.world, protagonist_id=self.player,
            game_date=self.date, audience="private",
        )
        self.context(
            context_id=row["context_id"], expected_revision=1,
            label="수정된 배경", detail="미리보기 뒤 바뀐 설정",
        )
        with self.assertRaisesRegex(ValueError, "설정이 바뀌었습니다"):
            personal_context.validate_references(
                self.state, reference, universe_id=self.world, protagonist_id=self.player,
                game_date=self.date, audience="private",
            )

    def test_user_cannot_spoof_verified_observation(self):
        with self.assertRaises(ValueError):
            self.context(basis="verified_game_observation")
        with self.assertRaises(ValueError):
            self.context(source_event_ids=["fact:fake"])
        with self.assertRaises(ValueError):
            personal_context.record_verified_observation(
                self.state, universe_id=self.world, protagonist_id=self.player,
                player_label="가상 주인공", game_date=self.date,
                label="확인한 행동", detail="경기 화면에서 확인", source_fact_ids=["fact:missing"],
            )
        fact = nc.new_fact(
            kind="fixture", label="검증 사실", value=1, evidence_class="save_verified",
            game_date=self.date, fact_id="fact:verified",
        )
        self.state["fact_registry"][fact["fact_id"]] = fact
        row = personal_context.record_verified_observation(
            self.state, universe_id=self.world, protagonist_id=self.player,
            player_label="가상 주인공", game_date=self.date,
            label="확인한 행동", detail="경기 화면에서 확인", source_fact_ids=[fact["fact_id"]],
        )
        self.assertEqual("save_verified", row["evidence_class"])
        self.assertEqual([fact["fact_id"]], row["source_event_ids"])

    def test_context_is_strictly_isolated_by_world_and_player(self):
        self.context()
        for world, player in (("world-b", self.player), (self.world, "player-8")):
            view = personal_context.view(
                self.state, universe_id=world, protagonist_id=player,
                game_date=self.date,
            )
            self.assertEqual([], view["items"])
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            self.context(world_origin={"universe_id": "world-b", "protagonist_id": self.player, "game_date": self.date})
        self.assertEqual(before, self.state)

    def test_explicit_counterparts_allow_same_name_but_require_selection(self):
        first = self.counterpart()
        second = self.counterpart(note="동명이인의 다른 후배")
        self.assertNotEqual(first["entity_id"], second["entity_id"])
        projected = world_identity.counterparts(
            self.state, universe_id=self.world, protagonist_id=self.player,
        )
        self.assertTrue(all(row["needs_explicit_selection"] for row in projected))
        with self.assertRaisesRegex(ValueError, "둘 이상"):
            world_identity.prepare_participant(
                self.state, universe_id=self.world, protagonist_id=self.player,
                role="rookie", supplied_name="가상 후배",
            )
        selected = world_identity.prepare_participant(
            self.state, universe_id=self.world, protagonist_id=self.player,
            role="rookie", supplied_name="막내", entity_id=first["entity_id"],
        )
        self.assertEqual(first["entity_id"], selected["participant_key"])

    def test_alias_normalization_rename_history_and_retirement(self):
        row = self.counterpart(canonical_name="Ｓｋｅｎｅｓ")
        self.assertEqual("skenes", world_identity.normalize_alias(" SKENES "))
        updated = world_identity.update_counterpart(
            self.state,
            {"entity_id": row["entity_id"], "expected_revision": 1,
             "canonical_name": "Paul Skenes", "role": "rival", "aliases": "스킨스"},
            universe_id=self.world, protagonist_id=self.player, game_date="2027-07-25",
        )
        self.assertIn("Ｓｋｅｎｅｓ", updated["aliases"])
        self.assertEqual(2, updated["identity_revision"])
        retired = world_identity.retire_counterpart(
            self.state, {"entity_id": row["entity_id"], "expected_revision": 2},
            universe_id=self.world, protagonist_id=self.player, game_date="2027-07-26",
        )
        self.assertEqual("retired", retired["status"])
        self.assertEqual(3, len(retired["identity_history"]))
        with self.assertRaisesRegex(ValueError, "보관된"):
            world_identity.prepare_participant(
                self.state, universe_id=self.world, protagonist_id=self.player,
                role="rival", supplied_name="Paul Skenes", entity_id=row["entity_id"],
            )

    def test_counterpart_role_and_historical_identity_are_explicit(self):
        row = self.counterpart(canonical_name="첫 이름")
        with self.assertRaisesRegex(ValueError, "역할"):
            world_identity.prepare_participant(
                self.state, universe_id=self.world, protagonist_id=self.player,
                role="coach", supplied_name="첫 이름", entity_id=row["entity_id"],
            )
        world_identity.update_counterpart(
            self.state,
            {"entity_id": row["entity_id"], "expected_revision": 1,
             "canonical_name": "바뀐 이름", "role": "rookie", "aliases": "첫 이름"},
            universe_id=self.world, protagonist_id=self.player, game_date="2027-07-25",
        )
        old = world_identity.counterparts(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date="2027-07-24",
        )[0]
        current = world_identity.counterparts(
            self.state, universe_id=self.world, protagonist_id=self.player,
            game_date="2027-07-25",
        )[0]
        self.assertEqual("첫 이름", old["label"])
        self.assertEqual(row["aliases"], old["aliases"])
        self.assertEqual("바뀐 이름", current["label"])
        self.assertIn("첫 이름", current["aliases"])

    def test_legacy_interaction_person_remains_readable_and_reused(self):
        legacy_id = "interaction-person:" + nc.stable_id(
            self.world, self.player, "rookie", "가상 후배",
        )
        self.state["world_entities"][legacy_id] = {
            "entity_id": legacy_id, "kind": "user_named_story_participant",
            "label": "가상 후배", "universe_id": self.world, "aliases": [],
        }
        self.state["star_interactions"]["old"] = {
            "interaction_id": "old", "universe_id": self.world,
            "protagonist_id": self.player, "participant_key": legacy_id,
        }
        selected = world_identity.prepare_participant(
            self.state, universe_id=self.world, protagonist_id=self.player,
            role="rookie", supplied_name="가상 후배",
        )
        self.assertEqual(legacy_id, selected["participant_key"])
        world_identity.ensure_interaction_entity(
            self.state, entity_id=legacy_id, label="가상 후배", role="rookie",
            universe_id=self.world, protagonist_id=self.player, game_date=self.date,
            event_id="event-1", source="user_label",
        )
        enriched = self.state["world_entities"][legacy_id]
        self.assertEqual(self.player, enriched["protagonist_id"])
        self.assertEqual("event-1", enriched["event_links"][0]["event_id"])


class WorldbookServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="starmodefeed-worldbook-")
        self.addCleanup(self.temp.cleanup)
        helper = fixtures.AttachmentServiceTests()
        self.app, self.config, self.snapshot, patches = helper._running(self.temp.name)
        patches.extend([
            patch.object(self.app, "_read_snapshot", side_effect=lambda _path: copy.deepcopy(self.snapshot)),
            patch.object(service_module, "_llm_reachable", return_value=False),
            patch.object(service_module, "_probe_llm_ports", return_value=False),
        ])
        self.patches = fixtures._Patches(patches)
        self.patches.__enter__()
        self.addCleanup(self.patches.__exit__)
        dashboard = self.app.check_save(lambda *_args: None)
        self.world = dashboard["world"]["world_id"]
        self.player = str(self.snapshot["player"]["id"])
        self.date = "2027-07-24"
        self.ledger_path = Path(self.config["data_dir"]) / "worlds" / self.world / "ledger.json"

    def ledger(self):
        return Ledger(str(self.ledger_path), self.world, read_only=True)

    def origin(self):
        return {"universe_id": self.world, "protagonist_id": self.player, "game_date": self.date}

    def test_service_round_trip_dashboard_and_history_capsule(self):
        result = self.app.upsert_personal_context({
            "basis": "user_preference", "label": "햄버거", "detail": "원정 후 함께 먹는 메뉴",
            "visibility": "private", "world_origin": self.origin(),
        })
        context_id = result["item"]["context_id"]
        self.assertEqual(context_id, result["dashboard"]["story"]["personal_context"]["items"][0]["context_id"])
        counterpart = self.app.upsert_counterpart({
            "canonical_name": "가상 후배", "role": "rookie", "aliases": "막내",
            "note": "햄버거 약속 상대", "world_origin": self.origin(),
        })["item"]
        dashboard = self.app.create_story_event({
            "category": "starplayer", "situation": "player_exchange", "target": "rookie",
            "participant_entity_id": counterpart["entity_id"], "participant_name": "가상 후배",
            "interaction_topic": "햄버거", "visibility": "private",
        })["dashboard"]
        interaction = dashboard["story"]["interactions"][0]
        self.assertEqual(counterpart["entity_id"], interaction["participant_key"])
        self.assertIn("원정 후 함께 먹는 메뉴", dashboard["story"]["current"]["turns"][-1]["scene"]["response"])
        capsule = self.app.read_history_capsule(self.date)
        self.assertEqual(context_id, capsule["personal_context"]["items"][0]["context_id"])
        self.assertEqual(counterpart["entity_id"], capsule["counterparts"][0]["entity_id"])
        entity = self.ledger().state["world_entities"][counterpart["entity_id"]]
        self.assertEqual(1, len(entity["event_links"]))

    def test_stale_origin_and_stale_revisions_leave_the_ledger_unchanged(self):
        before = self.ledger_path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.upsert_personal_context({
                "basis": "authored_background", "label": "잘못된 세계", "detail": "저장되면 안 됨",
                "world_origin": {**self.origin(), "universe_id": "foreign"},
            })
        self.assertEqual(before, self.ledger_path.read_bytes())
        row = self.app.upsert_counterpart({
            "canonical_name": "가상 코치", "role": "coach", "world_origin": self.origin(),
        })["item"]
        self.app.upsert_counterpart({
            "entity_id": row["entity_id"], "expected_revision": 1,
            "canonical_name": "가상 코치", "role": "coach", "note": "수정됨",
            "world_origin": self.origin(),
        })
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "다른 창"):
            self.app.retire_counterpart({
                "entity_id": row["entity_id"], "expected_revision": 1,
                "world_origin": self.origin(),
            })
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_context_change_after_interaction_preview_rejects_confirmation(self):
        context = self.app.upsert_personal_context({
            "basis": "user_preference", "label": "햄버거", "detail": "후배와 나누는 메뉴",
            "visibility": "private", "world_origin": self.origin(),
        })["item"]
        interaction_id = self.app.create_story_event({
            "category": "starplayer", "situation": "player_exchange", "target": "rookie",
            "participant_name": "가상 후배", "interaction_topic": "햄버거",
            "visibility": "private",
        })["interaction_id"]
        preview = self.app.chat_story({
            "interaction_id": interaction_id, "user_text": "농담을 해 볼게",
            "renderer_preference": "deterministic",
        })
        proposal = preview["turn"]["proposed_events"][0]
        self.app.upsert_personal_context({
            "context_id": context["context_id"], "expected_revision": 1,
            "basis": "user_preference", "label": "바뀐 메뉴", "detail": "미리보기 뒤 수정됨",
            "visibility": "private", "world_origin": self.origin(),
        })
        before = self.ledger_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "설정이 바뀌었습니다"):
            self.app.chat_story({
                "confirm_turn_id": preview["turn"]["turn_id"],
                "confirm_proposal_id": proposal["proposal_id"],
            })
        self.assertEqual(before, self.ledger_path.read_bytes())

    def test_same_named_explicit_people_never_merge_relationship_edges(self):
        people = [
            self.app.upsert_counterpart({
                "canonical_name": "김민수", "role": "rookie", "note": f"동명이인 {index}",
                "world_origin": self.origin(),
            })["item"]
            for index in range(2)
        ]
        ids = []
        for person in people:
            result = self.app.create_story_event({
                "category": "starplayer", "situation": "player_exchange", "target": "rookie",
                "participant_entity_id": person["entity_id"], "participant_name": "김민수",
                "interaction_topic": person["note"], "visibility": "private",
            })
            ids.append(result["interaction_id"])
        state = self.ledger().state
        keys = [state["star_interactions"][ident]["participant_key"] for ident in ids]
        self.assertEqual({row["entity_id"] for row in people}, set(keys))
        self.assertEqual(2, len({state["star_interactions"][ident]["thread_id"] for ident in ids}))

    def test_retired_context_is_preserved_but_not_used_for_new_scenes(self):
        created = self.app.upsert_personal_context({
            "basis": "user_preference", "label": "은퇴한 소재", "detail": "다시 자동 사용하지 않음",
            "visibility": "private", "world_origin": self.origin(),
        })["item"]
        self.app.retire_personal_context({
            "context_id": created["context_id"], "expected_revision": 1,
            "world_origin": self.origin(),
        })
        result = self.app.create_story_event({
            "category": "starplayer", "situation": "outing_walk", "target": "self",
            "interaction_topic": "은퇴한 소재", "visibility": "private",
        })
        self.assertNotIn("다시 자동 사용하지 않음", result["item"]["scene"]["response"])
        items = result["dashboard"]["story"]["personal_context"]["items"]
        self.assertEqual("retired", next(row for row in items if row["context_id"] == created["context_id"])["status"])


if __name__ == "__main__":
    unittest.main()
