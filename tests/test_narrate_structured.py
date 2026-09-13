#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import narrate


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class StructuredFeedCompatibilityTests(unittest.TestCase):
    def test_request_shape_error_retries_once_without_response_format(self):
        requests = []

        def urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 400, "unsupported", {}, io.BytesIO())
            return _Response({"choices": [{"message": {"content": "{\"version\":\"1\"}"}}]})

        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8082/v1/chat/completions", "fixture")), patch.object(
            narrate.urllib.request, "urlopen", side_effect=urlopen
        ):
            text, model = narrate.structured_feed("system", "user")

        self.assertEqual("fixture", model)
        self.assertEqual('{"version":"1"}', text)
        self.assertIn("response_format", requests[0])
        self.assertNotIn("response_format", requests[1])


if __name__ == "__main__":
    unittest.main()
