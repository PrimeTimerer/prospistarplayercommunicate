"""Semantic review protocol, budgets and publication with synthetic transport."""
import contextlib
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gemini_provider as gp
import narrative_review as review
import service
from tests.test_gemini_provider import _completion, _Response
from tests.test_narrative_context import FACTS, SOURCE, TARGETS
from tests.test_numeric_passage_recovery import SAFE_A, SAFE_B, draft
from tests import test_story_service as story_tests


VALID = "시즌 500탈삼진까지 1개 남음. 25승도 초읽기다."
BAD = "현재 시즌 500탈삼진과 25승을 이미 달성했다."


def verdict(payload, kind="approve", edits=None):
    return {"verdict": kind, "reason": "원본 기록과 앞뒤 문맥을 함께 검토했다.",
            "findings": [{"id": row["id"], "assessment": {"approve": "valid", "revise": "corrected", "reject": "unsupported"}[kind],
                          "reason": "남은 수량과 목표의 표현이며 현재 달성을 주장하지 않는다."}
                         for row in payload["findings"]], "edits": edits or []}


class Transport:
    def __init__(self, text=VALID, kind="approve", edit=None, mutate=None, finish="STOP", prefix=()):
        self.text, self.kind, self.edit, self.mutate, self.finish = text, kind, edit, mutate, finish
        self.prefix = prefix
        self.calls = []

    def __call__(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append(body)
        if len(self.calls) <= len(self.prefix):
            return _Response(_completion(*self.prefix[len(self.calls) - 1]))
        if len(self.calls) == len(self.prefix) + 1:
            return _Response(_completion(self.text))
        payload = json.loads(body["contents"][0]["parts"][0]["text"])
        response = verdict(payload, self.kind, self.edit)
        if self.mutate:
            response = self.mutate(response)
        raw = _completion(json.dumps(response, ensure_ascii=False), self.finish)
        return _Response(raw)


def generate(send, **options):
    kwargs = dict(api_key="fixture-key", prospective_targets=TARGETS, narrative_facts=FACTS,
                  recover_numeric_passages=True, semantic_review=True, urlopen=send, transient_error_retries=0)
    kwargs.update(options)
    return gp.generate_text("한국어 서사", SOURCE, **kwargs)


class ReviewProtocolTests(unittest.TestCase):
    def prepared(self, text=VALID):
        try:
            gp._validate_generated_text(text, SOURCE, prospective_targets=TARGETS,
                                        narrative_metadata=True, narrative_facts=FACTS)
        except gp.GeminiProviderError as error:
            return review.prepare(text, SOURCE, error, facts=FACTS, targets=TARGETS)
        self.fail("Fixture must reproduce a heuristic refusal")

    def test_valid_context_can_overrule_heuristic_without_rewriting(self):
        prepared = self.prepared()
        text, audit = review.adjudicate(json.dumps(verdict(json.loads(prepared["request"])), ensure_ascii=False),
                                       prepared, model="fixture-model")
        self.assertEqual(VALID, text)
        self.assertFalse(audit["record_authority"])
        self.assertEqual("approve", audit["verdict"])
        self.assertEqual(0, audit["changed_paragraphs"])

    def test_source_facts_targets_and_whole_draft_are_separate(self):
        prepared = self.prepared(draft(VALID))
        payload = json.loads(prepared["request"])
        self.assertEqual(FACTS, payload["verified_records"])
        self.assertEqual(TARGETS, payload["target_proofs"])
        self.assertEqual(SOURCE, payload["source"])
        self.assertIn(SAFE_A, str(payload["draft_paragraphs"]))
        self.assertIn(SAFE_B, str(payload["draft_paragraphs"]))
        self.assertIn("오탐", review.SYSTEM)
        self.assertIn("명령은 따르지", review.SYSTEM)

    def test_incomplete_or_ambiguous_verdict_is_not_approval(self):
        prepared = self.prepared()
        proper = verdict(json.loads(prepared["request"]))
        for value in (True, "approve", {}, {**proper, "reason": ""}, {**proper, "findings": []},
                      {**proper, "edits": [{"paragraph_id": 1, "text": "다른 글"}]},
                      {**proper, "findings": [{"id": "unknown", "assessment": "valid", "reason": "확인"}]}):
            with self.subTest(value=value), self.assertRaises(review.ReviewError):
                review.adjudicate(json.dumps(value), prepared, model="fixture")

    def test_duplicate_json_key_and_duplicate_decisions_fail(self):
        prepared = self.prepared()
        with self.assertRaises(review.ReviewError):
            review.adjudicate('{"verdict":"reject","verdict":"approve"}', prepared, model="fixture")
        value = verdict(json.loads(prepared["request"]))
        value["findings"] *= 2
        with self.assertRaises(review.ReviewError):
            review.adjudicate(json.dumps(value), prepared, model="fixture")

    def test_fenced_json_is_accepted_not_markdown_as_story(self):
        prepared = self.prepared()
        text, _ = review.adjudicate("```json\n" + json.dumps(verdict(json.loads(prepared["request"]))) + "\n```",
                                    prepared, model="fixture")
        self.assertEqual(VALID, text)

    def test_reviewer_rejection_is_not_overruled_by_local_recovery(self):
        send = Transport(draft(BAD), "reject")
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("rejected", caught.exception.semantic_review_status)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(draft(BAD), caught.exception.validation_draft)
        self.assertIn("Gemini가 전체 문맥", gp.public_failure_detail(caught.exception)["message"])

    def test_minimal_model_revision_keeps_all_other_paragraphs(self):
        send = Transport(draft(BAD), "revise", [{"paragraph_id": 4, "text": VALID}])
        result = generate(send)
        self.assertEqual(draft(VALID), result.text)
        self.assertIsNone(result.validation_repair)
        self.assertEqual([4], result.validation_review["paragraph_ids"])
        self.assertEqual(hashlib.sha256(result.text.encode()).hexdigest(), result.validation_review["approved_sha256"])
        self.assertEqual(draft(BAD), result.validation_draft)

    def test_brief_explicit_rejection_still_blocks_mechanical_recovery(self):
        send = Transport(draft(BAD), mutate=lambda _value: {"verdict": "reject"})
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("rejected", caught.exception.semantic_review_status)

    def test_invalid_edit_scope_does_not_publish(self):
        prepared = self.prepared(draft(BAD))
        for edits in ([{"paragraph_id": True, "text": "기록"}], [{"paragraph_id": 999, "text": "기록"}],
                      [{"paragraph_id": 4, "text": ""}], [{"paragraph_id": 4, "text": "새 장면\n\n또 다른 장면"}],
                      [{"paragraph_id": 4, "text": VALID}] * 2):
            value = verdict(json.loads(prepared["request"]), "revise", edits)
            with self.subTest(edits=edits), self.assertRaises(review.ReviewError):
                review.adjudicate(json.dumps(value), prepared, model="fixture")

    def test_large_context_is_not_silently_truncated(self):
        error = gp.GeminiProviderError("UNSUPPORTED_NUMERIC_CLAIM", "fixture")
        with self.assertRaises(review.ReviewError):
            review.prepare(VALID * 2000, SOURCE, error)

    def test_approval_is_bound_to_draft_and_context_not_numeric_whitelist(self):
        first = self.prepared()
        other = self.prepared(VALID + " 저녁에는 산책했다.")
        self.assertNotEqual(first["draft_sha256"], other["draft_sha256"])
        self.assertNotEqual(first["context_sha256"], other["context_sha256"])
        gp._validate_generated_text("현재 시즌 499탈삼진이다.", SOURCE)
        with self.assertRaises(gp.GeminiProviderError):
            gp._validate_generated_text(BAD, SOURCE, prospective_targets=TARGETS)


class ReviewAdapterTests(unittest.TestCase):
    def test_flagged_story_uses_one_review_without_full_rewrite(self):
        send, updates = Transport(), []
        result = generate(send, on_validation_retry=updates.append)
        self.assertEqual(VALID, result.text)
        self.assertEqual(2, result.request_count)
        self.assertEqual(24, result.prompt_tokens)
        self.assertEqual(16, result.output_tokens)
        self.assertEqual(40, result.total_tokens)
        self.assertEqual(2, result.metered_responses)
        self.assertEqual("semantic_review", updates[0]["stage"])
        self.assertEqual(2, len(send.calls))
        self.assertLessEqual(send.calls[1]["generationConfig"]["maxOutputTokens"], 2400)
        self.assertEqual(review.SYSTEM, send.calls[1]["system_instruction"]["parts"][0]["text"])
        self.assertFalse(result.validation_draft)

    def test_healthy_story_keeps_single_request(self):
        send = Transport("현재 시즌 499탈삼진이다.")
        result = generate(send)
        self.assertEqual(1, result.request_count)
        self.assertIsNone(result.validation_review)

    def test_legacy_string_token_budget_is_normalized_for_review(self):
        for budget in ("1200", "invalid", None):
            with self.subTest(budget=budget):
                send = Transport()
                result = generate(send, max_tokens=budget)
                self.assertEqual("approve", result.validation_review["verdict"])
                self.assertEqual(1200 if budget == "1200" else 2400,
                                 send.calls[1]["generationConfig"]["maxOutputTokens"])

    def test_generic_or_disabled_review_keeps_previous_validation(self):
        for opts in ({"semantic_review": False}, {"recover_numeric_passages": False}, {"invalid_response_retries": 0}):
            with self.subTest(opts=opts), self.assertRaises(gp.GeminiProviderError):
                generate(lambda *_a, **_k: _Response(_completion(VALID)), **opts)

    def test_malformed_review_never_becomes_story_and_keeps_budget(self):
        send = Transport(mutate=lambda _value: {"verdict": "approve"})
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send, invalid_response_retries=2)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(40, caught.exception.total_tokens)
        self.assertEqual(VALID, caught.exception.validation_draft)
        self.assertIn(caught.exception.semantic_review_status, ("invalid_response", "incomplete_verdict"))

    def test_review_transport_retries_use_shared_request_accounting(self):
        send, tries, delays = Transport(), [], []
        def congested(request, timeout):
            tries.append(request)
            if len(tries) == 2:
                raise gp.GeminiProviderError("REMOTE_UNAVAILABLE", "fixture", retryable=True)
            return send(request, timeout)
        result = generate(congested, transient_error_retries=1, sleep_fn=delays.append)
        self.assertEqual(3, result.request_count)
        self.assertEqual(2, result.metered_responses)
        self.assertEqual("approve", result.validation_review["verdict"])
        self.assertTrue(delays)

    def test_truncated_review_keeps_original_draft_and_metering(self):
        send = Transport(finish="MAX_TOKENS")
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("TRUNCATED_RESPONSE", caught.exception.code)
        self.assertEqual(VALID, caught.exception.validation_draft)
        self.assertEqual(2, caught.exception.request_count)

    def test_remote_review_failure_is_not_approval(self):
        send = Transport()
        def unavailable(request, timeout):
            if send.calls:
                raise gp.GeminiProviderError("REMOTE_TIMEOUT", "fixture timeout", retryable=True)
            return send(request, timeout)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(unavailable)
        self.assertEqual("unavailable", caught.exception.semantic_review_status)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(VALID, caught.exception.validation_draft)

    def test_language_guard_cannot_be_overridden(self):
        send = Transport(BAD, "revise", [{"paragraph_id": 1, "text": "The player has broken every baseball record."}])
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("LANGUAGE_REJECTED", caught.exception.code)

    def test_cancel_after_review_response_stops_publication(self):
        send = Transport()
        def check():
            if len(send.calls) == 2:
                raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            generate(send, check_cancelled=check)

    def test_review_audit_does_not_expose_api_key(self):
        def mutate(value):
            value["reason"] += " fixture-key"
            return value
        result = generate(Transport(mutate=mutate))
        self.assertNotIn("fixture-key", str(result.validation_review))


