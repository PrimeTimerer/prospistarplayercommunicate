#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

from ui_server import LocalAppServer


class FakeService:
    def __init__(self, root):
        self.image_path = Path(root) / "known.png"
        self.image_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    def bootstrap(self):
        return {"schema_version": 1, "app": {"offline": True}}

    def rescan_saves(self):
        return [
            {
                "path": "C:/fixture/00/StarPlayer.dat",
                "slot": "00",
                "identity_status": "verified",
                "player": {
                    "name": "Paul Skenes",
                    "team": "YOKOHAMA DeNA BAYSTARS",
                    "age": 20,
                },
            }
        ]

    def check_save(self, update, options=None):
        update("done", 100, "complete")
        return {"checked": True, "options": options or {}}

    def generate_narrative(self, update, options=None):
        update("done", 100, "complete")
        return {"narrative": True, "options": options or {}}

    def enrich_feed(self, update, options=None):
        update("done", 100, "complete")
        return {"feed": {"media": [], "boards": [], "social": []}, "options": options or {}}

    def update_settings(self, payload):
        return payload

    def test_gemini(self, payload):
        return {
            "connected": True,
            "provider": "gemini",
            "model": payload.get("gemini_model", "gemini-test"),
            "key_source": "unsaved_input",
        }

    def auto_activate_gemini(self):
        return {
            "connected": True,
            "activated": True,
            "attempted": True,
            "status": "verified",
            "config": {"ai_provider": "gemini"},
        }

    def local_model_status(self):
        return {
            "state": "off", "reachable": False, "owned": False,
            "can_start": True, "can_stop": False, "automatic_start": False,
        }

    def start_local_model(self, payload):
        if payload.get("confirmed") is not True:
            raise RuntimeError("confirmation required")
        return {
            "state": "starting", "reachable": False, "owned": True,
            "can_start": False, "can_stop": True, "automatic_start": False,
        }

    def stop_local_model(self, payload):
        if payload.get("confirmed") is not True:
            raise RuntimeError("confirmation required")
        return {
            "state": "off", "reachable": False, "owned": False,
            "can_start": True, "can_stop": False, "automatic_start": False,
        }

    def capture_game(self, count=1, interval_ms=650, trigger="ui"):
        return {
            "ok": True,
            "message": f"captured {count}",
            "count": count,
            "requested": count,
            "trigger": trigger,
            "captures": [{"name": f"capture-{index}.png"} for index in range(count)],
        }

    def capture_status(self):
        return {
            "items": [{"name": "known.png", "image_url": "/api/v1/capture-image/known.png"}],
            "pending_count": 1,
            "narrative_limit": 8,
            "hotkeys": {"active": True},
        }

    def read_capture_path(self, name):
        if name != "known.png":
            raise FileNotFoundError("missing")
        return self.image_path

    def remove_capture(self, name):
        if name != "known.png":
            raise FileNotFoundError("missing")
        return {"removed": name, "items": [], "pending_count": 0}

    def upsert_career_season(self, payload):
        return {"item": payload, "dashboard": {"career": {"seasons": [payload]}}}

    def upsert_career_honor(self, payload):
        return {"item": payload, "dashboard": {"career": {"honors": [payload]}}}

    def remove_career_item(self, payload):
        return {"removed": True, "dashboard": {"career": {}}}

    def read_daily_archive(self, archive_id):
        if archive_id != "known":
            raise FileNotFoundError("missing")
        return {"id": archive_id, "feed": {"boards": [], "media": []}}

    def create_story_event(self, payload):
        return {"item": {"source": "button", "input": payload}, "dashboard": {"story": {}}}

    def chat_story(self, payload):
        return {"item": {"source": "llm", "input": payload}, "dashboard": {"story": {}}}

    def upsert_personal_context(self, payload):
        return {"item": {"context_id": "context-test", **payload}, "dashboard": {"story": {}}}

    def retire_personal_context(self, payload):
        return {"item": {"status": "retired", **payload}, "dashboard": {"story": {}}}

    def upsert_counterpart(self, payload):
        return {"item": {"entity_id": "counterpart-test", **payload}, "dashboard": {"story": {}}}

    def retire_counterpart(self, payload):
        return {"item": {"status": "retired", **payload}, "dashboard": {"story": {}}}

    def upsert_day_context(self, payload):
        return {"item": payload, "dashboard": {"value_lab": payload}}

    def read_history_capsule(self, game_date):
        if game_date != "2027-07-24":
            raise FileNotFoundError("missing")
        return {"game_date": game_date, "status": "sealed"}

    def read_story_desk(self, *, before=None, day=None):
        return {"before": before, "day": day, "turns": []}

    def write_story_memory(self, payload):
        return {"dashboard": {"story_desk": payload}}

    def write_director_turn(self, update, options=None):
        update("stored", 100, "complete")
        return {"story_desk": options}

    def read_story_image(self, name, *, universe_id=None):
        if name != "known.png" or universe_id not in (None, "known-world"):
            raise FileNotFoundError("missing")
        return self.image_path


    def read_editorial_sources(self, *, channel, day=None):
        return {"channel":channel, "day":day, "sources":[]}

    def read_editorial_source(self, ident):
        return {"id":ident, "text":"보관 원문"}

    def read_chronicle(self, kind, key, *, provider="local", universe_id=None):
        return {"kind":kind, "key":key, "provider":provider, "universe_id":universe_id}

    def write_opponent_context(self, payload):
        return {"dashboard":{"context":payload}}

    def generate_chronicle(self, update, payload):
        update("done", 100, "complete")
        return {"chronicle":payload}


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = LocalAppServer()
        self.server.service = FakeService(self.temp.name)
        parsed = urlparse(self.server.start())
        self.host = parsed.hostname
        self.port = parsed.port

    def tearDown(self):
        self.server.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        raw = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if raw is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=raw, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        connection.close()
        return result

    def test_bootstrap_issues_token_and_no_store_headers(self):
        status, headers, body = self.request("GET", "/api/v1/bootstrap")
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual(self.server.csrf_token, payload["csrf_token"])
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_save_scan_exposes_player_team_and_age(self):
        status, headers, body = self.request("GET", "/api/v1/saves")
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual("Paul Skenes", payload["saves"][0]["player"]["name"])
        self.assertEqual("YOKOHAMA DeNA BAYSTARS", payload["saves"][0]["player"]["team"])
        self.assertEqual(20, payload["saves"][0]["player"]["age"])

    def test_static_shell_has_strict_csp_and_traversal_is_rejected(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(b"StarPlayer", body)
        status, _headers, _body = self.request("GET", "/%2e%2e/config.json")
        self.assertEqual(404, status)

    def test_mutation_requires_csrf_and_rejects_non_image_upload(self):
        status, _headers, body = self.request("POST", "/api/v1/settings", {})
        self.assertEqual(403, status)
        self.assertEqual("TOKEN_REJECTED", json.loads(body)["error"]["code"])
        status, _headers, body = self.request(
            "POST",
            "/api/v1/upload",
            {"data": "dGV4dA=="},
            {"X-StarMode-Token": self.server.csrf_token},
        )
        self.assertEqual(400, status)
        self.assertEqual("IMAGE_TYPE_REJECTED", json.loads(body)["error"]["code"])

    def test_gemini_probe_is_an_explicit_csrf_protected_operation(self):
        status, _headers, body = self.request(
            "POST",
            "/api/v1/providers/gemini/test",
            {"gemini_model": "gemini-contract"},
            {"X-StarMode-Token": self.server.csrf_token},
        )
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertTrue(payload["connected"])
        self.assertEqual("gemini-contract", payload["model"])

    def test_gemini_auto_activation_is_csrf_protected(self):
        status, _headers, body = self.request("POST", "/api/v1/providers/gemini/auto-activate", {})
        self.assertEqual(403, status)
        status, _headers, body = self.request(
            "POST",
            "/api/v1/providers/gemini/auto-activate",
            {},
            {"X-StarMode-Token": self.server.csrf_token},
        )
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertTrue(payload["connected"])
        self.assertEqual("gemini", payload["config"]["ai_provider"])

    def test_local_model_status_is_read_only_and_start_stop_are_csrf_protected(self):
        status, response_headers, body = self.request("GET", "/api/v1/providers/local/status")
        self.assertEqual(200, status)
        self.assertIn("no-store", response_headers["Cache-Control"])
        self.assertFalse(json.loads(body)["status"]["automatic_start"])

        status, _response_headers, _body = self.request(
            "POST", "/api/v1/providers/local/start", {"confirmed": True}
        )
        self.assertEqual(403, status)
        headers = {"X-StarMode-Token": self.server.csrf_token}
        status, _response_headers, body = self.request(
            "POST", "/api/v1/providers/local/start", {"confirmed": True}, headers
        )
        self.assertEqual(200, status)
        self.assertEqual("starting", json.loads(body)["status"]["state"])
        status, _response_headers, body = self.request(
            "POST", "/api/v1/providers/local/stop", {"confirmed": True}, headers
        )
        self.assertEqual(200, status)
        self.assertEqual("off", json.loads(body)["status"]["state"])

    def test_job_result_is_addressed_by_unique_id(self):
        status, _headers, body = self.request(
            "POST",
            "/api/v1/jobs/check",
            {"heat": 5},
            {"X-StarMode-Token": self.server.csrf_token},
        )
        self.assertEqual(202, status)
        job_id = json.loads(body)["job"]["id"]
        deadline = time.time() + 2
        job = None
        while time.time() < deadline:
            status, _headers, body = self.request("GET", f"/api/v1/jobs/{job_id}")
            self.assertEqual(200, status)
            job = json.loads(body)["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual("completed", job["status"])
        self.assertEqual({"checked": True, "options": {"heat": 5}}, job["result"])

    def test_whole_feed_enrichment_is_a_server_owned_job(self):
        status, _headers, body = self.request(
            "POST",
            "/api/v1/jobs/feed",
            {"provider": "local_auto"},
            {"X-StarMode-Token": self.server.csrf_token},
        )
        self.assertEqual(202, status)
        job_id = json.loads(body)["job"]["id"]
        deadline = time.time() + 2
        job = None
        while time.time() < deadline:
            status, _headers, body = self.request("GET", f"/api/v1/jobs/{job_id}")
            self.assertEqual(200, status)
            job = json.loads(body)["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual("completed", job["status"])
        self.assertEqual("local_auto", job["result"]["options"]["provider"])

    def test_article_and_community_enrichment_are_separate_server_owned_jobs(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        for path, expected_kind, expected_scope in (
            ("/api/v1/jobs/feed/articles", "feed_articles", "articles"),
            ("/api/v1/jobs/feed/community", "feed_community", "community"),
        ):
            with self.subTest(path=path):
                status, _headers, body = self.request("POST", path, {"provider": "gemini"}, headers)
                self.assertEqual(202, status)
                job_id = json.loads(body)["job"]["id"]
                deadline = time.time() + 2
                job = None
                while time.time() < deadline:
                    status, _headers, body = self.request("GET", f"/api/v1/jobs/{job_id}")
                    self.assertEqual(200, status)
                    job = json.loads(body)["job"]
                    if job["status"] == "completed":
                        break
                    time.sleep(0.02)
                self.assertEqual("completed", job["status"])
                self.assertEqual(expected_kind, job["kind"])
                self.assertEqual("gemini", job["result"]["options"]["provider"])
                self.assertEqual(expected_scope, job["result"]["options"]["surface_scope"])

    def test_explicit_local_routes_override_only_the_requested_job(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        for path, expected_kind, expected_scope in (
            ("/api/v1/jobs/feed/articles/local", "feed_articles", "articles"),
            ("/api/v1/jobs/feed/community/local", "feed_community", "community"),
        ):
            with self.subTest(path=path):
                status, _headers, body = self.request("POST", path, {"provider": "gemini"}, headers)
                self.assertEqual(202, status)
                job_id = json.loads(body)["job"]["id"]
                deadline = time.time() + 2
                job = None
                while time.time() < deadline:
                    status, _headers, body = self.request("GET", f"/api/v1/jobs/{job_id}")
                    self.assertEqual(200, status)
                    job = json.loads(body)["job"]
                    if job["status"] == "completed":
                        break
                    time.sleep(0.02)
                self.assertEqual("completed", job["status"])
                self.assertEqual(expected_kind, job["kind"])
                self.assertEqual("local_only", job["result"]["options"]["provider"])
                self.assertEqual(expected_scope, job["result"]["options"]["surface_scope"])

        status, _headers, body = self.request(
            "POST", "/api/v1/jobs/narrative/local", {"provider": "gemini"}, headers
        )
        self.assertEqual(202, status)
        job_id = json.loads(body)["job"]["id"]
        deadline = time.time() + 2
        job = None
        while time.time() < deadline:
            status, _headers, body = self.request("GET", f"/api/v1/jobs/{job_id}")
            self.assertEqual(200, status)
            job = json.loads(body)["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual("completed", job["status"])

        self.assertEqual("local_only", job["result"]["options"]["provider"])

    def test_foreign_host_header_is_rejected(self):
        status, _headers, body = self.request(
            "GET", "/api/v1/health", headers={"Host": "evil.example"}
        )
        self.assertEqual(403, status)
        self.assertEqual("HOST_REJECTED", json.loads(body)["error"]["code"])

    def test_career_mutations_are_csrf_protected_and_archive_is_addressable(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        season = {"season_year": 2026, "stats": {"bat_H": 100}}
        status, _response_headers, body = self.request(
            "POST", "/api/v1/career/season", season, headers
        )
        self.assertEqual(200, status)
        self.assertEqual(2026, json.loads(body)["item"]["season_year"])
        status, _response_headers, body = self.request("GET", "/api/v1/archive/known")
        self.assertEqual(200, status)
        self.assertEqual("known", json.loads(body)["archive"]["id"])
        status, _response_headers, body = self.request("GET", "/api/v1/archive/missing")
        self.assertEqual(404, status)
        self.assertEqual("ARCHIVE_NOT_FOUND", json.loads(body)["error"]["code"])

    def test_multiple_capture_api_gallery_and_removal_are_addressed(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        status, _response_headers, body = self.request(
            "POST", "/api/v1/capture", {"count": 3, "interval_ms": 150}, headers
        )
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual(3, payload["capture"]["count"])
        self.assertEqual("ui", payload["capture"]["trigger"])

        status, response_headers, body = self.request("GET", "/api/v1/captures")
        self.assertEqual(200, status)
        self.assertIn("no-store", response_headers["Cache-Control"])
        self.assertEqual(1, json.loads(body)["captures"]["pending_count"])

        status, response_headers, body = self.request(
            "GET", "/api/v1/capture-image/known.png"
        )
        self.assertEqual(200, status)
        self.assertEqual("image/png", response_headers["Content-Type"])
        self.assertTrue(body.startswith(b"\x89PNG"))

        status, _response_headers, body = self.request(
            "POST", "/api/v1/captures/remove", {"name": "known.png"}, headers
        )
        self.assertEqual(200, status)
        self.assertEqual("known.png", json.loads(body)["captures"]["removed"])

    def test_story_context_and_history_capsule_endpoints_keep_csrf_boundary(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        status, _response_headers, body = self.request(
            "POST",
            "/api/v1/story/event",
            {"category": "fans", "situation": "fan_message"},
            headers,
        )
        self.assertEqual(200, status)
        self.assertEqual("button", json.loads(body)["item"]["source"])
        status, _response_headers, body = self.request(
            "POST", "/api/v1/day-context", {"standings": []}, headers
        )
        self.assertEqual(200, status)
        self.assertEqual([], json.loads(body)["item"]["standings"])
        status, _response_headers, body = self.request(
            "GET", "/api/v1/history/2027-07-24"
        )
        self.assertEqual(200, status)
        self.assertEqual("sealed", json.loads(body)["capsule"]["status"])
        status, _response_headers, body = self.request(
            "GET", "/api/v1/history/2027-07-25"
        )
        self.assertEqual(404, status)
        self.assertEqual("HISTORY_NOT_FOUND", json.loads(body)["error"]["code"])

    def test_worldbook_mutations_are_csrf_protected_and_addressed(self):
        headers = {"X-StarMode-Token": self.server.csrf_token}
        calls = [
            ("/api/v1/worldbook/context", {"label": "햄버거"}, "context_id", "context-test"),
            ("/api/v1/worldbook/context/retire", {"context_id": "context-test"}, "status", "retired"),
            ("/api/v1/worldbook/counterpart", {"canonical_name": "가상 후배"}, "entity_id", "counterpart-test"),
            ("/api/v1/worldbook/counterpart/retire", {"entity_id": "counterpart-test"}, "status", "retired"),
        ]
        for path, request, key, expected in calls:
            with self.subTest(path=path):
                status, _response_headers, body = self.request("POST", path, request, headers)
                self.assertEqual(200, status)
                self.assertEqual(expected, json.loads(body)["item"][key])
        status, _response_headers, body = self.request(
            "POST", "/api/v1/worldbook/context", {"label": "차단"}
        )
        self.assertEqual(403, status)
        self.assertEqual("TOKEN_REJECTED", json.loads(body)["error"]["code"])

    def test_director_read_and_memory_routes_keep_csrf_boundary(self):
        status, headers, body = self.request("GET", "/api/v1/story-desk?before=older&day=2027-07-24")
        self.assertEqual(200, status)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual("older", json.loads(body)["story_desk"]["before"])
        for route in ("/api/v1/story-desk/memory", "/api/v1/jobs/director"):
            self.assertEqual(403, self.request("POST", route, {})[0])
        status, _, body = self.request("POST", "/api/v1/story-desk/memory", {"label":"기억"}, {"X-StarMode-Token":self.server.csrf_token})
        self.assertEqual(200, status)
        self.assertEqual("기억", json.loads(body)["dashboard"]["story_desk"]["label"])

    def test_director_job_survives_request_connection_and_has_own_result(self):
        status, _, body = self.request("POST", "/api/v1/jobs/director", {"provider":"note"}, {"X-StarMode-Token":self.server.csrf_token})
        self.assertEqual(202, status)
        job_id = json.loads(body)["job"]["id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = json.loads(self.request("GET", f"/api/v1/jobs/{job_id}")[2])["job"]
            if job["status"] == "completed": break
            time.sleep(.01)
        self.assertEqual("completed", job["status"])
        self.assertEqual("director", job["kind"])
        self.assertEqual("note", job["result"]["story_desk"]["provider"])

    def test_director_images_can_be_read_from_own_preserved_world(self):
        for route in ("/api/v1/story-desk/image/known.png", "/api/v1/universes/known-world/story-image/known.png"):
            status, headers, body = self.request("GET", route)
            self.assertEqual(200, status)
            self.assertTrue(body.startswith(b"\x89PNG"))
            self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(404, self.request("GET", "/api/v1/universes/wrong-world/story-image/known.png")[0])

    def test_connected_reads_and_museum_period_are_explicit_and_uncached(self):
        for route, field, key, expected in (
            ("/api/v1/editorial/sources?channel=article&day=2027-07-24", None, "channel", "article"),
            ("/api/v1/editorial/source?id=desk%3Atest", "source", "id", "desk:test"),
            ("/api/v1/chronicle?kind=month&key=2027-07&provider=gemini", "chronicle", "provider", "gemini"),
            ("/api/v1/universes/old-world/chronicle/year/2027", "chronicle", "universe_id", "old-world"),
        ):
            status, headers, body = self.request("GET", route)
            self.assertEqual(200, status)
            self.assertIn("no-store", headers["Cache-Control"])
            value = json.loads(body)
            self.assertEqual(expected, (value[field] if field else value)[key])

    def test_connected_mutations_require_csrf_and_chronicle_uses_server_job(self):
        for route in ("/api/v1/editorial/context", "/api/v1/jobs/chronicle"):
            self.assertEqual(403, self.request("POST", route, {})[0])
        auth = {"X-StarMode-Token":self.server.csrf_token}
        self.assertEqual(200, self.request("POST", "/api/v1/editorial/context", {"people":[]}, auth)[0])
        status, _, body = self.request("POST", "/api/v1/jobs/chronicle", {"kind":"day"}, auth)
        self.assertEqual(202, status)
        job_id = json.loads(body)["job"]["id"]
        for _ in range(100):
            job = json.loads(self.request("GET", f"/api/v1/jobs/{job_id}")[2])["job"]
            if job["status"] == "completed": break
            time.sleep(.01)
        self.assertEqual("chronicle", job["kind"])
        self.assertEqual("day", job["result"]["chronicle"]["kind"])


if __name__ == "__main__":
    unittest.main()
