#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gemini_provider import ProviderResult
from provider_usage import ProviderUsageStore, connection_is_recent


class ProviderUsageStoreTests(unittest.TestCase):
    def test_missing_store_is_read_only_and_reports_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-usage.json"
            summary = ProviderUsageStore(path).summary(
                now=datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
            )
            self.assertFalse(path.exists())
            self.assertEqual(0, summary["total"]["calls"])
            self.assertEqual("untested", summary["connection"]["status"])

    def test_generation_and_failure_accumulate_only_numeric_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-usage.json"
            store = ProviderUsageStore(path)
            result = ProviderResult(
                text="must never reach usage storage",
                model="gemini-test",
                request_hash="secret-prompt-hash",
                request_bytes=100,
                response_bytes=200,
                finish_reason="STOP",
                request_count=2,
                prompt_tokens=30,
                output_tokens=20,
                total_tokens=50,
                cached_tokens=5,
                thoughts_tokens=3,
                metered_responses=2,
            )
            now = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)

            store.record_generation(result, operation="story_chat", now=now)
            store.record_failure(
                operation="cinematic_narrative", model="gemini-test",
                error_code="QUOTA_EXCEEDED", calls=1, request_bytes=40, now=now,
            )
            summary = store.summary(now=now)
            disk = path.read_text(encoding="utf-8")

            self.assertEqual(3, summary["today"]["calls"])
            self.assertEqual(1, summary["total"]["completed_generations"])
            self.assertEqual(1, summary["total"]["failed_operations"])
            self.assertEqual(50, summary["total"]["total_tokens"])
            self.assertEqual(140, summary["total"]["request_bytes"])
            self.assertNotIn(result.text, disk)
            self.assertNotIn(result.request_hash, disk)
            self.assertEqual("failed", summary["last_operation"]["status"])

    def test_connection_probe_is_separate_from_generation_counters(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-usage.json"
            store = ProviderUsageStore(path)
            summary = store.record_connection(
                connected=True,
                model="gemini-test",
                now=datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc),
            )
            self.assertEqual("verified", summary["connection"]["status"])
            self.assertEqual(0, summary["total"]["calls"])

    def test_recent_connection_requires_matching_status_model_and_time_window(self):
        now = datetime(2026, 9, 4, 13, 5, tzinfo=timezone.utc)
        summary = {
            "connection": {
                "status": "verified",
                "tested_at": "2026-09-04T13:00:00+00:00",
                "model": "gemini-test",
            }
        }
        self.assertTrue(connection_is_recent(summary, model="gemini-test", status="verified", max_age_seconds=301, now=now))
        self.assertFalse(connection_is_recent(summary, model="gemini-other", status="verified", max_age_seconds=301, now=now))
        self.assertFalse(connection_is_recent(summary, model="gemini-test", status="failed", max_age_seconds=301, now=now))
        self.assertFalse(connection_is_recent(summary, model="gemini-test", status="verified", max_age_seconds=299, now=now))


if __name__ == "__main__":
    unittest.main()
