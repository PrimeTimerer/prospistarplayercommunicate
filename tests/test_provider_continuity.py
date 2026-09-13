"""2.5.20 preserves successful provider contracts and bounds failure work."""

import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import Mock, patch

import config_v2
import editorial_engine
import gemini_provider as gemini
import narrate
import provider_feed
import service as service_module
from job_manager import JobCancelled, JobManager
from tests.test_gemini_provider import _Response, _completion
from tests.test_provider_feed import event, feed, spotlight, valid_response
from tests.test_feed_recovery import _fixture, _app, _remote_result
from tests.test_feed_provider_service import _model_response


class RequestContinuityTests(unittest.TestCase):
    def test_healthy_pacer_keeps_five_requests_immediate(self):
        sleep = Mock()
        pacer = gemini.BatchRequestPacer(clock=lambda: 10.0, sleep=sleep)
        for _ in range(5):
            pacer.wait()
        sleep.assert_not_called()

    def test_congestion_cooldown_is_shared_then_staggered(self):
        now = [10.0]
        pacer = gemini.BatchRequestPacer(clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n))
        pacer.defer({"code": "QUOTA_EXCEEDED", "delay_seconds": 5})
        pacer.wait()
        self.assertAlmostEqual(15, now[0])
        pacer.wait()
        self.assertAlmostEqual(17, now[0])

    def test_pacer_cancellation_does_not_wait_for_cooldown(self):
        sleep = Mock()
        pacer = gemini.BatchRequestPacer(sleep=sleep)
        pacer.defer({"code": "REMOTE_UNAVAILABLE", "delay_seconds": 45})
        with self.assertRaises(JobCancelled):
            pacer.wait(Mock(side_effect=JobCancelled("stop")))
        sleep.assert_not_called()

    def test_cancelled_remote_job_never_sends(self):
        send = Mock()
        with self.assertRaises(JobCancelled):
            gemini.generate_text("지시", "기록", api_key="fixture", urlopen=send,
                                 check_cancelled=Mock(side_effect=JobCancelled("stop")))
        send.assert_not_called()

    def test_cancel_during_backoff_prevents_another_request(self):
        cancelled = [False]
        waits = []

        def check():
            if cancelled[0]:
                raise JobCancelled("stop")

        def sleep(seconds):
            waits.append(seconds)
            cancelled[0] = True

        send = Mock(side_effect=urllib.error.HTTPError("https://example.invalid", 503, "busy", {}, io.BytesIO()))
        with self.assertRaises(JobCancelled):
            gemini.generate_text("지시", "기록", api_key="fixture", urlopen=send,
                                 check_cancelled=check, sleep_fn=sleep)
        self.assertEqual(1, send.call_count)
        self.assertEqual([0.2], waits)

    def test_successful_remote_request_and_result_are_unchanged(self):
        bodies = []

        def send(request, timeout):
            bodies.append(request.data)
            return _Response(_completion())

        before = gemini.generate_text("지시", "기록", api_key="fixture", urlopen=send)
        after = gemini.generate_text("지시", "기록", api_key="fixture", urlopen=send,
                                    check_cancelled=lambda: None, before_request=lambda: None)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(before, after)

    def test_cancel_probe_does_not_add_log_rows(self):
        manager = JobManager()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        observed = []

        def work(update):
            entered.set()
            release.wait(2)
            try:
                update.check_cancelled()
            except JobCancelled:
                observed.append("cancelled")
                raise
            finally:
                finished.set()

        job = manager.start("check", work)
        self.assertTrue(entered.wait(2))
        original_logs = manager.get(job["id"])["logs"]
        manager.cancel(job["id"])
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(["cancelled"], observed)
        self.assertEqual(original_logs, manager.get(job["id"])["logs"])

    def test_concurrency_default_and_limits_are_compatible(self):
        self.assertEqual(5, config_v2._validate({})["gemini_batch_concurrency"])
        for value, expected in [(1, 1), (2, 2), (99, 5), (0, 1), ("bad", 5)]:
            self.assertEqual(expected, config_v2._validate({"gemini_batch_concurrency": value})["gemini_batch_concurrency"])

    def test_healthy_remote_batches_keep_one_request_per_batch(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, evt, config, world, _output = _fixture(root, provider="gemini")
            app = _app(ledger, evt, config, world)
            with patch.object(app, "_secret_store") as secrets, patch.object(app, "_record_gemini_generation"), patch.object(
                service_module.gemini_provider, "generate_text", side_effect=lambda _s, user, **kw: _remote_result(user)
            ) as send, patch.object(app, "_ensure_local_llm") as local:
                secrets.return_value.get_gemini_key.return_value = ("fixture", "fixture")
                dashboard = app.enrich_feed(lambda *_args: None)
            self.assertEqual(dashboard["feed"]["reaction_bundle"]["batch_count"], send.call_count)
            self.assertTrue(all(row["attempts"] == 1 for row in dashboard["feed"]["reaction_bundle"]["provider_audit"]["batches"]))
            local.assert_not_called()

    def test_cancelled_batch_queue_never_calls_a_provider(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, evt, config, world, _output = _fixture(root, provider="gemini")
            app = _app(ledger, evt, config, world)
            update = Mock()
            update.check_cancelled.side_effect = JobCancelled("stop")
            with patch.object(app, "_secret_store") as secrets, patch.object(
                service_module.gemini_provider, "generate_text"
            ) as send, patch.object(app, "_ensure_local_llm") as local:
                secrets.return_value.get_gemini_key.return_value = ("fixture", "fixture")
                with self.assertRaises(JobCancelled):
                    app.enrich_feed(update)
            send.assert_not_called()
            local.assert_not_called()

    def test_only_the_truncated_local_batch_uses_a_compact_retry(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, evt, config, world, _output = _fixture(root, provider="local")
            app = _app(ledger, evt, config, world)
            calls = []

            def generate(_system, user, **_kwargs):
                ordinal = json.loads(user)["sequential_batch"]["ordinal"]
                calls.append(ordinal)
                if len(calls) == 1:
                    return None, "LOCAL_TRUNCATED_RESPONSE: fixture"
                return _model_response(user), "local-fixture"

            with patch.object(app, "_ensure_local_llm", return_value=True), patch.object(
                service_module.narration_module, "structured_feed", side_effect=generate
            ):
                dashboard = app.enrich_feed(lambda *_args: None)
            total = dashboard["feed"]["reaction_bundle"]["batch_count"]
            self.assertEqual([1, *range(1, total + 1)], calls)
            self.assertEqual("local_llm_expression", dashboard["feed"]["reaction_bundle"]["renderer"])


class LocalTransportContinuityTests(unittest.TestCase):
    def _run(self, send, **kwargs):
        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8082/v1/chat/completions", "fixture")), patch.object(
            narrate.urllib.request, "urlopen", side_effect=send
        ) as network:
            result = narrate.structured_feed("system", "user", **kwargs)
        return result, network

    def test_timeout_never_starts_three_long_generations(self):
        for error in [TimeoutError(), urllib.error.URLError(TimeoutError())]:
            result, network = self._run(error, json_schema={"type": "object"})
            self.assertIsNone(result[0])
            self.assertIn("시간", result[1])
            self.assertEqual(1, network.call_count)

    def test_schema_compatibility_uses_remaining_timeout(self):
        times = []

        def send(request, timeout):
            times.append(timeout)
            if len(times) == 1:
                raise urllib.error.HTTPError(request.full_url, 422, "unsupported", {}, io.BytesIO())
            return _Response(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

        with patch.object(narrate.time, "monotonic", side_effect=[0, 0, 5]):
            result, _network = self._run(send, timeout=10)
        self.assertEqual([10, 5], times)
        self.assertEqual(("{}", "fixture"), result)

    def test_local_truncation_is_not_mistaken_for_valid_json(self):
        data = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        result, _network = self._run(lambda *_a, **_k: _Response(json.dumps(data).encode()))
        self.assertIsNone(result[0])
        self.assertTrue(result[1].startswith("LOCAL_TRUNCATED_RESPONSE"))

    def test_local_non_text_message_is_not_stringified(self):
        data = {"choices": [{"message": {"content": [{"text": "not an answer"}]}}]}
        result, _network = self._run(lambda *_a, **_k: _Response(json.dumps(data).encode()))
        self.assertIsNone(result[0])

    def test_malformed_local_choice_is_contained(self):
        for choice in [None, "unexpected", []]:
            data = {"choices": [choice]}
            result, _network = self._run(lambda *_a, **_k: _Response(json.dumps(data).encode()))
            self.assertIsNone(result[0])
            self.assertIn("형식", result[1])

    def test_local_cancel_does_not_probe_or_generate(self):
        with patch.object(narrate, "pick_endpoint") as probe:
            with self.assertRaises(JobCancelled):
                narrate.structured_feed("system", "user", check_cancelled=Mock(side_effect=JobCancelled("stop")))
        probe.assert_not_called()


class AuthoredResourceContinuityTests(unittest.TestCase):
    def test_new_pack_candidates_keep_authorship_and_existing_format(self):
        press, discussion, _manifest = editorial_engine.packs()
        self.assertEqual("authored", press["license_class"])
        self.assertEqual("authored", discussion["license_class"])
        for code in ["dc", "fmk", "mlb"]:
            for stance in editorial_engine.STANCES:
                self.assertGreaterEqual(len(discussion["voices"][code][stance]), 6)
        for code, variants in discussion["social_variants"].items():
            self.assertEqual(4, len(variants["posts"]), code)
            self.assertEqual(8, len(variants["replies"]), code)
            self.assertTrue(all("{name" in text for text in variants["posts"]))

    def test_social_variety_is_deterministic_and_never_changes_counts(self):
        original = feed()
        saved = copy.deepcopy(original)
        first = provider_feed.prepare_canonical(original, event(), spotlight(), universe_id="world-a")
        same = provider_feed.prepare_canonical(original, event(), spotlight(), universe_id="world-a")
        self.assertEqual(first, same)
        self.assertEqual(saved, original)
        texts = {code: set() for code, *_ in provider_feed._SOCIAL_SPECS}
        for day in range(1, 25):
            evt = event()
            evt["snapshot"]["content_hash"] = f"fictional-{day}"
            value = provider_feed.prepare_canonical(original, evt, spotlight(), universe_id="world-a")
            self.assertEqual(6, len(value["social"]))
            for post in value["social"]:
                self.assertEqual(4, len(post["replies"]))
                self.assertEqual("authored", post["license_class"])
                self.assertIn("Paul Skenes", post["text"])
                texts[post["code"]].add(post["text"])
        self.assertTrue(all(len(values) >= 4 for values in texts.values()), texts)

    def test_final_assembly_keeps_actual_provider_and_builtin_item_owners(self):
        canonical = provider_feed.prepare_canonical(feed(), event(), spotlight(), universe_id="world-a")
        batches = provider_feed.expression_batches(canonical)
        contract = json.loads(valid_response(canonical))
        audit = {"batches": []}
        for batch in batches:
            audit["batches"].append({"batch_id": batch["batch_id"], "provider": "gemini", "model": "fixture", "reverted_items": []})
        chosen = next(i for i, b in enumerate(batches) if b["kind"] == "social")
        post_id = batches[chosen]["item_ids"][0]
        original = next(r for r in provider_feed.expression_contract(canonical)["social"] if r["id"] == post_id)
        contract["social"] = [original if r["id"] == post_id else r for r in contract["social"]]
        audit["batches"][chosen]["reverted_items"] = [{"id": post_id, "surface": "social", "reasons": ["test"]}]
        result = provider_feed.finalize_batched_expression(canonical, contract, provider="gemini", model="fixture", batches=batches, provider_audit=audit)
        self.assertEqual(contract, provider_feed.expression_contract(result))
        self.assertEqual("builtin", next(r for r in result["social"] if r["id"] == post_id)["expression_renderer"])
        self.assertEqual("gemini", result["media"][0]["expression_renderer"])
        self.assertEqual("mixed_expression", result["reaction_bundle"]["surface_sources"]["community"]["renderer"])
        self.assertEqual("gemini_expression", result["reaction_bundle"]["surface_sources"]["articles"]["renderer"])
