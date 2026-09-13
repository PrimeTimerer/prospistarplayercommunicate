"""Structured review wire format and private diagnostics; no live inference."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gemini_provider as gp
import narrative_review as review
from tests import test_narrative_review as fixtures


def prepared(text=fixtures.VALID):
    return fixtures.ReviewProtocolTests().prepared(text)


class ReviewWireFormatTests(unittest.TestCase):
    def test_review_alone_requests_json_schema_with_bound_ids(self):
        send = fixtures.Transport(prefix=fixtures.ReviewAfterRecoveryTests.TRUNCATED)
        result = fixtures.generate(send)
        first, repair, request = send.calls
        self.assertNotIn("responseMimeType", first["generationConfig"])
        self.assertNotIn("responseJsonSchema", repair["generationConfig"])
        config = request["generationConfig"]
        self.assertEqual("application/json", config["responseMimeType"])
        self.assertNotIn("responseSchema", config)
        schema = config["responseJsonSchema"]
        payload = json.loads(request["contents"][0]["parts"][0]["text"])
        self.assertEqual(["approve", "revise", "reject"], schema["properties"]["verdict"]["enum"])
        self.assertEqual(set(schema["properties"]), set(schema["required"]))
        self.assertFalse(schema["additionalProperties"])
        findings = schema["properties"]["findings"]
        self.assertEqual(len(payload["findings"]), findings["minItems"])
        self.assertEqual(len(payload["findings"]), findings["maxItems"])
        self.assertEqual([row["id"] for row in payload["findings"]], findings["items"]["properties"]["id"]["enum"])
        edits = schema["properties"]["edits"]
        self.assertEqual(review.MAX_EDITS, edits["maxItems"])
        self.assertEqual(len(payload["draft_paragraphs"]), edits["items"]["properties"]["paragraph_id"]["maximum"])
        self.assertEqual(3, result.request_count)

    def test_healthy_request_keeps_legacy_body_exactly(self):
        send = fixtures.Transport("현재 시즌 499탈삼진이다.")
        result = fixtures.generate(send)
        self.assertEqual(1, result.request_count)
        self.assertEqual({"maxOutputTokens": 2400}, send.calls[0]["generationConfig"])

    def test_schema_is_in_request_hash_and_body_size_guard(self):
        send, request_bytes = fixtures.Transport(), []
        def capture(request, timeout):
            request_bytes.append(request.data)
            return send(request, timeout)
        result = fixtures.generate(capture)
        self.assertEqual(hashlib.sha256(request_bytes[-1]).hexdigest(), result.request_hash)
        self.assertEqual(sum(map(len, request_bytes)), result.request_bytes)
        self.assertIn(b'"responseJsonSchema"', request_bytes[-1])
        with patch.object(gp, "MAX_PROMPT_BYTES", 100), self.assertRaises(ValueError):
            gp._body("한국어", "검토", 2400, response_schema=review.response_schema(prepared()))

    def test_prompt_does_not_present_pipe_alternatives_as_literal_values(self):
        self.assertNotIn('"approve|revise|reject"', review.SYSTEM)
        self.assertNotIn('"valid|corrected|unsupported"', review.SYSTEM)

    def test_split_json_parts_and_thought_part_do_not_corrupt_review(self):
        send = fixtures.Transport()
        def multipart(request, timeout):
            response = send(request, timeout)
            if len(send.calls) == 2:
                value = json.loads(response.payload)
                text = value["candidates"][0]["content"]["parts"][0]["text"]
                split = text.index("approve") + 3
                value["candidates"][0]["content"]["parts"] = [
                    {"thought": True, "text": "PRIVATE_THOUGHT_FIXTURE"},
                    {"text": text[:split]}, {"text": text[split:]}]
                return fixtures._Response(json.dumps(value).encode())
            return response
        result = fixtures.generate(multipart)
        self.assertEqual(fixtures.VALID, result.text)
        self.assertEqual("approve", result.validation_review["verdict"])
        self.assertNotIn("PRIVATE_THOUGHT_FIXTURE", str(result))

    def test_plain_final_parts_preserve_old_newlines_without_thought_text(self):
        raw = json.loads(fixtures._completion())
        raw["candidates"][0]["content"]["parts"] = [
            {"text": " 첫 장면 "}, {"thought": True, "text": "PRIVATE_THOUGHT_FIXTURE"}, {"text": " 둘째 장면 "}]
        text, _, usage = gp._response_text(json.dumps(raw).encode())
        self.assertEqual("첫 장면\n둘째 장면", text)
        self.assertEqual(1, usage["thoughts_tokens"])

    def test_structured_whitespace_fragment_inside_reason_is_preserved(self):
        context = prepared()
        verdict = fixtures.verdict(json.loads(context["request"]))
        verdict["reason"] = "목표 표현"
        text = json.dumps(verdict, ensure_ascii=False)
        space = text.index("목표 표현") + 2
        raw = json.loads(fixtures._completion())
        raw["candidates"][0]["content"]["parts"] = [
            {"text": text[:space]}, {"text": " "}, {"text": text[space + 1:]}]
        result, _, _ = gp._response_text(json.dumps(raw).encode(), structured=True)
        self.assertEqual(text, result)
        _, audit = review.adjudicate(result, context, model="fixture")
        self.assertEqual("목표 표현", audit["reason"])

    def test_thought_only_response_is_not_a_verdict(self):
        raw = json.loads(fixtures._completion("PRIVATE_THOUGHT_FIXTURE"))
        raw["candidates"][0]["content"]["parts"][0]["thought"] = True
        with self.assertRaises(gp.GeminiProviderError) as caught:
            gp._response_text(json.dumps(raw).encode(), structured=True)
        self.assertEqual("EMPTY_RESPONSE", caught.exception.code)


class ReviewCompatibilityTests(unittest.TestCase):
    def test_bom_case_whitespace_and_explicit_null_edits_are_unambiguous(self):
        context = prepared()
        value = fixtures.verdict(json.loads(context["request"]))
        value.update(verdict=" APPROVE ", edits=None)
        for row in value["findings"]:
            row["id"] = " " + row["id"].upper() + " "
            row["assessment"] = " VALID "
        text, audit = review.adjudicate("\ufeff" + json.dumps(value), context, model="fixture")
        self.assertEqual(fixtures.VALID, text)
        self.assertEqual("approve", audit["verdict"])

    def test_numeric_string_paragraph_id_keeps_same_edit_scope(self):
        context = prepared(fixtures.draft(fixtures.BAD))
        value = fixtures.verdict(json.loads(context["request"]), "revise", [{"paragraph_id": "4", "text": fixtures.VALID}])
        text, audit = review.adjudicate(json.dumps(value), context, model="fixture")
        self.assertEqual(fixtures.draft(fixtures.VALID), text)
        self.assertEqual([4], audit["paragraph_ids"])

    def test_missing_or_ambiguous_information_is_not_invented(self):
        context = prepared()
        valid = fixtures.verdict(json.loads(context["request"]))
        for value in ({"verdict": "approve"}, {**valid, "verdict": "approve|revise|reject"},
                      {**valid, "reason": None}, {**valid, "findings": []},
                      {**valid, "verdict": "revise", "edits": None}):
            with self.subTest(value=value), self.assertRaises(review.ReviewError):
                review.adjudicate(json.dumps(value), context, model="fixture")
        for raw in ("검토 완료 " + json.dumps(valid), json.dumps(valid) * 2,
                    '{"verdict":"reject","verdict":"approve"}'):
            with self.subTest(raw=raw), self.assertRaises(review.ReviewError):
                review.adjudicate(raw, context, model="fixture")

    def test_uppercase_reject_is_still_rejection_not_recovery(self):
        send = fixtures.Transport(fixtures.draft(fixtures.BAD), mutate=lambda _value: {"verdict": " REJECT "})
        with self.assertRaises(gp.GeminiProviderError) as caught:
            fixtures.generate(send)
        self.assertEqual("rejected", caught.exception.semantic_review_status)
        self.assertEqual(2, caught.exception.request_count)

    def test_reasoned_failures_identify_syntax_verdict_and_edit_fields(self):
        context = prepared()
        valid = fixtures.verdict(json.loads(context["request"]))
        examples = [("not json", "json_syntax"), ('{"verdict": "unknown"}', "verdict_value"),
                    (json.dumps({**valid, "reason": None}), "reason_missing"),
                    (json.dumps({**valid, "edits": {}}), "edits_shape")]
        for raw, detail in examples:
            with self.subTest(detail=detail), self.assertRaises(review.ReviewError) as caught:
                review.adjudicate(raw, context, model="fixture")
            self.assertEqual(detail, caught.exception.detail)

    def test_oversized_integer_json_is_a_contained_format_failure(self):
        with self.assertRaises(review.ReviewError) as caught:
            review.adjudicate('{"verdict":' + '9' * 5000 + '}', prepared(), model="fixture")
        self.assertEqual("invalid_response", caught.exception.status)


class ReviewDiagnosticTests(unittest.TestCase):
    def bad_transport(self, message="LOCAL_REVIEW_REPLY_SENTINEL fixture-key"):
        return fixtures.Transport(mutate=lambda value: {**value, "verdict": message})

    def test_failed_reply_is_bounded_redacted_and_separate_from_public_failure(self):
        send = self.bad_transport()
        with self.assertRaises(gp.GeminiProviderError) as caught:
            fixtures.generate(send)
        error = caught.exception
        diagnostic = error.validation_review_diagnostic
        self.assertIn("LOCAL_REVIEW_REPLY_SENTINEL", diagnostic["response"])
        self.assertNotIn("fixture-key", str(diagnostic))
        self.assertFalse(diagnostic["remote_allowed"])
        self.assertFalse(diagnostic["record_authority"])
        self.assertEqual(hashlib.sha256(fixtures.VALID.encode()).hexdigest(), diagnostic["draft_sha256"])
        self.assertEqual("verdict_value", diagnostic["detail"])
        public = gp.public_failure_detail(error)
        self.assertEqual("verdict_value", public["semantic_review_detail"])
        self.assertNotIn("LOCAL_REVIEW_REPLY_SENTINEL", str(public))
        self.assertNotIn("LOCAL_REVIEW_REPLY_SENTINEL", str(error))
        self.assertEqual(2, error.request_count)

    def test_oversized_reply_is_capped_before_private_persistence(self):
        with self.assertRaises(gp.GeminiProviderError) as caught:
            fixtures.generate(self.bad_transport("LOCAL_REVIEW_REPLY_SENTINEL" * 2000))
        diagnostic = caught.exception.validation_review_diagnostic
        self.assertLessEqual(len(diagnostic["response"]), review.MAX_DIAGNOSTIC_CHARS)
        self.assertTrue(diagnostic["truncated"])
        self.assertGreater(diagnostic["response_chars"], len(diagnostic["response"]))

    def test_service_keeps_reply_only_in_world_local_diagnostic(self):
        with tempfile.TemporaryDirectory() as root:
            result, output, ledger, previous, messages, _ = fixtures.ReviewServiceTests().run_case(
                root, transport=self.bad_transport(), prefix=())
            evidence = json.loads((output / "diagnostics/narrative-validation.latest.json").read_text(encoding="utf-8"))
            diagnostic = evidence["records"][0]["review_diagnostic"]
            self.assertIn("LOCAL_REVIEW_REPLY_SENTINEL", diagnostic["response"])
            self.assertFalse(evidence["remote_allowed"])
            self.assertEqual(previous, ledger.state)
            self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))
            self.assertNotIn("LOCAL_REVIEW_REPLY_SENTINEL", str(result) + str(messages) + str(ledger.state))

    def test_cancelled_or_stale_failure_does_not_write_private_reply(self):
        for options in ({"cancel": True}, {"stale": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                result, output, ledger, previous, _, _ = fixtures.ReviewServiceTests().run_case(
                    root, transport=self.bad_transport(), **options)
                self.assertIsInstance(result, Exception)
                self.assertFalse((output / "diagnostics").exists())
                self.assertEqual(previous, ledger.state)
                self.assertEqual("기존 서사", (output / "narrative.md").read_text(encoding="utf-8"))

    def test_legacy_safe_recovery_retains_private_review_reply_without_claiming_approval(self):
        send = fixtures.Transport(fixtures.draft(fixtures.BAD), mutate=lambda _value: {"verdict": "bad format"})
        result = fixtures.generate(send)
        self.assertIsNone(result.validation_review)
        self.assertTrue(result.validation_repair)
        self.assertIn("bad format", result.validation_review_diagnostic["response"])
        self.assertNotIn("bad format", str(result))


if __name__ == "__main__":
    unittest.main()