class ReviewAfterRecoveryTests(unittest.TestCase):
    TRUNCATED = (("잘린 초안은 검토·게시하지 않는다", "MAX_TOKENS"),)

    def test_truncation_repair_then_flagged_complete_draft_gets_one_review(self):
        send, updates = Transport(prefix=self.TRUNCATED), []
        result = generate(send, on_validation_retry=updates.append)
        self.assertEqual(VALID, result.text)
        self.assertEqual("approve", result.validation_review["verdict"])
        self.assertEqual(3, result.request_count)
        self.assertEqual(3, result.metered_responses)
        self.assertEqual(60, result.total_tokens)
        self.assertEqual(36, result.prompt_tokens)
        self.assertEqual(24, result.output_tokens)
        self.assertEqual("TRUNCATED_RESPONSE", updates[0]["code"])
        self.assertEqual("semantic_review", updates[1]["stage"])
        self.assertEqual((1, 1), (updates[1]["retry"], updates[1]["max_retries"]))
        payload = json.loads(send.calls[-1]["contents"][0]["parts"][0]["text"])
        self.assertIn(VALID, str(payload["draft_paragraphs"]))
        self.assertNotIn(self.TRUNCATED[0][0], str(payload))

    def test_successful_truncation_recovery_does_not_need_review(self):
        send = Transport("현재 시즌 499탈삼진이다.", prefix=self.TRUNCATED)
        result = generate(send)
        self.assertEqual(2, result.request_count)
        self.assertIsNone(result.validation_review)

    def test_repeated_truncation_is_never_reviewed_or_extended(self):
        send = Transport(prefix=self.TRUNCATED * 3)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("TRUNCATED_RESPONSE", caught.exception.code)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(40, caught.exception.total_tokens)
        self.assertFalse(getattr(caught.exception, "validation_draft", ""))
        self.assertEqual(2, len(send.calls))

    def test_repaired_language_or_multiple_repairs_keep_bounded_review(self):
        for prefix in ((("The player has broken every baseball record.", "STOP"),), self.TRUNCATED * 2):
            with self.subTest(prefix=prefix):
                send = Transport(prefix=prefix)
                result = generate(send, invalid_response_retries=len(prefix))
                self.assertEqual(len(prefix) + 2, result.request_count)
                self.assertEqual("approve", result.validation_review["verdict"])

    def test_opt_out_and_image_paths_do_not_gain_extra_request(self):
        for options in ({"semantic_review": False}, {"recover_numeric_passages": False},
                        {"image_parts": [{"mime": "image/png", "data": b"fixture-image"}]}):
            with self.subTest(options=options):
                send = Transport(prefix=self.TRUNCATED)
                with self.assertRaises(gp.GeminiProviderError) as caught:
                    generate(send, **options)
                self.assertEqual(2, caught.exception.request_count)
                self.assertEqual(2, len(send.calls))

    def test_disabled_retries_stop_at_truncation_without_review(self):
        send = Transport(prefix=self.TRUNCATED)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send, invalid_response_retries=0)
        self.assertEqual(1, caught.exception.request_count)
        self.assertEqual(1, len(send.calls))

    def test_oversized_review_after_repair_does_not_expand_context_or_requests(self):
        send = Transport(prefix=self.TRUNCATED)
        with patch.object(review, "prepare", side_effect=review.ReviewError("input_too_large")):
            with self.assertRaises(gp.GeminiProviderError) as caught:
                generate(send)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(2, len(send.calls))
        self.assertEqual(VALID, caught.exception.validation_draft)

    def test_explicit_rejection_after_recovery_cannot_be_mechanically_approved(self):
        send = Transport(draft(BAD), "reject", prefix=self.TRUNCATED)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(send)
        self.assertEqual("rejected", caught.exception.semantic_review_status)
        self.assertEqual(draft(BAD), caught.exception.validation_draft)
        self.assertEqual(3, caught.exception.request_count)
        self.assertEqual(60, caught.exception.total_tokens)

    def test_failed_review_after_recovery_has_no_further_repair_loop(self):
        for options in ({"mutate": lambda _value: {"verdict": "approve"}}, {"finish": "MAX_TOKENS"}):
            with self.subTest(options=options):
                send = Transport(prefix=self.TRUNCATED, **options)
                with self.assertRaises(gp.GeminiProviderError) as caught:
                    generate(send)
                self.assertEqual(3, caught.exception.request_count)
                self.assertEqual(3, len(send.calls))
                self.assertEqual(VALID, caught.exception.validation_draft)
                self.assertTrue(caught.exception.semantic_review_status)

    def test_recovery_and_review_share_transient_budget(self):
        send, attempts = Transport(prefix=self.TRUNCATED), []
        def unavailable(request, timeout):
            attempts.append(request)
            if len(attempts) in (1, 4):
                raise gp.GeminiProviderError("REMOTE_UNAVAILABLE", "fixture", retryable=True)
            return send(request, timeout)
        with self.assertRaises(gp.GeminiProviderError) as caught:
            generate(unavailable, transient_error_retries=1, sleep_fn=lambda _delay: None)
        self.assertEqual("unavailable", caught.exception.semantic_review_status)
        self.assertEqual(4, caught.exception.request_count)
        self.assertEqual(2, caught.exception.metered_responses)
        self.assertEqual(4, len(attempts))
        self.assertEqual(VALID, caught.exception.validation_draft)

    def test_cancellation_before_review_prevents_extra_request(self):
        send = Transport(prefix=self.TRUNCATED)
        def check():
            if len(send.calls) == 2:
                raise RuntimeError("cancelled before review")
        with self.assertRaisesRegex(RuntimeError, "cancelled before review"):
            generate(send, check_cancelled=check)
        self.assertEqual(2, len(send.calls))


