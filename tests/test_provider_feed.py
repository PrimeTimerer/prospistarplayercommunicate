#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import copy
import unittest

import provider_feed


def event():
    return {
        "snapshot": {
            "content_hash": "source-hash",
            "player": {"id": 36, "name": "Paul Skenes", "team": "Yokohama"},
            "date": {"year": 2027, "month": 7, "day": 25},
        }
    }


def feed():
    return {
        "media": [
            {
                "id": "article-a",
                "outlet": "전국 기록 데스크",
                "title": "Paul Skenes, 시즌 흐름의 중심에 서다",
                "sub": "확인된 기록으로 현재 위치를 읽는다",
                "body": ["Paul Skenes의 누적 성적이 다시 화제다.", "다음 경기의 관전점도 현재 기록에서 출발한다."],
                "blocks": [{"text": "Paul Skenes의 누적 성적이 다시 화제다."}, {"text": "다음 경기의 관전점도 현재 기록에서 출발한다."}],
            }
        ],
        "boards": [
            {
                "id": "board-a",
                "board": "구단 팬 포럼",
                "code": "club-pulse",
                "title": "Paul Skenes의 다음 경기를 어떻게 볼 것인가",
                "comments": [
                    {"post_id": "comment-a", "author": "기록노트", "text": "누적 흐름부터 봐야 한다.", "up": 7},
                    {"post_id": "comment-b", "author": "원정석", "text": "다음 장면은 차분히 기다리자.", "up": 3},
                ],
                "posts": [
                    {"post_id": "comment-a", "author": "기록노트", "text": "누적 흐름부터 봐야 한다."},
                    {"post_id": "comment-b", "author": "원정석", "text": "다음 장면은 차분히 기다리자."},
                ],
            }
        ],
        "editorial": {
            "binding": {"universe_id": "world-a", "protagonist_id": "36", "game_date": "2027-07-25"},
            "facts": [
                {"fact_id": "fact-a", "kind": "season", "text": "시즌 110홈런", "evidence_class": "save_verified", "values": {"bat_HR": 110}}
            ],
        },
    }


def spotlight(tier="global", mode="standard"):
    return {
        "tier": tier,
        "label": "세계적 현상" if tier == "global" else "개인 세계선",
        "drivers": [{"label": "시즌 110홈런"}],
        "memory_anchors": ["월간 MVP 수상 이력"],
        "reaction_budget": {"mode": mode, "expression_heat": 8},
    }


def valid_response(canonical):
    value = provider_feed.expression_contract(canonical)
    article = value["articles"][0]
    article["title"] = "Paul Skenes를 둘러싼 기록의 무게"
    article["sub"] = "기록과 다음 승부를 함께 읽는 전국 분석"
    article["body"] = [
        "Paul Skenes의 공개된 누적 성적은 오늘 논의의 출발점이 됐다.",
        "분석가들은 단정 대신 다음 경기에서 이어질 흐름을 차분히 살폈다.",
    ]
    board = value["boards"][0]
    board["title"] = "Paul Skenes를 두고 엇갈린 팬들의 시선"
    board["comments"][0]["text"] = "지금까지 쌓인 기록의 맥락을 먼저 보자는 의견이다."
    board["comments"][1]["text"] = "기대는 크지만 다음 승부의 과정도 함께 지켜보고 싶다."
    syllables = [chr(0xAC00 + index) for index in range(40)]
    for index, post in enumerate(value["social"]):
        post["text"] = f"Paul Skenes의 기록을 바라보는 {syllables[index]} 관점이 {canonical['social'][index]['platform']}에서 이어졌다."
        for ordinal, reply in enumerate(post["replies"]):
            token = syllables[10 + index * 4 + ordinal]
            reply["text"] = f"{token} 관점에서는 성급한 결론보다 공개된 흐름을 오래 지켜보자는 반응이 나왔다."
    return json.dumps(value, ensure_ascii=False)


