#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Security and protocol contracts for the optional Gemini adapter."""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import config_v2
import gemini_provider
from secret_store import SecretStore, _protect_windows, _unprotect_windows


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.payload


def _completion(text="풍부하지만 사실 경계를 지킨 응답", finish_reason="STOP") -> bytes:
    return json.dumps(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "finishReason": finish_reason,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 8,
                "totalTokenCount": 20,
                "cachedContentTokenCount": 2,
                "thoughtsTokenCount": 1,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


class SecretStoreTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI contract")
    def test_real_current_user_dpapi_round_trip_uses_disposable_bytes(self):
        value = b"StarModeFeed disposable DPAPI test value"
        protected = _protect_windows(value)
        self.assertNotEqual(value, protected)
        self.assertEqual(value, _unprotect_windows(protected))

    def test_round_trip_is_separate_and_plaintext_never_reaches_disk(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / "secrets.json"
            protect = lambda value: b"protected:" + value[::-1]
            unprotect = lambda value: value.removeprefix(b"protected:")[::-1]
            store = SecretStore(path, protect=protect, unprotect=unprotect)
            key = "test-key-never-written-verbatim"

            store.set_gemini_key(key)

            self.assertNotIn(key, path.read_text(encoding="utf-8"))
            self.assertEqual((key, "windows_account"), store.get_gemini_key())
            self.assertEqual(
                {"configured": True, "source": "windows_account"},
                store.public_status(),
            )
            self.assertTrue(store.clear_gemini_key())
            self.assertEqual((None, None), store.get_gemini_key())

    def test_documented_environment_precedence_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "google-primary", "GEMINI_API_KEY": "gemini-secondary"},
            clear=True,
        ):
            path = Path(temp) / "secrets.json"
            store = SecretStore(path, protect=lambda value: value, unprotect=lambda value: value)

            self.assertEqual(
                ("google-primary", "environment:GOOGLE_API_KEY"),
                store.get_gemini_key(),
            )
            self.assertFalse(path.exists())

    def test_status_check_never_moves_or_rewrites_a_corrupt_secret_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / "secrets.json"
            original = b"{not-json"
            path.write_bytes(original)
            store = SecretStore(path, protect=lambda value: value, unprotect=lambda value: value)

            self.assertEqual(
                {"configured": False, "source": "unreadable"},
                store.public_status(),
            )
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([], list(path.parent.glob("secrets.json.corrupt-*")))