class ReviewServiceTests(unittest.TestCase):
    def run_case(self, root, *, provider="gemini", consent=True, kind="approve", stale=False, cancel=False, prefix=(), transport=None):
        snap, ledger, event, config, world = story_tests.StoryServiceTests()._fixture(root)
        snap["stats"].update(pit_K=499, pit_W=24, bat_HR=136)
        config.update(ai_provider=provider, gemini_consent=consent)
        service.personal_context.upsert_user_context(
            ledger.state, {"basis": "authored_background", "label": "PRIVATE_REVIEW_SENTINEL",
                           "detail": "로컬에서만 사용하는 정보", "visibility": "private"},
            universe_id=world["world_id"], protagonist_id=7, player_label="Test Player", game_date="2027-07-24")
        app = service.StarModeService()
        app._prepare = lambda update=None: (config, {}, None, ledger, world, event)
        output = Path(config["output_dir"]) / "worlds" / world["world_id"]
        output.mkdir(parents=True)
        (output / "narrative.md").write_text("기존 서사", encoding="utf-8")
        previous = copy.deepcopy(ledger.state)
        send, messages = transport or Transport(kind=kind, prefix=prefix), []
        actual = gp.generate_text
        secret = Mock()
        secret.get_gemini_key.return_value = ("fixture-key", "fixture")
        def remote(system, prompt, **kwargs):
            nonlocal previous
            try:
                return actual(system, prompt, **kwargs, urlopen=send)
            finally:
                if stale:
                    snap["content_hash"] = "changed-save"
                    previous = copy.deepcopy(ledger.state)  # Account for the fixture's external save change.
        def update(*args):
            messages.append(args)
        @contextlib.contextmanager
        def cancelled():
            raise RuntimeError("cancelled")
            yield
        if cancel:
            update.commit = cancelled
        with patch.object(app, "_secret_store", return_value=secret), \
                patch.object(app, "_ensure_local_llm", return_value=False), \
                patch.object(gp, "generate_text", side_effect=remote):
            try:
                result = app.generate_narrative(update)
            except RuntimeError as error:
                result = error
        return result, output, ledger, previous, messages, send

    def test_approval_is_published_with_visible_audit_not_builtin_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            result, output, _, _, messages, send = self.run_case(root)
            self.assertNotIsInstance(result, Exception)
            self.assertEqual(VALID, (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertFalse(result["run"]["fallback"])
            self.assertIn("Gemini 문맥 검토 통과", result["run"]["message"])
            self.assertEqual("approve", result["run"]["provider_audit"]["validation_review"]["verdict"])
            self.assertIn("전체 초안", str(messages))
            self.assertEqual(2, len(send.calls))
            self.assertNotIn("PRIVATE_REVIEW_SENTINEL", str(send.calls))
            self.assertEqual(2, result["config"]["gemini_usage"]["total"]["calls"])

    def test_rejected_review_preserves_previous_story_and_record_state(self):
        with tempfile.TemporaryDirectory() as root:
            result, output, ledger, previous, _, _ = self.run_case(root, kind="reject")
            self.assertTrue(result["run"]["provider_failed"])
            self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertEqual(previous, ledger.state)

    def test_local_or_nonconsenting_runs_never_send_review(self):
        for opts in ({"consent": False}, {"provider": "local_only"}, {"provider": "deterministic"}):
            with self.subTest(opts=opts), tempfile.TemporaryDirectory() as root:
                _, _, _, _, _, send = self.run_case(root, **opts)
                self.assertFalse(send.calls)

    def test_stale_or_cancelled_approved_review_cannot_write(self):
        for opts in ({"stale": True}, {"cancel": True}):
            with self.subTest(opts=opts), tempfile.TemporaryDirectory() as root:
                result, output, ledger, previous, _, _ = self.run_case(root, **opts)
                self.assertIsInstance(result, Exception)
                self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))
                self.assertEqual(previous, ledger.state)
                self.assertFalse((output / "diagnostics").exists())

    def test_recovery_then_review_publishes_gemini_prose_and_accounts_all_calls(self):
        with tempfile.TemporaryDirectory() as root:
            result, output, _, _, messages, send = self.run_case(root, prefix=ReviewAfterRecoveryTests.TRUNCATED)
            self.assertFalse(result["run"]["fallback"])
            self.assertEqual(VALID, (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertEqual("approve", result["run"]["provider_audit"]["validation_review"]["verdict"])
            self.assertIn("Gemini 문맥 검토 통과", result["run"]["message"])
            self.assertIn("전체 초안", str(messages))
            self.assertIn("Gemini 잘린 응답 복구", str(messages))
            self.assertIn("문맥 검토 기회는 유지", str(messages))
            self.assertEqual(3, result["config"]["gemini_usage"]["total"]["calls"])
            self.assertEqual(3, len(send.calls))
            self.assertNotIn("PRIVATE_REVIEW_SENTINEL", str(send.calls))

    def test_recovery_then_rejected_review_preserves_existing_story(self):
        with tempfile.TemporaryDirectory() as root:
            result, output, ledger, previous, _, send = self.run_case(
                root, kind="reject", prefix=ReviewAfterRecoveryTests.TRUNCATED)
            self.assertTrue(result["run"]["provider_failed"])
            self.assertEqual(3, len(send.calls))
            self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertEqual(previous, ledger.state)

    def test_recovery_then_stale_or_cancelled_review_cannot_commit(self):
        for options in ({"stale": True}, {"cancel": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                result, output, ledger, previous, _, send = self.run_case(
                    root, prefix=ReviewAfterRecoveryTests.TRUNCATED, **options)
                self.assertIsInstance(result, Exception)
                self.assertEqual(3, len(send.calls))
                self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))
                self.assertEqual(previous, ledger.state)
                self.assertFalse((output / "diagnostics").exists())


if __name__ == "__main__":
    unittest.main()