class ProviderFeedTests(unittest.TestCase):
    def test_global_canonical_bundle_adds_six_text_social_surfaces(self):
        value = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        self.assertEqual(6, len(value["social"]))
        self.assertEqual(24, sum(len(row["replies"]) for row in value["social"]))
        self.assertEqual(24, len({reply["text"] for row in value["social"] for reply in row["replies"]}))
        self.assertNotIn("이(가)", " ".join(row["text"] for row in value["social"]))
        self.assertEqual("deterministic", value["reaction_bundle"]["renderer"])
        self.assertEqual("Paul Skenes", value["reaction_bundle"]["focus"]["protagonist_name"])

    def test_private_tier_does_not_pad_a_rookie_day_with_public_social_posts(self):
        value = provider_feed.prepare_canonical(feed(), event(), spotlight("private"), universe_id="world-a")
        self.assertEqual([], value["social"])

    def test_social_heat_changes_wording_without_changing_structure_or_ids(self):
        low_spotlight = spotlight()
        low_spotlight["reaction_budget"]["expression_heat"] = 1
        high_spotlight = copy.deepcopy(low_spotlight)
        high_spotlight["reaction_budget"]["expression_heat"] = 10
        low = provider_feed.prepare_canonical(feed(), event(), low_spotlight, universe_id="world-a")
        high = provider_feed.prepare_canonical(feed(), event(), high_spotlight, universe_id="world-a")
        self.assertEqual(provider_feed.bundle_counts(low), provider_feed.bundle_counts(high))
        self.assertEqual(
            [row["id"] for row in low["social"]],
            [row["id"] for row in high["social"]],
        )
        self.assertNotEqual(
            [row["text"] for row in low["social"]],
            [row["text"] for row in high["social"]],
        )
        self.assertTrue(all(row["expression_heat_band"] == "mild" for row in low["social"]))
        self.assertTrue(all(row["expression_heat_band"] == "hot" for row in high["social"]))

    def test_explicit_scene_may_request_a_minimum_social_surface_without_inflating_replies(self):
        scene_spotlight = spotlight("private", "quick")
        scene_spotlight["reaction_budget"]["minimum_social_posts"] = 1
        value = provider_feed.prepare_canonical(feed(), event(), scene_spotlight, universe_id="world-a")
        self.assertEqual(1, len(value["social"]))
        self.assertEqual(2, len(value["social"][0]["replies"]))

    def test_provider_may_change_only_expression_fields(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        packet = json.loads(user)
        self.assertEqual("전국 기록 데스크", packet["locked_surface_plan"]["articles"][0]["outlet"])
        self.assertEqual("X 타임라인", packet["locked_surface_plan"]["social"][0]["platform"])
        self.assertIn("style_profile", packet["locked_surface_plan"]["boards"][0])
        self.assertIn("style_profile", packet["locked_surface_plan"]["social"][0])
        self.assertEqual(
            "derived_features_only",
            packet["style_controls"]["community_style_database"]["storage_mode"],
        )
        value = provider_feed.apply_expression(
            canonical,
            valid_response(canonical),
            provider="local_llm",
            model="fixture-model",
            allowed_number_source=allowed,
        )
        self.assertEqual("local_llm_expression", value["reaction_bundle"]["renderer"])
        self.assertEqual("article-a", value["media"][0]["id"])
        self.assertEqual(7, value["boards"][0]["comments"][0]["up"])
        self.assertEqual(value["boards"][0]["comments"][0]["text"], value["boards"][0]["posts"][0]["text"])
        self.assertEqual(provider_feed.bundle_counts(canonical), provider_feed.bundle_counts(value))

    def test_language_ceiling_rejects_low_level_profanity_but_preserves_level_five(self):
        low = provider_feed.prepare_canonical(
            feed(), event(), spotlight(), universe_id="world-a", language_level=1
        )
        low_response = json.loads(valid_response(low))
        low_response["boards"][0]["comments"][0]["text"] = "씨발 지금까지 쌓인 기록의 맥락부터 먼저 보자는 의견이다."
        _system, _user, low_allowed = provider_feed.build_prompt(low, spotlight=spotlight())
        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "언어 수위"):
            provider_feed.apply_expression(
                low,
                json.dumps(low_response, ensure_ascii=False),
                provider="local_llm",
                model="fixture",
                allowed_number_source=low_allowed,
            )

        high = provider_feed.prepare_canonical(
            feed(), event(), spotlight(), universe_id="world-a", language_level=5
        )
        high_response = json.loads(valid_response(high))
        high_response["boards"][0]["comments"][0]["text"] = "씨발 지금까지 쌓인 기록의 맥락부터 먼저 보자는 의견이다."
        _system, _user, high_allowed = provider_feed.build_prompt(high, spotlight=spotlight())
        rendered = provider_feed.apply_expression(
            high,
            json.dumps(high_response, ensure_ascii=False),
            provider="local_llm",
            model="fixture",
            allowed_number_source=high_allowed,
        )
        self.assertEqual(5, rendered["reaction_bundle"]["community_language_level"])
        self.assertIn("씨발", rendered["boards"][0]["comments"][0]["text"])

    def test_sequential_batch_plan_is_stable_ordered_and_surface_local(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batches = provider_feed.expression_batches(
            canonical,
            max_text_fields=4,
            max_source_chars=2000,
        )
        self.assertEqual(
            batches,
            provider_feed.expression_batches(
                canonical,
                max_text_fields=4,
                max_source_chars=2000,
            ),
        )
        self.assertEqual(
            list(range(1, len(batches) + 1)),
            [row["ordinal"] for row in batches],
        )
        self.assertTrue(all(row["total"] == len(batches) for row in batches))
        self.assertEqual(
            sorted([row["kind"] for row in batches], key=("articles", "boards", "social").index),
            [row["kind"] for row in batches],
        )
        contract = provider_feed.expression_contract(canonical)
        for kind in ("articles", "boards", "social"):
            self.assertEqual(
                [row["id"] for row in contract[kind]],
                [item_id for row in batches if row["kind"] == kind for item_id in row["item_ids"]],
            )
        for batch in batches:
            partial = provider_feed.expression_contract(
                provider_feed.expression_batch_feed(canonical, batch)
            )
            self.assertEqual(batch["item_ids"], [row["id"] for row in partial[batch["kind"]]])
            self.assertEqual(
                [batch["kind"]],
                [key for key in ("articles", "boards", "social") if partial[key]],
            )

    def test_scoped_batch_plans_contain_only_the_requested_surfaces(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        article_batches = provider_feed.scoped_expression_batches(canonical, "articles")
        community_batches = provider_feed.scoped_expression_batches(canonical, "community")

        self.assertTrue(article_batches)
        self.assertTrue(community_batches)
        self.assertEqual({"articles"}, {row["kind"] for row in article_batches})
        self.assertEqual({"boards", "social"}, {row["kind"] for row in community_batches})
        for scope, batches in (("articles", article_batches), ("community", community_batches)):
            self.assertEqual(list(range(1, len(batches) + 1)), [row["ordinal"] for row in batches])
            self.assertTrue(all(row["total"] == len(batches) for row in batches))
            self.assertTrue(all(row["surface_scope"] == scope for row in batches))
            self.assertTrue(all(provider_feed.batch_token_budget(row, "explosion") <= 5200 for row in batches))

    def test_scoped_merges_preserve_the_other_saved_surface_and_source(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        batches = provider_feed.expression_batches(canonical)
        gemini = provider_feed.finalize_batched_expression(
            canonical,
            json.loads(valid_response(canonical)),
            provider="gemini",
            model="gemini-fixture",
            batches=batches,
            allowed_number_source=allowed,
        )

        articles_only = provider_feed.merge_scoped_expression(canonical, canonical, gemini, "articles")
        self.assertEqual(gemini["media"], articles_only["media"])
        self.assertEqual(canonical["boards"], articles_only["boards"])
        self.assertEqual(canonical["social"], articles_only["social"])
        self.assertEqual("mixed_expression", articles_only["reaction_bundle"]["renderer"])
        self.assertEqual("gemini", articles_only["reaction_bundle"]["surface_sources"]["articles"]["provider"])
        self.assertEqual("builtin", articles_only["reaction_bundle"]["surface_sources"]["community"]["provider"])

        community_only = provider_feed.merge_scoped_expression(canonical, articles_only, gemini, "community")
        self.assertEqual(articles_only["media"], community_only["media"])
        self.assertEqual(gemini["boards"], community_only["boards"])
        self.assertEqual(gemini["social"], community_only["social"])
        self.assertEqual("gemini_expression", community_only["reaction_bundle"]["renderer"])
        self.assertEqual("gemini", community_only["reaction_bundle"]["provider"])
        self.assertEqual("community", community_only["reaction_bundle"]["last_surface_scope"])

    def test_scoped_merge_rejects_a_changed_frozen_identifier(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        changed = copy.deepcopy(canonical)
        changed["media"][0]["id"] = "different-article"
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.merge_scoped_expression(canonical, canonical, changed, "articles")

    def test_batch_prompt_has_explicit_limits_and_compact_retry_contract(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batch = provider_feed.expression_batches(canonical)[0]
        normal_system, normal_user, _allowed = provider_feed.build_batch_prompt(
            canonical,
            batch,
            spotlight=spotlight(),
        )
        compact_system, compact_user, _compact_allowed = provider_feed.build_batch_prompt(
            canonical,
            batch,
            spotlight=spotlight(),
            compact=True,
        )
        normal = json.loads(normal_user)
        compact = json.loads(compact_user)
        self.assertTrue(normal["sequential_batch"]["complete_json_required"])
        self.assertFalse(normal["sequential_batch"]["compact_retry"])
        self.assertTrue(compact["sequential_batch"]["compact_retry"])
        self.assertLess(
            compact["sequential_batch"]["visible_text_limits"]["paragraph_chars"],
            normal["sequential_batch"]["visible_text_limits"]["paragraph_chars"],
        )
        self.assertIn("한 묶음", normal_system)
        self.assertIn("더 짧게", compact_system)
        self.assertLessEqual(provider_feed.batch_token_budget(batch, "explosion"), 7000)
        self.assertLess(
            provider_feed.batch_token_budget(batch, "standard", compact=True),
            provider_feed.batch_token_budget(batch, "standard"),
        )

    def test_validated_batches_merge_into_one_full_contract(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batches = provider_feed.expression_batches(canonical)
        aggregate = provider_feed.expression_contract(canonical)
        allowed_sources = []
        for batch in batches:
            partial_feed = provider_feed.expression_batch_feed(canonical, batch)
            _system, _user, allowed = provider_feed.build_batch_prompt(
                canonical,
                batch,
                spotlight=spotlight(),
            )
            validated = provider_feed.apply_expression(
                partial_feed,
                json.dumps(provider_feed.expression_contract(partial_feed), ensure_ascii=False),
                provider="gemini",
                model="fixture",
                allowed_number_source=allowed,
            )
            aggregate = provider_feed.merge_batch_contract(
                aggregate,
                provider_feed.expression_contract(validated),
                batch,
            )
            allowed_sources.append(allowed)
        result = provider_feed.finalize_batched_expression(
            canonical,
            aggregate,
            provider="gemini",
            model="fixture",
            batches=batches,
            allowed_number_source="\n".join(allowed_sources),
        )
        self.assertEqual(provider_feed.expression_contract(canonical), provider_feed.expression_contract(result))
        self.assertEqual("gemini_expression", result["reaction_bundle"]["renderer"])
        self.assertEqual("sequential_batches", result["reaction_bundle"]["generation_mode"])
        self.assertEqual(len(batches), result["reaction_bundle"]["batch_count"])

    def test_batch_merge_rejects_any_id_outside_the_frozen_plan(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        aggregate = provider_feed.expression_contract(canonical)
        batch = provider_feed.expression_batches(canonical)[0]
        partial = provider_feed.expression_contract(provider_feed.expression_batch_feed(canonical, batch))
        partial[batch["kind"]][0]["id"] = "unplanned-id"
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.merge_batch_contract(aggregate, partial, batch)

    def test_provider_cannot_flatten_known_community_registers_into_one_polished_voice(self):
        source = feed()
        source["boards"] = [
            {
                "id": "board-dc", "board": "DCInside", "code": "dc",
                "title": "Paul Skenes 오늘 기록 뭐냐 ㅋㅋ",
                "comments": [
                    {"post_id": "dc-a", "author": "ㅇㅇ(118.***)", "text": "Paul Skenes 오늘 진짜 미쳤네 ㅋㅋ", "up": 12},
                    {"post_id": "dc-b", "author": "직관만감", "text": "이건 타팀팬도 인정해야 됨 ㅇㅇ", "up": 8},
                ],
            },
            {
                "id": "board-fmk", "board": "FMKorea", "code": "fmk",
                "title": "[기록] 오늘자 Paul Skenes 근황",
                "comments": [
                    {"post_id": "fmk-a", "author": "포텐대기중", "text": "타팀팬인데 이건 포텐 가도 인정", "up": 33},
                    {"post_id": "fmk-b", "author": "움짤어디감", "text": "하이라이트 전체 영상도 보고 싶다", "up": 14},
                ],
            },
            {
                "id": "board-mlb", "board": "MLBPARK", "code": "mlb",
                "title": "Paul Skenes의 현재 기록을 어떻게 평가하시나요",
                "comments": [
                    {"post_id": "mlb-a", "author": "표본먼저", "text": "현재 성과는 인상적입니다. 다만 역할과 표본을 함께 놓고 비교해야 한다고 봅니다.", "up": 9},
                    {"post_id": "mlb-b", "author": "다음등판", "text": "다음 경기에서 상대의 대응이 어떻게 달라지는지도 확인해 보고 싶습니다.", "up": 5},
                ],
            },
        ]
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        self.assertEqual([], provider_feed.platform_register_violations(canonical))
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        flattened = provider_feed.expression_contract(canonical)
        flattened["boards"][0]["title"] = "Paul Skenes의 기록을 차분히 살펴보는 토론"
        flattened["boards"][0]["comments"][0]["text"] = "현재까지 공개된 기록의 의미를 침착하게 살펴보고 있습니다."
        flattened["boards"][0]["comments"][1]["text"] = "향후 경기의 변화도 함께 확인하면 좋겠습니다."
        flattened["boards"][1]["title"] = "Paul Skenes를 둘러싼 기록의 의미"
        flattened["boards"][1]["comments"][0]["text"] = "선수의 현재 성과를 여러 관점에서 검토할 필요가 있습니다."
        flattened["boards"][1]["comments"][1]["text"] = "다음 경기까지 지켜본 뒤 평가해도 늦지 않습니다."
        with self.assertRaisesRegex(provider_feed.ProviderFeedError, "서로 다른 말투"):
            provider_feed.apply_expression(
                canonical,
                json.dumps(flattened, ensure_ascii=False),
                provider="gemini",
                model="fixture",
                allowed_number_source=allowed,
            )

    def test_changed_ids_or_unsupported_numbers_are_rejected(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        changed = json.loads(valid_response(canonical))
        changed["boards"][0]["id"] = "different"
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical, json.dumps(changed, ensure_ascii=False), provider="gemini", model="fixture", allowed_number_source=allowed
            )
        unsupported = json.loads(valid_response(canonical))
        unsupported["boards"][0]["comments"][0]["text"] = "검증 자료에 없는 9999승을 달성했다는 반응이다."
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical, json.dumps(unsupported, ensure_ascii=False), provider="gemini", model="fixture", allowed_number_source=allowed
            )

    def test_structural_id_digits_do_not_license_a_baseball_claim(self):
        source = feed()
        source["media"][0]["id"] = "article-987654"
        canonical = provider_feed.prepare_canonical(source, event(), spotlight(), universe_id="world-a")
        _system, _user, allowed = provider_feed.build_prompt(canonical, spotlight=spotlight())
        generated = json.loads(valid_response(canonical))
        generated["boards"][0]["comments"][0]["text"] = "구조 식별자에만 있던 987654승을 거뒀다는 주장이다."
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical,
                json.dumps(generated, ensure_ascii=False),
                provider="local_llm",
                model="fixture",
                allowed_number_source=allowed,
            )

    def test_non_object_rows_are_rejected_as_provider_errors(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        generated = provider_feed.expression_contract(canonical)
        generated["articles"][0] = generated["articles"][0]["id"]
        with self.assertRaises(provider_feed.ProviderFeedError):
            provider_feed.apply_expression(
                canonical,
                json.dumps(generated, ensure_ascii=False),
                provider="local_llm",
                model="fixture",
            )

    def test_fallback_preserves_every_canonical_expression(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        source = provider_feed.expression_contract(canonical)
        value = provider_feed.mark_fallback(
            canonical,
            [{"provider": "gemini", "status": "failed", "code": "REMOTE_UNAVAILABLE"}],
            "내장 피드 유지",
        )
        self.assertEqual(source, provider_feed.expression_contract(value))
        self.assertEqual("deterministic", value["reaction_bundle"]["renderer"])
        self.assertEqual("REMOTE_UNAVAILABLE", value["reaction_bundle"]["fallback_chain"][0]["code"])


if __name__ == "__main__":
    unittest.main()
