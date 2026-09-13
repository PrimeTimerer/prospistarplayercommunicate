#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned loopback-only HTTP API and static UI server."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import attachments as attachments_module
import applog
import config as config_module
from hotkeys import GlobalCaptureHotkeys
from atomic_io import atomic_write_bytes
from job_manager import JobConflictError, JobManager
from local_model_manager import LocalModelControlError
from service import StarModeService

MAX_JSON_BYTES = 16 * 1024 * 1024
UI_ROOT = Path(__file__).resolve().parent / "ui"
log = applog.get_logger(__name__)


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    return None


class LocalAppServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.csrf_token = secrets.token_urlsafe(32)
        self.service = StarModeService()
        self.jobs = JobManager()
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.capture_hotkeys: GlobalCaptureHotkeys | None = None

    @property
    def url(self) -> str:
        if not self.httpd:
            raise RuntimeError("server has not started")
        return f"http://{self.host}:{self.httpd.server_address[1]}"

    def start(self) -> str:
        owner = self

        class Handler(AppHandler):
            app = owner

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="StarModeFeed-HTTP",
            daemon=True,
        )
        self.thread.start()
        return self.url

    def start_capture_hotkeys(self) -> dict:
        if not self.capture_hotkeys:
            self.capture_hotkeys = GlobalCaptureHotkeys(
                lambda count, trigger: self.service.capture_game(
                    count=count, interval_ms=650, trigger=trigger
                )
            )
            self.service.set_hotkey_status_provider(self.capture_hotkeys.status)
        return self.capture_hotkeys.start()

    def stop(self) -> None:
        if self.capture_hotkeys:
            self.capture_hotkeys.stop()
            self.capture_hotkeys = None
            self.service.set_hotkey_status_provider(None)
        shutdown = getattr(self.service, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception as exc:  # pragma: no cover - best effort during shell exit
                log.exception("local model shutdown failed: %s", exc)
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.httpd = None
        self.thread = None


class AppHandler(BaseHTTPRequestHandler):
    app: LocalAppServer
    server_version = "StarModeFeed/2"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "").lower()
        allowed = {
            f"127.0.0.1:{self.server.server_address[1]}",
            f"localhost:{self.server.server_address[1]}",
            f"[::1]:{self.server.server_address[1]}",
        }
        return host in allowed

    def _base_headers(self, *, api: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if api:
            self.send_header("Cache-Control", "no-store, max-age=0")
        else:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )

    def _send(self, status: int, content_type: str, body: bytes, *, api: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._base_headers(api=api)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, api=True)

    def _error(self, status: int, code: str, message: str, **extra) -> None:
        self._json(status, {"ok": False, "error": {"code": code, "message": message, **extra}})

    def _service_error(self, exc: Exception, default_code: str) -> None:
        """Map the shared live-binding guard to stable codes; keep old codes otherwise."""
        code = getattr(exc, "code", None)
        if code in ("WORLD_READ_ONLY", "WORLD_BINDING_CHANGED"):
            self._error(409, code, str(exc))
        elif code == "UNIVERSE_NOT_FOUND":
            self._error(404, code, str(exc))
        else:
            self._error(400, default_code, str(exc))

    def _read_json(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if size < 0 or size > MAX_JSON_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(size) if size else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def _csrf_ok(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-StarMode-Token", ""), self.app.csrf_token
        )

    @staticmethod
    def _public_job(job: dict | None) -> dict | None:
        if not job:
            return None
        value = dict(job)
        if value.get("error"):
            value["error"] = {
                "type": value["error"].get("type"),
                "message": value["error"].get("message"),
            }
        return value

    def do_GET(self):
        if not self._allowed_host():
            self._error(403, "HOST_REJECTED", "허용되지 않은 로컬 호스트입니다.")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/v1/health":
            self._json(200, {"ok": True, "offline": True})
            return
        if path == "/api/v1/bootstrap":
            dashboard = self.app.service.bootstrap()
            self._json(
                200,
                {
                    "ok": True,
                    "csrf_token": self.app.csrf_token,
                    "dashboard": dashboard,
                    "job": self._public_job(self.app.jobs.latest()),
                },
            )
            return
        if path == "/api/v1/story-desk":
            try:
                query = parse_qs(urlparse(self.path).query)
                arguments = {"before": (query.get("before") or [None])[0], "day": (query.get("day") or [None])[0]}
                if query.get("channel"):
                    arguments["channel"] = query["channel"][0]
                value = self.app.service.read_story_desk(**arguments)
                self._json(200, {"ok": True, "story_desk": value})
            except Exception as exc:
                self._service_error(exc, "STORY_DESK_REJECTED")
            return
        if path in {"/api/v1/editorial/sources", "/api/v1/editorial/source", "/api/v1/chronicle"}:
            try:
                query = parse_qs(urlparse(self.path).query)
                arg = lambda key, default=None: (query.get(key) or [default])[0]
                if path.endswith("/sources"):
                    result = self.app.service.read_editorial_sources(channel=arg("channel", "story"), day=arg("day"))
                elif path.endswith("/source"):
                    result = {"source": self.app.service.read_editorial_source(arg("id", ""))}
                else:
                    result = {"chronicle": self.app.service.read_chronicle(arg("kind", "day"), arg("key", ""), provider=arg("provider", "local"))}
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "CONNECTED_STORY_REJECTED")
            return
        if path.startswith("/api/v1/story-desk/image/"):
            try:
                target = self.app.service.read_story_image(unquote(path.rsplit("/", 1)[-1]))
                self._send(200, mimetypes.guess_type(str(target))[0] or "application/octet-stream", target.read_bytes(), api=True)
            except Exception as exc:
                self._service_error(exc, "STORY_IMAGE_REJECTED")
            return
        if path == "/api/v1/saves":
            self._json(200, {"ok": True, "saves": self.app.service.rescan_saves()})
            return
        if path == "/api/v1/captures":
            self._json(200, {"ok": True, "captures": self.app.service.capture_status()})
            return
        if path.startswith("/api/v1/capture-image/"):
            name = unquote(path.rsplit("/", 1)[-1])
            try:
                target = self.app.service.read_capture_path(name)
                mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._send(200, mime, target.read_bytes(), api=True)
            except FileNotFoundError as exc:
                self._error(404, "CAPTURE_NOT_FOUND", str(exc))
            except Exception as exc:
                self._error(400, "CAPTURE_REJECTED", str(exc))
            return
        if path == "/api/v1/jobs/latest":
            self._json(200, {"ok": True, "job": self._public_job(self.app.jobs.latest())})
            return
        if path.startswith("/api/v1/archive/"):
            archive_id = path.rsplit("/", 1)[-1]
            try:
                archive = self.app.service.read_daily_archive(archive_id)
                self._json(200, {"ok": True, "archive": archive})
            except FileNotFoundError as exc:
                self._error(404, "ARCHIVE_NOT_FOUND", str(exc))
            except Exception as exc:
                self._error(400, "ARCHIVE_REJECTED", str(exc))
            return
        if path.startswith("/api/v1/history/"):
            game_date = unquote(path.rsplit("/", 1)[-1])
            try:
                capsule = self.app.service.read_history_capsule(game_date)
                self._json(200, {"ok": True, "capsule": capsule})
            except FileNotFoundError as exc:
                self._error(404, "HISTORY_NOT_FOUND", str(exc))
            except Exception as exc:
                self._error(400, "HISTORY_REJECTED", str(exc))
            return
        if path.startswith("/api/v1/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = self.app.jobs.get(job_id)
            if not job:
                self._error(404, "JOB_NOT_FOUND", "작업을 찾지 못했습니다.")
            else:
                self._json(200, {"ok": True, "job": self._public_job(job)})
            return
        if path == "/api/v1/universes":
            try:
                self._json(200, {"ok": True, **self.app.service.list_universes()})
            except Exception as exc:
                self._service_error(exc, "UNIVERSES_REJECTED")
            return
        if path == "/api/v1/attachments/status":
            try:
                self._json(200, {"ok": True, "status": self.app.service.attachment_status()})
            except Exception as exc:
                self._service_error(exc, "ATTACHMENT_STATUS_REJECTED")
            return
        if path == "/api/v1/providers/local/status":
            try:
                self._json(200, {"ok": True, "status": self.app.service.local_model_status()})
            except Exception as exc:
                self._error(400, "LOCAL_MODEL_STATUS_REJECTED", str(exc))
            return
        if path.startswith("/api/v1/universes/"):
            self._universe_read(path)
            return
        self._serve_static(path)

    def _universe_read(self, path: str) -> None:
        parts = [unquote(part) for part in path[len("/api/v1/universes/") :].split("/") if part]
        if not parts:
            self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")
            return
        universe_id = parts[0]
        try:
            if len(parts) == 1:
                self._json(200, {"ok": True, "universe": self.app.service.read_universe(universe_id)})
            elif parts[1] == "history" and len(parts) == 2:
                self._json(200, {"ok": True, **self.app.service.read_universe_history(universe_id)})
            elif parts[1] == "history" and len(parts) == 3:
                capsule = self.app.service.read_universe_capsule(universe_id, parts[2])
                self._json(200, {"ok": True, "capsule": capsule})
            elif parts[1] == "reactions" and len(parts) == 3:
                archive = self.app.service.read_universe_reaction(universe_id, parts[2])
                self._json(200, {"ok": True, "archive": archive})
            elif parts[1] == "story-image" and len(parts) == 3:
                target = self.app.service.read_story_image(parts[2], universe_id=universe_id)
                self._send(200, mimetypes.guess_type(str(target))[0] or "application/octet-stream", target.read_bytes(), api=True)
            elif parts[1] == "chronicle" and len(parts) == 4:
                query = parse_qs(urlparse(self.path).query)
                value = self.app.service.read_chronicle(parts[2], parts[3], provider=(query.get("provider") or ["local"])[0], universe_id=universe_id)
                self._json(200, {"ok": True, "chronicle": value})
            else:
                self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")
        except FileNotFoundError as exc:
            self._error(404, getattr(exc, "code", None) or "UNIVERSE_NOT_FOUND", str(exc))
        except Exception as exc:
            self._service_error(exc, "UNIVERSE_REJECTED")

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else unquote(request_path.lstrip("/"))
        target = (UI_ROOT / relative).resolve()
        try:
            target.relative_to(UI_ROOT.resolve())
        except ValueError:
            self._error(404, "NOT_FOUND", "파일을 찾지 못했습니다.")
            return
        if not target.is_file():
            self._error(404, "NOT_FOUND", "파일을 찾지 못했습니다.")
            return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            mime = "text/javascript"
        self._send(200, f"{mime}; charset=utf-8" if mime.startswith("text/") else mime, target.read_bytes())

    def do_POST(self):
        if not self._allowed_host():
            self._error(403, "HOST_REJECTED", "허용되지 않은 로컬 호스트입니다.")
            return
        if not self._csrf_ok():
            self._error(403, "TOKEN_REJECTED", "앱 세션 토큰이 올바르지 않습니다.")
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            self._error(400, "BAD_JSON", str(exc))
            return

        if path == "/api/v1/jobs/director":
            self._start_job("director", self.app.service.write_director_turn, payload)
        elif path == "/api/v1/jobs/chronicle":
            self._start_job("chronicle", self.app.service.generate_chronicle, payload)
        elif path == "/api/v1/editorial/context":
            try:
                self._json(200, {"ok": True, **self.app.service.write_opponent_context(payload)})
            except Exception as exc:
                self._service_error(exc, "OPPONENT_CONTEXT_REJECTED")
        elif path == "/api/v1/story-desk/memory":
            try:
                self._json(200, {"ok": True, **self.app.service.write_story_memory(payload)})
            except Exception as exc:
                self._service_error(exc, "STORY_MEMORY_REJECTED")
        elif path == "/api/v1/jobs/check":
            self._start_job("check", self.app.service.check_save, payload)
        elif path == "/api/v1/jobs/feed":
            self._start_job("feed", self.app.service.enrich_feed, payload)
        elif path == "/api/v1/jobs/feed/articles":
            self._start_job(
                "feed_articles",
                self.app.service.enrich_feed,
                {**payload, "surface_scope": "articles"},
            )
        elif path == "/api/v1/jobs/feed/articles/local":
            self._start_job(
                "feed_articles",
                self.app.service.enrich_feed,
                {**payload, "surface_scope": "articles", "provider": "local_only"},
            )
        elif path == "/api/v1/jobs/feed/community":
            self._start_job(
                "feed_community",
                self.app.service.enrich_feed,
                {**payload, "surface_scope": "community"},
            )
        elif path == "/api/v1/jobs/feed/community/local":
            self._start_job(
                "feed_community",
                self.app.service.enrich_feed,
                {**payload, "surface_scope": "community", "provider": "local_only"},
            )
        elif path == "/api/v1/jobs/narrative":
            self._start_job("narrative", self.app.service.generate_narrative, payload)
        elif path == "/api/v1/jobs/narrative/local":
            self._start_job(
                "narrative",
                self.app.service.generate_narrative,
                {**payload, "provider": "local_only"},
            )
        elif path == "/api/v1/jobs/cancel":
            job = self.app.jobs.cancel(str(payload.get("id") or ""))
            if not job:
                self._error(404, "JOB_NOT_FOUND", "작업을 찾지 못했습니다.")
            else:
                self._json(200, {"ok": True, "job": self._public_job(job)})
        elif path == "/api/v1/capture":
            self._json(
                200,
                {
                    "ok": True,
                    "capture": self.app.service.capture_game(
                        count=payload.get("count", 1),
                        interval_ms=payload.get("interval_ms", 650),
                        trigger="ui",
                    ),
                },
            )
        elif path == "/api/v1/captures/remove":
            try:
                result = self.app.service.remove_capture(str(payload.get("name") or ""))
                self._json(200, {"ok": True, "captures": result})
            except FileNotFoundError as exc:
                self._error(404, "CAPTURE_NOT_FOUND", str(exc))
            except Exception as exc:
                self._error(400, "CAPTURE_REMOVE_REJECTED", str(exc))
        elif path == "/api/v1/settings":
            try:
                config = self.app.service.update_settings(payload)
                self._json(200, {"ok": True, "config": config})
            except Exception as exc:
                self._error(400, "SETTINGS_REJECTED", str(exc))
        elif path == "/api/v1/providers/gemini/test":
            try:
                result = self.app.service.test_gemini(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._error(400, "GEMINI_TEST_REJECTED", str(exc))
        elif path == "/api/v1/providers/gemini/auto-activate":
            try:
                result = self.app.service.auto_activate_gemini()
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._error(400, "GEMINI_AUTO_ACTIVATE_REJECTED", str(exc))
        elif path == "/api/v1/providers/local/start":
            try:
                result = self.app.service.start_local_model(payload)
                self._json(200, {"ok": True, "status": result})
            except LocalModelControlError as exc:
                self._error(409, exc.code, str(exc))
            except Exception as exc:
                self._error(400, "LOCAL_MODEL_START_REJECTED", str(exc))
        elif path == "/api/v1/providers/local/stop":
            try:
                result = self.app.service.stop_local_model(payload)
                self._json(200, {"ok": True, "status": result})
            except LocalModelControlError as exc:
                self._error(409, exc.code, str(exc))
            except Exception as exc:
                self._error(400, "LOCAL_MODEL_STOP_REJECTED", str(exc))
        elif path == "/api/v1/career/season":
            try:
                result = self.app.service.upsert_career_season(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "CAREER_SEASON_REJECTED")
        elif path == "/api/v1/career/honor":
            try:
                result = self.app.service.upsert_career_honor(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "CAREER_HONOR_REJECTED")
        elif path == "/api/v1/career/remove":
            try:
                result = self.app.service.remove_career_item(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "CAREER_REMOVE_REJECTED")
        elif path == "/api/v1/story/event":
            try:
                result = self.app.service.create_story_event(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "STORY_EVENT_REJECTED")
        elif path == "/api/v1/story/chat":
            try:
                result = self.app.service.chat_story(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "STORY_CHAT_REJECTED")
        elif path == "/api/v1/worldbook/context":
            try:
                result = self.app.service.upsert_personal_context(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "WORLD_CONTEXT_REJECTED")
        elif path == "/api/v1/worldbook/context/retire":
            try:
                result = self.app.service.retire_personal_context(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "WORLD_CONTEXT_RETIRE_REJECTED")
        elif path == "/api/v1/worldbook/counterpart":
            try:
                result = self.app.service.upsert_counterpart(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "WORLD_COUNTERPART_REJECTED")
        elif path == "/api/v1/worldbook/counterpart/retire":
            try:
                result = self.app.service.retire_counterpart(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "WORLD_COUNTERPART_RETIRE_REJECTED")
        elif path == "/api/v1/day-context":
            try:
                result = self.app.service.upsert_day_context(payload)
                self._json(200, {"ok": True, **result})
            except Exception as exc:
                self._service_error(exc, "DAY_CONTEXT_REJECTED")
        elif path.startswith("/api/v1/universes/"):
            self._universe_mutation(path, payload)
        elif path.startswith("/api/v1/attachments/"):
            self._attachment_mutation(path, payload)
        elif path == "/api/v1/upload":
            self._upload(payload)
        else:
            self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")

    def _universe_mutation(self, path: str, payload: dict) -> None:
        parts = [unquote(part) for part in path[len("/api/v1/universes/") :].split("/") if part]
        if len(parts) < 2:
            self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")
            return
        universe_id = parts[0]
        action = "/".join(parts[1:])
        try:
            if action == "relink/preview":
                self._json(200, {"ok": True, "preview": self.app.service.relink_preview(universe_id)})
            elif action == "relink/confirm":
                self._json(200, {"ok": True, **self.app.service.relink_confirm(universe_id, payload)})
            elif action == "export":
                self._json(200, {"ok": True, "export": self.app.service.export_universe(universe_id)})
            else:
                self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")
        except Exception as exc:
            self._service_error(exc, "UNIVERSE_REJECTED")

    def _attachment_mutation(self, path: str, payload: dict) -> None:
        action = path[len("/api/v1/attachments/") :].strip("/")
        handlers = {
            "analyze-local": ("analyze_attachments", "ATTACHMENT_ANALYZE_REJECTED"),
            "propose-application": ("propose_attachment", "ATTACHMENT_PROPOSE_REJECTED"),
            "commit": ("commit_attachment", "ATTACHMENT_COMMIT_REJECTED"),
            "discard": ("discard_attachment", "ATTACHMENT_DISCARD_REJECTED"),
            "analyze-llm": ("analyze_attachment_llm", "ATTACHMENT_LLM_REJECTED"),
        }
        if action not in handlers:
            self._error(404, "NOT_FOUND", "API 경로를 찾지 못했습니다.")
            return
        method_name, code = handlers[action]
        # Analysis and discard persist review metadata, and optional vision
        # starts inference. Each service writer enforces the shared live guard;
        # propose-application alone is a non-persisting review preview.
        try:
            result = getattr(self.app.service, method_name)(payload)
            self._json(200, {"ok": True, **result})
        except FileNotFoundError as exc:
            self._error(404, "ATTACHMENT_NOT_FOUND", str(exc))
        except Exception as exc:
            self._service_error(exc, code)

    def _start_job(self, kind: str, worker, payload: dict) -> None:
        guard = getattr(self.app.service, "assert_mutable", None)
        if callable(guard):
            try:
                guard()
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code in ("WORLD_READ_ONLY", "WORLD_BINDING_CHANGED"):
                    # Refuse before any job exists (master plan 17.5).
                    self._error(409, code, str(exc))
                    return
                # Every other failure (for example a missing save) is reported
                # by the job itself exactly as before.
        try:
            job = self.app.jobs.start(kind, worker, payload)
            self._json(202, {"ok": True, "job": self._public_job(job)})
        except JobConflictError as exc:
            self._error(
                409,
                "JOB_RUNNING",
                "이미 다른 작업이 실행 중입니다.",
                job_id=str(exc),
            )

    def _upload(self, payload: dict) -> None:
        encoded = payload.get("data")
        if not isinstance(encoded, str):
            self._error(400, "IMAGE_REQUIRED", "이미지 데이터가 없습니다.")
            return
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            self._error(400, "IMAGE_DECODE_FAILED", "이미지를 해석하지 못했습니다.")
            return
        if not data or len(data) > 12 * 1024 * 1024:
            self._error(400, "IMAGE_SIZE_REJECTED", "이미지는 12MB 이하여야 합니다.")
            return
        extension = _image_extension(data)
        if not extension:
            self._error(400, "IMAGE_TYPE_REJECTED", "지원하지 않는 이미지 형식입니다.")
            return
        config = config_module.load()
        directory = Path(config["shots_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()[:10]
        name = f"upload-{time.strftime('%Y%m%d-%H%M%S')}-{digest}{extension}"
        target = directory / name
        # Uploads never keep camera/EXIF/XMP segments (master plan 8.12).
        atomic_write_bytes(target, attachments_module.strip_jpeg_metadata(data))
        self._json(
            200,
            {
                "ok": True,
                "image": {
                    "name": name,
                    "bytes": len(data),
                    "provenance": "visual_hint",
                },
            },
        )
