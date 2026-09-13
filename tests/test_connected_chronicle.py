"""Cross-channel provenance and incremental period recovery without inference."""
import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import chronicle as c
import chronicle_service
import gemini_provider
import narrate
import service
import story_desk as desk
import story_links as links
import test_story_service as fixtures


def choice(**kw):
    return {"kind": "day", "key": "2027-07-24", "provider": "local", "text": "", "request_id": "period-request-0001", "max_calls": 3, "remote_allowed": False, **kw}


class ChroniclePlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = {"world_id": "world-one", "player_id": "7", "game_date": "2027-07-31", "revision": 0}
        self.state = {"world_id": "world-one", "day_snapshots": {}, "story_sessions": {}}
        self.rows = [links.source("article-1", "2027-07-24", "article", "기사", "햄버거 사건의 첫 기사"),
                     links.source("board-1", "2027-07-24", "community", "논쟁", "기사에 팬이 답했다")]

    def complete(self, kind="day", key="2027-07-24", text="첫날 종합\n\n## 이어갈 핵심\n팬과 기사 사이의 약속"):
        sel = choice(kind=kind, key=key)
        planned = c.plan(self.state, self.context, self.rows, sel)
        previous = planned["previous"]
        for batch in planned["batches"]:
            previous = c.append_checkpoint(self.state, self.context, sel, batch, text, "local", previous)
        return previous

    def test_bounds_validate_and_clamp_current_period(self):
        self.assertEqual(("2027-07-01", "2027-07-24"), c.bounds("month", "2027-07", "2027-07-24"))
        self.assertEqual(("2028-02-01", "2028-02-29"), c.bounds("month", "2028-02", "2028-03-01"))
        for kind, key in (("day", "2027-02-30"), ("month", "2027-13"), ("year", "0"), ("year", "2028")):
            with self.assertRaises(ValueError): c.bounds(kind, key, "2027-07-31")

    def test_unchanged_zero_batches_and_added_only(self):
        first = self.complete()
        self.assertFalse(c.plan(self.state, self.context, self.rows, choice())["batches"])
        self.rows.append(links.source("story-2", "2027-07-24", "story", "후속", "동료가 그 햄버거를 기억했다"))
        planned = c.plan(self.state, self.context, self.rows, choice())
        self.assertEqual(["story-2#p0"], [r["id"] for r in planned["changes"]])
        _, prompt = c.summary_prompt(self.context, choice(), planned["batches"][0], first)
        packet = json.loads(prompt)
        self.assertEqual("팬과 기사 사이의 약속", packet["prior_continuity"])
        self.assertNotIn("햄버거 사건의 첫 기사", prompt)

    def test_changed_and_removed_inputs_are_explicit_corrections(self):
        self.complete()
        self.rows[0] = links.source("article-1", "2027-07-24", "article", "기사", "기존 보도를 정정했다")
        self.rows.pop()
        plan = c.plan(self.state, self.context, self.rows, choice())
        self.assertEqual({"revised", "removed"}, {r["change"] for r in plan["changes"]})
        for batch in plan["batches"]:
            c.append_checkpoint(self.state, self.context, choice(), batch, "정정 종합", "local", c.latest(self.state, self.context, "day", "2027-07-24", "local"))
        self.assertFalse(c.plan(self.state, self.context, self.rows, choice())["changes"])

    def test_chunk_prefix_checkpoint_can_resume_and_prompt_is_bounded(self):
        self.rows = [links.source("long", "2027-07-24", "story", "긴 글", "장문입니다. " * 15000)]
        planned = c.plan(self.state, self.context, self.rows, choice())
        self.assertGreater(len(planned["batches"]), 3)
        first = planned["batches"][0]
        c.append_checkpoint(self.state, self.context, choice(), first, "첫 묶음", "local", None)
        resumed = c.plan(self.state, self.context, self.rows, choice())
        self.assertFalse({r["id"] for r in first} & {r["id"] for r in resumed["changes"]})
        for batch in resumed["batches"]:
            _, prompt = c.summary_prompt(self.context, choice(), batch, resumed["previous"])
            self.assertLess(len(prompt), 17500)
        self.assertEqual(105000, len(self.rows[0]["text"]))

    def test_cloud_projection_and_local_editions_are_separate(self):
        self.rows.append(links.source("private", "2027-07-24", "story", "비밀", "Google 금지", visibility="private", remote=False))
        self.complete()
        remote = c.plan(self.state, self.context, self.rows, choice(provider="gemini"))
        self.assertIsNone(remote["previous"])
        self.assertNotIn("Google 금지", json.dumps(remote, ensure_ascii=False))
        self.assertEqual(2, len(remote["changes"]))

    def test_appended_suffix_preserves_complete_chunk_prefix(self):
        self.rows = [links.source("long", "2027-07-24", "story", "긴 글", "가" * 5600)]
        self.complete()
        self.rows = [links.source("long", "2027-07-24", "story", "긴 글", "가" * 5600 + "새 내용")]
        self.assertEqual(["long#p2"], [r["id"] for r in c.plan(self.state, self.context, self.rows, choice())["changes"]])

    def test_missing_monthly_summary_keeps_multiple_days_and_directions(self):
        rows = [links.source("day"+str(i), f"2027-07-{i:02}", "story", f"다른 사건 {i}", f"날짜별 사건 {i}") for i in range(1, 6)]
        selection = choice(text="약속을 중심으로 정리")
        c.add_direction(self.state, self.context, selection)
        month = c.period_sources(self.state, self.context, rows, "month", "2027-07", "local")
        self.assertIn("약속을 중심으로 정리", json.dumps(month, ensure_ascii=False))
        year = c.period_sources(self.state, self.context, rows, "year", "2027", "local")
        self.assertGreaterEqual(json.dumps(year, ensure_ascii=False).count("날짜별 사건"), 4)
        self.assertEqual(("9999-12-01", "9999-12-31"), c.bounds("month", "9999-12", "9999-12-31"))

    def test_legacy_reactions_and_chat_stay_scoped_and_private(self):
        state = copy.deepcopy(self.state)
        state["conversation_turns"] = [{"turn_id":"old", "universe_id":"world-one", "protagonist_id":"7", "game_date":"2027-07-24", "user_text":"비공개 대화", "reply_text":"아직 제안", "proposed_events":[{"text":"미확정 사건"}]}]
        state["story_sessions"] = {"2027-07-24":{"turns":[{"scene":{"response":"혼자 저녁"}, "reactions":{"media":[{"title":"공개 시즌 기사", "body":"공개 성적"}]}}]}}
        rows = links.collect(state, self.context)
        self.assertIn("공개 성적", json.dumps(rows, ensure_ascii=False))
        self.assertNotIn("미확정 사건", json.dumps(rows, ensure_ascii=False))
        self.assertNotIn("비공개 대화", json.dumps([r for r in rows if links.permitted(r, remote=True)], ensure_ascii=False))
        self.assertNotIn("비공개 대화", json.dumps([r for r in rows if links.permitted(r, audience="public")], ensure_ascii=False))

    def test_month_prefers_current_daily_continuity_not_original_transcript(self):
        self.complete(text="긴 종합 원문\n\n## 이어갈 핵심\nDAILY_CONTINUITY 핵심 회수")
        month = c.period_sources(self.state, self.context, self.rows, "month", "2027-07", "local")
        self.assertEqual("saved_current_summary", month[0]["basis"])
        self.assertIn("DAILY_CONTINUITY", month[0]["text"])
        self.assertNotIn("햄버거 사건의 첫 기사", month[0]["text"])
        self.rows.append(links.source("new", "2027-07-24", "article", "새 기사", "갱신할 기사", importance=200))
        revised = c.period_sources(self.state, self.context, self.rows, "month", "2027-07", "local")
        self.assertEqual("local_extractive_highlights", revised[0]["basis"])
        self.assertNotIn("DAILY_CONTINUITY", revised[0]["text"])
        self.assertIn("갱신할 기사", revised[0]["text"])

    def test_year_prefers_current_month_and_unchanged_year_reuses_coverage(self):
        self.complete("month", "2027-07", "월간 원문\n\n## 이어갈 핵심\n월간 갈등이 해소됨")
        yearly = c.plan(self.state, self.context, self.rows, choice(kind="year", key="2027"))
        self.assertIn("월간 갈등이 해소됨", yearly["sources"][0]["text"])
        self.assertEqual("saved_current_summary", yearly["sources"][0]["basis"])
        self.complete("year", "2027", "연간 핵심")
        self.assertFalse(c.plan(self.state, self.context, self.rows, choice(kind="year", key="2027"))["batches"])

    def test_month_year_are_synthesis_only_and_bad_budgets_rejected(self):
        for payload in ({"kind":"month", "key":"2027-07", "text":"새 사건"}, {"max_calls":0}, {"max_calls":7}, {"provider":"note"}):
            with self.assertRaises(ValueError): c.normalize({**choice(), **payload}, self.context["game_date"])

    def test_stat_delta_uses_outs_and_never_sums_season_cumulatives(self):
        for day, innings, hits in (("2027-06-30", 5.2, 30), ("2027-07-24", 6.1, 35)):
            self.state["day_snapshots"][day] = {"verified_snapshot":{"player":{"id":7}, "stats":{"pit_IP":innings, "bat_H":hits}}}
        data = json.loads(c.record_change(self.state, self.context, "2027-07-01", "2027-07-31")["text"])
        self.assertEqual({"pit_IP":0.2, "bat_H":5}, data["observed_delta"])
        self.assertTrue(data["period_start_covered"])
        del self.state["day_snapshots"]["2027-06-30"]
        data = json.loads(c.record_change(self.state, self.context, "2027-07-01", "2027-07-31")["text"])
        self.assertFalse(data["period_start_covered"])

    def test_directives_deduplicate_and_do_not_become_article_sources(self):
        c.add_direction(self.state, self.context, choice(text="농담을 중심으로 정리"))
        c.add_direction(self.state, self.context, choice(text="농담을 중심으로 정리"))
        self.assertEqual(1, len(self.state[c.STORE]["directions"]))
        self.assertTrue(any(r["basis"] == "author_direction" for r in c.plan(self.state, self.context, self.rows, choice())["sources"]))
        self.assertFalse(any("농담을 중심" in r["text"] for r in links.collect(self.state, self.context)))

    def test_counterpart_revisions_validate_and_preserve_old_dates(self):
        c.save_context(self.state, {"previous_team":"한신", "people":[{"name":"테스트 OB", "team":"한신", "role":"ob"}]}, {**self.context,"game_date":"2027-07-24"})
        c.save_context(self.state, {"upcoming_team":"요미우리"}, self.context)
        self.assertEqual("한신", c.current_context(self.state, {**self.context, "game_date":"2027-07-24"})["previous_team"])
        self.assertEqual({}, links.named_voices(c.current_context(self.state, self.context), remote=True))
        with self.assertRaises(ValueError): c.save_context(self.state, {"people":[{"name":"a", "team":"b", "role":"ob", "source_url":"file:///secret"}]}, self.context)

    def test_public_selection_cannot_cross_private_or_cloud_consent(self):
        rows = self.rows + [links.source("secret", "2027-07-24", "story", "비공개", "비공개 장면", visibility="private", remote=False)]
        selection = {"provider":"local", "channel":"article", "text":"기사", "source_ids":["secret"]}
        with self.assertRaises(ValueError): links.linked_context(rows, selection, "2027-07-24")
        selection.update(channel="story", provider="gemini")
        with self.assertRaises(ValueError): links.linked_context(rows, selection, "2027-07-24")
        selection.update(provider="local")
        self.assertEqual("secret", links.linked_context(rows, selection, "2027-07-24")[0]["id"])


class ConnectedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.snap, self.ledger, self.event, self.config, self.world = fixtures.StoryServiceTests()._fixture(self.temp.name)
        self.config.update(gemini_consent=True, gemini_model="mock-model")
        self.app = service.StarModeService()
        self.app._prepare = lambda update=None: (self.config, {}, None, self.ledger, self.world, self.event)
        self.app._secret_store = Mock(return_value=Mock(get_gemini_key=Mock(return_value=("test-key", "fixture"))))
        self.app._record_gemini_generation = Mock(); self.app._record_gemini_failure = Mock()
        self.update = lambda message, progress=None, phase=None: None
        patcher = patch.object(service, "_llm_reachable", return_value=False); patcher.start(); self.addCleanup(patcher.stop)

    def payload(self, **kw):
        return {**choice(), "chronicle_origin": c.origin(self.ledger.state, self.snap, self.world), **kw}

    def turn(self, text, **kw):
        return self.app.write_director_turn(self.update, {"text":text, "request_id":"channel-" + desk.digest([text,kw])[:16],
            "desk_origin":desk.origin(self.ledger.state, self.snap, self.world), **kw})

    def test_article_community_story_link_without_replacing_feed_or_stats(self):
        initial_stats = copy.deepcopy(self.snap["stats"])
        article = self.turn("햄버거 기사를 쓰겠다", channel="article", visibility="public", allow_remote_recall=True)
        row = article["editorial_desk"]["article"]["turns"][0]
        with patch.object(narrate, "director_chat", return_value=("그 기사에 팬들이 답했다.", "local")) as model:
            community = self.turn("그 기사에 농담으로 답해줘", channel="community", visibility="public", provider="local", source_ids=["desk:" + row["id"]])
        self.assertIn("햄버거 기사를 쓰겠다", model.call_args.args[1])
        self.assertEqual(1, community["editorial_desk"]["community"]["total"])
        self.assertEqual(0, community["story_desk"]["total"])
        with patch.object(narrate, "director_chat", return_value=("선수가 그 반응을 보고 웃는다.", "local")) as model:
            self.turn("팬들의 반응 이후 장면", provider="local")
        self.assertIn("그 기사에 팬들이 답했다", model.call_args.args[1])
        self.assertEqual(initial_stats, self.snap["stats"])
        self.assertEqual(1, self.app.read_story_desk(channel="article")["total"])
        self.assertEqual(3, len(self.ledger.history_capsule("2027-07-24", self.snap)["story_desk"]["turns"]))

    def test_public_channel_filters_private_memory_and_scene(self):
        self.turn("숨겨진 햄버거 비밀", visibility="private", remember_input=True)
        with patch.object(narrate, "director_chat", return_value=("공개 자료만 기사로 쓴다.", "local")) as model:
            self.turn("오늘 인터뷰 기사", channel="article", visibility="public", provider="local")
        self.assertNotIn("숨겨진 햄버거 비밀", model.call_args.args[1])
        with self.assertRaises(ValueError): self.turn("숨겨진 기사", channel="article", visibility="private")

    def test_daily_resume_reopen_and_unchanged_zero_call(self):
        with patch.object(narrate, "director_chat", return_value=("종합 본문\n\n## 이어갈 핵심\n첫날을 기억", "local")) as model:
            result = self.app.generate_chronicle(self.update, self.payload())
        self.assertEqual(1, model.call_count)
        reloaded = fixtures.Ledger(self.ledger.path, "world-one")
        self.assertEqual(self.ledger.state[c.STORE], reloaded.state[c.STORE])
        with patch.object(narrate, "director_chat") as model:
            same = self.app.generate_chronicle(self.update, self.payload(request_id="period-unchanged-02"))
        model.assert_not_called(); self.assertIn("호출하지", same["run"]["message"])
        self.turn("새로운 햄버거 약속", visibility="public")
        with patch.object(narrate, "director_chat", return_value=("추가 장면을 이어 정리", "local")) as model:
            self.app.generate_chronicle(self.update, self.payload(request_id="period-added-0003"))
        packet = json.loads(model.call_args.args[1])
        self.assertEqual("첫날을 기억", packet["prior_continuity"])
        self.assertTrue(all(r["source_id"].startswith("desk:") for r in packet["changed_material_only"]))
        self.assertEqual(2, len(self.ledger.state[c.STORE]["checkpoints"]))
        self.assertIn("chronicle", result)

    def test_partial_success_survives_later_failure_then_skips_completed_parts(self):
        for i in range(9): self.turn(f"긴 자료 {i} " + "기록된 장면입니다. " * 500)
        original = c.plan(self.ledger.state, c.origin(self.ledger.state, self.snap, self.world), self.app._connected_sources(self.config, self.ledger.state, self.snap, self.world), choice())
        self.assertGreater(len(original["batches"]), 2)
        with patch.object(narrate, "director_chat", side_effect=[("먼저 저장한 종합", "local"), RuntimeError("잠시 중단")]):
            with self.assertRaises(RuntimeError): self.app.generate_chronicle(self.update, self.payload())
        saved = copy.deepcopy(self.ledger.state[c.STORE]["checkpoints"])
        self.assertEqual(1, len(saved))
        covered = set(saved[0]["coverage"])
        with patch.object(narrate, "director_chat", return_value=("미반영 부분만 작성", "local")) as model:
            self.app.generate_chronicle(self.update, self.payload(request_id="period-resumed-0002", max_calls=1))
        packet = json.loads(model.call_args.args[1])
        self.assertFalse(covered & {r["id"] for r in packet["changed_material_only"]})
        self.assertEqual(saved[0], self.ledger.state[c.STORE]["checkpoints"][0])

    def test_gemini_consent_and_readonly_precede_model_calls(self):
        with patch.object(gemini_provider, "generate_text") as remote:
            with self.assertRaises(ValueError): self.app.generate_chronicle(self.update, self.payload(provider="gemini"))
            self.app._require_live = Mock(side_effect=RuntimeError("read only"))
            with self.assertRaises(RuntimeError): self.app.generate_chronicle(self.update, self.payload(provider="gemini", remote_consent=True))
        remote.assert_not_called(); self.assertNotIn(c.STORE, self.ledger.state)

    def test_changed_source_while_provider_runs_blocks_late_checkpoint(self):
        def changed(*args, **kwargs):
            self.turn("뒤늦게 바뀐 장면")
            return "오래된 종합", "local"
        with patch.object(narrate, "director_chat", side_effect=changed):
            with self.assertRaises(ValueError): self.app.generate_chronicle(self.update, self.payload())
        self.assertFalse(self.ledger.state[c.STORE]["checkpoints"])

    def test_cancelled_initial_gate_writes_nothing(self):
        from job_manager import JobCancelled
        @contextmanager
        def cancelled():
            raise JobCancelled("cancelled")
            yield
        self.update.commit = cancelled
        before = copy.deepcopy(self.ledger.state)
        with patch.object(narrate, "director_chat") as model:
            with self.assertRaises(JobCancelled): self.app.generate_chronicle(self.update, self.payload(text="편집 지시"))
        model.assert_not_called(); self.assertEqual(before, self.ledger.state)

    def test_preserved_period_read_works_without_live_save(self):
        with patch.object(narrate, "director_chat", return_value=("보관된 종합", "local")):
            self.app.generate_chronicle(self.update, self.payload())
        before = copy.deepcopy(self.ledger.state)
        self.app._prepare = Mock(side_effect=FileNotFoundError("save removed"))
        with patch.object(chronicle_service.config_module, "load", return_value=self.config), patch.object(self.app, "_universe_ledger", return_value=(self.ledger, {})):
            value = self.app.read_chronicle("day", "2027-07-24", universe_id="world-one")
        self.assertTrue(value["read_only"]); self.assertEqual("보관된 종합", value["checkpoints"][0]["text"])
        self.app._prepare.assert_not_called(); self.assertEqual(before, self.ledger.state)

    def test_named_opponent_context_is_dated_and_cloud_opt_in(self):
        self.app.write_opponent_context({"chronicle_origin":c.origin(self.ledger.state, self.snap, self.world),
            "previous_team":"한신", "upcoming_team":"요미우리", "people":[{"name":"테스트 OB", "team":"한신", "role":"ob"}]})
        with patch.object(narrate, "director_chat", return_value=("상대의 다른 반응", "local")) as model:
            self.turn("상대팀 반응", provider="local", channel="article", visibility="public")
        self.assertIn("테스트 OB", model.call_args.args[1])
        with patch.object(gemini_provider, "generate_text", return_value=Mock(text="허용된 반응", model="mock")) as model:
            self.turn("상대팀 반응의 해외편", provider="gemini", channel="article", visibility="public", remote_consent=True)
        self.assertNotIn("테스트 OB", model.call_args.args[1])

    def test_same_completed_request_is_idempotent_and_conflicting_id_rejected(self):
        payload = self.payload()
        with patch.object(narrate, "director_chat", return_value=("완료된 종합", "local")) as model:
            self.app.generate_chronicle(self.update, payload)
            self.app.generate_chronicle(self.update, payload)
        self.assertEqual(1, model.call_count)
        with self.assertRaises(ValueError): self.app.generate_chronicle(self.update, {**payload, "text":"다른 방향"})

    def test_legacy_cinematic_index_is_additive_and_deduplicated(self):
        self.app._remember_cinematic(self.ledger, self.snap, self.world, "원래의 장문 서사", "local-test")
        self.app._remember_cinematic(self.ledger, self.snap, self.world, "원래의 장문 서사", "local-test")
        self.assertEqual(1, len(self.ledger.state[c.STORE]["cinematics"]))
        sources = self.app._connected_sources(self.config, self.ledger.state, self.snap, self.world)
        row = next(r for r in sources if r["id"].startswith("cinematic:"))
        self.assertFalse(row["remote_allowed"]); self.assertEqual("private", row["visibility"])

    def test_cloud_revocation_between_chunks_stops_next_transmission(self):
        for i in range(7): self.turn("새 기사 " + str(i) + "가" * 6000, channel="article", visibility="public", allow_remote_recall=True)
        payload = self.payload(provider="gemini", remote_consent=True, max_calls=6)
        def update(message, *_):
            if "1묶음 저장 완료" in message: self.config["gemini_consent"] = False
        response = Mock(text="첫 묶음의 종합\n\n## 이어갈 핵심\n첫 기사의 기록", model="mock-model")
        with patch.object(gemini_provider, "generate_text", return_value=response) as model:
            with self.assertRaisesRegex(ValueError, "전송 동의가 해제"):
                self.app.generate_chronicle(update, payload)
        self.assertEqual(1, model.call_count)
        self.assertEqual(1, len(self.ledger.state[c.STORE]["checkpoints"]))

    def test_final_cancel_gate_keeps_success_but_not_completed_receipt(self):
        class Gate:
            count = 0
            def __call__(self, *_): pass
            @contextmanager
            def commit(self):
                self.count += 1
                if self.count == 3: raise RuntimeError("cancel at final gate")
                yield
        with patch.object(narrate, "director_chat", return_value=("종합 본문\n\n## 이어갈 핵심\n저장할 첫 묶음", "local")):
            with self.assertRaisesRegex(RuntimeError, "final gate"):
                self.app.generate_chronicle(Gate(), self.payload())
        self.assertEqual(1, len(self.ledger.state[c.STORE]["checkpoints"]))
        self.assertEqual("running", self.ledger.state[c.STORE]["receipts"][-1]["status"])


if __name__ == "__main__":
    unittest.main()