class GeminiProtocolTests(unittest.TestCase):
    def test_allowlist_contains_only_current_documented_stable_text_models(self):
        self.assertEqual(
            (
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
            ),
            gemini_provider.SUPPORTED_MODELS,
        )

    def test_generate_uses_fixed_host_header_key_and_bounded_json(self):
        requests = []

        def urlopen(request, timeout):
            requests.append((request, timeout))
            return _Response(_completion())

        result = gemini_provider.generate_text(
            "사실과 창작을 분리하라.",
            "검증된 요약",
            api_key="header-only-secret",
            model=gemini_provider.DEFAULT_MODEL,
            max_tokens=1200,
            urlopen=urlopen,
        )

        self.assertEqual("풍부하지만 사실 경계를 지킨 응답", result.text)
        self.assertEqual("STOP", result.finish_reason)
        self.assertEqual(1, result.request_count)
        self.assertEqual(12, result.prompt_tokens)
        self.assertEqual(8, result.output_tokens)
        self.assertEqual(20, result.total_tokens)
        self.assertEqual(1, result.metered_responses)
        request, timeout = requests[0]
        self.assertEqual("POST", request.get_method())
        self.assertTrue(request.full_url.startswith(gemini_provider.API_ROOT + "/"))
        self.assertNotIn("header-only-secret", request.full_url)
        self.assertNotIn(b"header-only-secret", request.data)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual("header-only-secret", headers["x-goog-api-key"])
        self.assertGreaterEqual(timeout, 3.0)

    def test_only_invalid_or_empty_responses_receive_one_bounded_retry(self):
        payloads = iter([b"not-json", _completion("두 번째 응답")])
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return _Response(next(payloads))

        result = gemini_provider.generate_text(
            "시스템", "입력", api_key="secret", urlopen=urlopen
        )
        self.assertEqual("두 번째 응답", result.text)
        self.assertEqual(2, len(calls))
        self.assertEqual(2, result.request_count)
        self.assertEqual(20, result.total_tokens)

    def test_explicit_503_responses_retry_with_backoff_and_honest_accounting(self):
        requests = []
        delays = []
        retries = []

        def urlopen(request, timeout):
            requests.append(request)
            if len(requests) < 3:
                raise urllib.error.HTTPError(
                    request.full_url,
                    503,
                    "temporary overload",
                    {},
                    io.BytesIO(b"untrusted-server-body"),
                )
            return _Response(_completion("세 번째 요청에서 정상 응답을 받았습니다."))

        result = gemini_provider.generate_text(
            "시스템",
            "입력",
            api_key="secret",
            urlopen=urlopen,
            sleep_fn=delays.append,
            random_fn=lambda: 0.0,
            on_transient_retry=retries.append,
        )

        self.assertEqual("세 번째 요청에서 정상 응답을 받았습니다.", result.text)
        self.assertEqual(3, result.request_count)
        self.assertEqual(sum(len(request.data) for request in requests), result.request_bytes)
        self.assertEqual([5.0, 15.0], delays)
        self.assertEqual([1, 2], [row["retry"] for row in retries])
        self.assertTrue(all(row["max_retries"] == 3 for row in retries))
        self.assertTrue(all(row["code"] == "REMOTE_UNAVAILABLE" for row in retries))

    def test_exhausted_503_retries_report_bounded_failure_without_key_leakage(self):
        key = "sensitive-key"
        requests = []
        delays = []

        def urlopen(request, timeout):
            requests.append(request)
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "temporary overload",
                {},
                io.BytesIO(f"server echoed {key}".encode()),
            )

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "시스템",
                "입력",
                api_key=key,
                urlopen=urlopen,
                sleep_fn=delays.append,
                random_fn=lambda: 0.0,
            )

        self.assertEqual("REMOTE_UNAVAILABLE", caught.exception.code)
        self.assertEqual(4, caught.exception.request_count)
        self.assertEqual(sum(len(request.data) for request in requests), caught.exception.request_bytes)
        self.assertEqual([5.0, 15.0, 45.0], delays)
        self.assertIn("3회 자동 재시도", str(caught.exception))
        self.assertNotIn(key, str(caught.exception))

    def test_auth_error_is_not_automatically_retried(self):
        requests = []
        delays = []

        def urlopen(request, timeout):
            requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO(b"ignored"))

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "시스템", "입력", api_key="secret", urlopen=urlopen,
                sleep_fn=delays.append, random_fn=lambda: 0.0,
            )

        self.assertEqual("AUTH_REJECTED", caught.exception.code)
        self.assertEqual(1, caught.exception.request_count)
        self.assertEqual(1, len(requests))
        self.assertEqual([], delays)

    def test_ambiguous_network_failure_is_not_automatically_replayed(self):
        requests = []
        delays = []

        def urlopen(request, timeout):
            requests.append(request)
            raise urllib.error.URLError("connection reset after upload")

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "시스템", "입력", api_key="secret", urlopen=urlopen,
                sleep_fn=delays.append, random_fn=lambda: 0.0,
            )

        self.assertEqual("NETWORK_FAILED", caught.exception.code)
        self.assertEqual(1, caught.exception.request_count)
        self.assertEqual(1, len(requests))
        self.assertEqual([], delays)

    def test_non_korean_or_unsupported_numeric_claim_is_rewritten_before_commit(self):
        payloads = iter(
            [
                _completion("The player suddenly recorded 999 home runs in this game."),
                _completion("검증된 110홈런 기록만 바탕으로 오늘의 분위기를 전했다."),
            ]
        )
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return _Response(next(payloads))

        result = gemini_provider.generate_text(
            "한국어로 작성한다.",
            "검증된 시즌 기록은 110홈런이다.",
            api_key="secret",
            urlopen=urlopen,
        )

        self.assertEqual("검증된 110홈런 기록만 바탕으로 오늘의 분위기를 전했다.", result.text)
        self.assertEqual(2, len(requests))
        self.assertIn("한국어 문단", requests[1].data.decode("utf-8"))

    def test_two_ungrounded_numeric_responses_are_rejected(self):
        def urlopen(request, timeout):
            return _Response(_completion("검증되지 않은 999승을 달성했다고 말했다."))

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "한국어로 작성한다.",
                "검증 기록은 20승이다.",
                api_key="secret",
                urlopen=urlopen,
            )
        self.assertEqual("UNSUPPORTED_NUMERIC_CLAIM", caught.exception.code)
        self.assertEqual(2, caught.exception.request_count)
        self.assertEqual(40, caught.exception.total_tokens)

    def test_truncated_response_is_repaired_once_and_never_committed_partial(self):
        payloads = iter(
            [
                _completion("끝나지 않은 첫 문단", "MAX_TOKENS"),
                _completion("두 번째 시도에서 마지막 문장까지 완결했다."),
            ]
        )
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return _Response(next(payloads))

        result = gemini_provider.generate_text(
            "한국어로 작성한다.", "검증된 입력", api_key="secret", urlopen=urlopen
        )

        self.assertEqual("두 번째 시도에서 마지막 문장까지 완결했다.", result.text)
        self.assertEqual(2, len(requests))
        self.assertIn("마지막 문장까지 완결", requests[1].data.decode("utf-8"))

    def test_non_stop_policy_finish_is_rejected_without_retry(self):
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            return _Response(_completion("일부 응답", "SAFETY"))

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "한국어로 작성한다.", "검증된 입력", api_key="secret", urlopen=urlopen
            )

        self.assertEqual("GENERATION_BLOCKED", caught.exception.code)
        self.assertEqual(1, len(calls))

    def test_remote_errors_are_categorized_without_response_or_key_leakage(self):
        key = "sensitive-key"
        calls = []
        delays = []

        def urlopen(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "quota",
                {},
                io.BytesIO(f"server echoed {key}".encode()),
            )

        with self.assertRaises(gemini_provider.GeminiProviderError) as caught:
            gemini_provider.generate_text(
                "시스템",
                "입력",
                api_key=key,
                urlopen=urlopen,
                sleep_fn=delays.append,
                random_fn=lambda: 0.0,
            )
        self.assertEqual("QUOTA_EXCEEDED", caught.exception.code)
        self.assertEqual(3, caught.exception.request_count)
        self.assertEqual(3, len(calls))
        self.assertEqual([5.0, 15.0], delays)
        self.assertIn("2회 자동 재시도", str(caught.exception))
        self.assertNotIn(key, str(caught.exception))

    def test_connection_probe_is_get_only_and_contains_no_story_payload(self):
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            payload = {"name": f"models/{gemini_provider.DEFAULT_MODEL}"}
            return _Response(json.dumps(payload).encode("utf-8"))

        result = gemini_provider.test_connection(api_key="secret", urlopen=urlopen)

        self.assertTrue(result["connected"])
        self.assertEqual("GET", requests[0].get_method())
        self.assertIsNone(requests[0].data)

    def test_vision_request_contains_one_inline_image_and_no_local_path(self):
        requests = []
        image = b"\x89PNG\r\n\x1a\nsynthetic-image"

        def urlopen(request, timeout):
            requests.append(request)
            return _Response(_completion("화면에는 선수 이름과 기록표가 보입니다."))

        result = gemini_provider.generate_vision(
            "보이는 것만 읽는다.",
            "이미지를 한국어로 설명한다.",
            image_bytes=image,
            mime_type="image/png",
            api_key="header-only-secret",
            urlopen=urlopen,
        )

        payload = json.loads(requests[0].data.decode("utf-8"))
        inline = payload["contents"][0]["parts"][0]["inline_data"]
        self.assertEqual("image/png", inline["mime_type"])
        self.assertEqual(image, base64.b64decode(inline["data"]))
        self.assertNotIn("C:\\", requests[0].data.decode("utf-8"))
        self.assertEqual("화면에는 선수 이름과 기록표가 보입니다.", result.text)

    def test_vision_uses_the_same_bounded_transient_retry_path(self):
        requests = []
        delays = []
        image = b"\x89PNG\r\n\x1a\nsynthetic-image"

        def urlopen(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b"ignored"))
            return _Response(_completion("재시도 뒤 화면의 기록표를 읽었습니다."))

        result = gemini_provider.generate_vision(
            "보이는 것만 읽는다.",
            "이미지를 설명한다.",
            image_bytes=image,
            mime_type="image/png",
            api_key="secret",
            urlopen=urlopen,
            sleep_fn=delays.append,
            random_fn=lambda: 0.0,
        )

        self.assertEqual("재시도 뒤 화면의 기록표를 읽었습니다.", result.text)
        self.assertEqual(2, result.request_count)
        self.assertEqual([5.0], delays)

    def test_vision_rejects_unsupported_image_before_network_access(self):
        with self.assertRaises(ValueError):
            gemini_provider.generate_vision(
                "시스템",
                "이미지 읽기",
                image_bytes=b"GIF89a-synthetic",
                mime_type="image/gif",
                api_key="secret",
                urlopen=lambda *_args, **_kwargs: self.fail("network must not be called"),
            )

    def test_unlisted_model_is_rejected_before_network_access(self):
        with self.assertRaises(ValueError):
            gemini_provider.generate_text(
                "시스템",
                "입력",
                api_key="secret",
                model="gemini-invented-preview",
                urlopen=lambda *_args, **_kwargs: self.fail("network must not be called"),
            )

    def test_key_bearing_request_rejects_every_non_allowlisted_origin(self):
        with self.assertRaises(ValueError):
            gemini_provider._request(
                "https://example.com/v1beta/models/test",
                api_key="secret",
                data=None,
                timeout=3,
            )

    def test_default_transport_refuses_redirect_construction(self):
        handler = gemini_provider._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, "https://example.com"))


class ConfigSecurityTests(unittest.TestCase):
    def test_legacy_config_defaults_off_and_string_consent_never_enables_network(self):
        legacy = config_v2._validate({"heat": 9})
        tampered = config_v2._validate({"gemini_consent": "true"})
        explicit = config_v2._validate({"gemini_consent": True})

        self.assertEqual("local_auto", legacy["ai_provider"])
        self.assertFalse(legacy["gemini_consent"])
        self.assertFalse(tampered["gemini_consent"])
        self.assertTrue(explicit["gemini_consent"])

    def test_community_language_and_notification_settings_are_backward_compatible(self):
        legacy = config_v2._validate({})
        high = config_v2._validate({"community_language_level": 9, "notifications_enabled": True})
        low = config_v2._validate({"community_language_level": -5, "notifications_enabled": "true"})

        self.assertEqual(2, legacy["community_language_level"])
        self.assertFalse(legacy["notifications_enabled"])
        self.assertEqual(5, high["community_language_level"])
        self.assertTrue(high["notifications_enabled"])
        self.assertEqual(1, low["community_language_level"])
        self.assertFalse(low["notifications_enabled"])


if __name__ == "__main__":
    unittest.main()
