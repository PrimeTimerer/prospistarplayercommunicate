"""Mock transports for the additive multimodal director path; zero real requests."""
import base64
import json
import unittest
from unittest.mock import patch

import gemini_provider as gemini
import narrate
import test_gemini_provider as fixtures

PART = {"data": b"synthetic-image", "mime": "image/png"}


class DirectorProviderTests(unittest.TestCase):
    def test_gemini_images_survive_format_repair_and_usage_is_complete(self):
        requests = []
        def transport(request, **kwargs):
            requests.append(json.loads(request.data))
            reason = "MAX_TOKENS" if len(requests) == 1 else "STOP"
            return fixtures._Response(fixtures._completion(finish_reason=reason))
        result = gemini.generate_text("시스템", "사용자 입력", api_key="test-only", image_parts=[PART, PART], urlopen=transport)
        self.assertEqual(2, result.request_count)
        self.assertEqual(40, result.total_tokens)
        for request in requests:
            parts = request["contents"][0]["parts"]
            self.assertEqual(3, len(parts))
            self.assertEqual(PART["data"], base64.b64decode(parts[1]["inline_data"]["data"]))

    def test_gemini_rejects_bad_images_without_network(self):
        for parts in ([PART] * 5, [{"mime":"text/html", "data":b"x"}], [{"mime":"image/png", "data":b"x" * (12*1024*1024+1)}]):
            with self.subTest(parts=len(parts)), patch.object(gemini.urllib.request, "urlopen") as transport:
                with self.assertRaises(ValueError): gemini.generate_text("s", "u", api_key="test-only", image_parts=parts)
                transport.assert_not_called()

    def test_gemini_text_path_has_no_image_fields(self):
        requests = []
        def transport(request, **kwargs):
            requests.append(json.loads(request.data))
            return fixtures._Response(fixtures._completion())
        gemini.generate_text("시스템", "입력", api_key="test-only", urlopen=transport)
        self.assertEqual([{"text":"입력"}], requests[0]["contents"][0]["parts"])

    def test_local_images_require_vision_endpoint_and_keep_all_selected_parts(self):
        response = fixtures._Response(json.dumps({"choices":[{"message":{"content":"햄버거 장면"}, "finish_reason":"stop"}]}).encode())
        with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8082/v1/chat/completions", "vision")) as endpoint, patch.object(narrate.urllib.request, "urlopen", return_value=response) as transport:
            result = narrate.director_chat("system", "user", image_parts=[PART, PART])
        endpoint.assert_called_once_with(need_vision=True)
        body = json.loads(transport.call_args.args[0].data)
        self.assertEqual(3, len(body["messages"][1]["content"]))
        self.assertEqual(("햄버거 장면", "vision"), result)

    def test_local_absent_never_drops_images_or_starts_anything(self):
        with patch.object(narrate, "pick_endpoint", return_value=(None, None)) as endpoint, patch.object(narrate.urllib.request, "urlopen") as transport:
            with self.assertRaises(RuntimeError): narrate.director_chat("s", "u", image_parts=[PART])
        endpoint.assert_called_once_with(need_vision=True)
        transport.assert_not_called()

    def test_local_truncation_empty_and_structured_content_are_rejected(self):
        for content, reason in (("잘린 장면", "length"), ("", "stop"), (["잘못된 응답"], "stop")):
            response = fixtures._Response(json.dumps({"choices":[{"message":{"content":content}, "finish_reason":reason}]}).encode())
            with patch.object(narrate, "pick_endpoint", return_value=("http://127.0.0.1:8082/v1/chat/completions", "local")), patch.object(narrate.urllib.request, "urlopen", return_value=response):
                with self.assertRaises(RuntimeError): narrate.director_chat("s", "u")
