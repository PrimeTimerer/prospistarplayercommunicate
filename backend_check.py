"""Opt-in packaged backend verification using an isolated synthetic world.

This is a release diagnostic, not a headless personal-profile app mode. It
starts no GUI, hotkeys, OCR, model, or game. Only its owned loopback listener
may receive a connection. All game/profile input is synthetic and temporary.
The report is create-only; the existing desktop entry point is unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def _exercise(root: Path, report: dict) -> None:
    # Imports occur only after main() gives this process a disposable profile.
    import attachments
    import editorial_engine
    import service
    from ledger_v2 import Ledger
    from ui_server import LocalAppServer, UI_ROOT

    config = {
        "output_dir": str(root / "output"), "shots_dir": str(root / "shots"),
        "data_dir": str(root / "data"), "ledger_path": None,
        "save_path": str(root / "StarPlayer.dat"), "heat": 7, "mode": "standard",
        "platforms": ["dc"], "persona": "", "auto_narrate": False,
        "auto_capture": False, "auto_open": False, "theme": "dark", "llm_launcher": None,
        "ai_provider": "local_auto", "gemini_model": service.gemini_provider.DEFAULT_MODEL,
        "gemini_consent": False,
    }
    Path(config["save_path"]).write_bytes(b"SYNTHETIC BACKEND CHECK - NOT A GAME SAVE")
    Path(config["shots_dir"]).mkdir()
    snapshot = {
        "player": {"id": 990001, "name": "검증 주인공", "team": "검증 구단", "pos": "투수"},
        "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
        "stats": {"pit_IP": 100, "pit_TBF": 370, "pit_H": 30, "pit_K": 200,
                  "pit_W": 12, "bat_AB": 180, "bat_H": 70, "bat_HR": 30, "bat_RBI": 65,
                  "bat_SB": 5, "bat_AVG": 70 / 180},
        "content_hash": "synthetic-interaction-initial", "profile_fingerprint": "synthetic-only",
        "source": {"file": "StarPlayer.dat", "slot": "00"},
        "validation": {"container": "verified", "verified_chunks": 51, "chunk_count": 51},
        "provenance": {"diagnostic_fixture": True},
    }
    endpoint = []
    original_connect = socket.socket.connect

    def only_owned_listener(sock, address):
        if not endpoint or tuple(address[:2]) != tuple(endpoint):
            raise AssertionError("The diagnostic attempted a connection outside its owned listener")
        return original_connect(sock, address)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GUI, OCR, subprocess, or LLM execution is forbidden in this diagnostic")

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    with ExitStack() as stack:
        for target, name, kwargs in [
            (service.config_module, "load_with_diagnostics", {"return_value": (config, {})}),
            (service.config_module, "load", {"return_value": config}),
            (service.config_module, "autodetect_saves", {"return_value": []}),
            (service.StarModeService, "_read_snapshot", {"side_effect": lambda _path: copy.deepcopy(snapshot)}),
            (service, "_llm_reachable", {"return_value": False}),
            (service, "_probe_llm_ports", {"return_value": False}),
            (service.narration_module, "pick_endpoint", {"return_value": (None, None)}),
            (service.narration_module, "narrate", {"side_effect": forbidden}),
            (service.narration_module, "story_chat", {"side_effect": forbidden}),
            (service.narration_module, "director_chat", {"side_effect": forbidden}),
            (service.gemini_provider, "generate_text", {"side_effect": forbidden}),
            (service.gemini_provider, "generate_vision", {"side_effect": forbidden}),
            (service.gemini_provider, "test_connection", {"side_effect": forbidden}),
            (attachments, "_run_script", {"side_effect": forbidden}),
            (attachments, "ocr_status", {"return_value": {"available": False, "languages": [], "engine": None, "error": "synthetic diagnostic"}}),
            (subprocess, "Popen", {"side_effect": forbidden}),
            (socket.socket, "connect", {"new": only_owned_listener}),
        ]:
            stack.enter_context(patch.object(target, name, **kwargs))
        server = LocalAppServer()
        origin = server.start()
        endpoint[:] = ["127.0.0.1", server.httpd.server_address[1]]
        report["owned_listener"] = origin
        thread = server.thread
        opener = build_opener(ProxyHandler({}))

        def request(path, payload=None, *, status=200, token=None):
            headers = {"Origin": origin, "X-StarMode-Token": server.csrf_token if token is None else token}
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                response = opener.open(Request(origin + path, data=body, headers=headers), timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                content = response.read()
                if response.code != status:
                    raise AssertionError(f"{path}: expected {status}, got {response.code}: {content[:300]!r}")
                return json.loads(content) if path.startswith("/api/") else content

        def confirm(response):
            turn = response["turn"]
            return request("/api/v1/story/chat", {"confirm_turn_id": turn["turn_id"], "confirm_proposal_id": turn["proposed_events"][0]["proposal_id"]})

        samples = report["samples"] = {}
        try:
            check("owned health", request("/api/v1/health")["ok"])
            check("packaged UI served", b"storyInteractionPanel" in request("/"))
            server.service.check_save(lambda *_args: None)
            samples["initial"] = request("/api/v1/bootstrap")["dashboard"]
            check(
                "runtime product version",
                samples["initial"]["app"]["version"] == service.APP_VERSION,
            )
            check("optional remote provider defaults off", samples["initial"]["config"]["ai_provider"] == "local_auto"
                  and not samples["initial"]["config"]["gemini_consent"]
                  and not samples["initial"]["config"]["gemini_key_present"])
            catalog = samples["initial"]["story"]["catalog"]
            check("four additive actions and 28 legacy choices", len(catalog["interactions"]["actions"]) == 4 and sum(len(c["situations"]) for c in catalog["categories"]) == 28)
            request("/api/v1/story/event", {}, status=403, token="not-authorized")
            check("CSRF retained", True)
            displayed_origin = {
                "universe_id": samples["initial"]["world"]["world_id"],
                "protagonist_id": str(samples["initial"]["player"]["id"]),
                "game_date": samples["initial"]["story"]["game_date"],
            }
            context_result = request("/api/v1/worldbook/context", {
                "basis": "user_preference", "label": "햄버거",
                "detail": "후배와 나누는 비공개 메뉴", "visibility": "private",
                "world_origin": displayed_origin,
            })
            counterpart_result = request("/api/v1/worldbook/counterpart", {
                "canonical_name": "검증 후배", "role": "rookie", "aliases": "막내",
                "note": "같은 세계선에서 다시 만날 후배", "world_origin": displayed_origin,
            })
            samples["worldbook"] = counterpart_result["dashboard"]
            counterpart_id = counterpart_result["item"]["entity_id"]
            check("worldbook is world-local and provenance-separated",
                  context_result["item"]["evidence_class"] == "user_confirmed"
                  and context_result["dashboard"]["story"]["personal_context"]["active_count"] == 1
                  and counterpart_result["dashboard"]["story"]["counterparts"][0]["entity_id"] == counterpart_id)
            start = request("/api/v1/story/event", {
                "category": "starplayer", "situation": "player_exchange", "target": "rookie",
                "participant_entity_id": counterpart_id, "participant_name": "검증 후배",
                "interaction_place": "비공개 검증 가게",
                "interaction_topic": "햄버거", "visibility": "private",
            })
            samples["started"] = start
            ident = start["interaction_id"]
            world_id = start["dashboard"]["world"]["world_id"]
            ledger_path = Path(config["data_dir"]) / "worlds" / world_id / "ledger.json"

            def read_state():
                return Ledger(str(ledger_path), world_id, read_only=True).state

            def chat(text):
                return request("/api/v1/story/chat", {"interaction_id": ident, "user_text": text, "renderer_preference": "deterministic"})

            before = copy.deepcopy(read_state()["star_interactions"])
            preview = chat("햄버거 값은 삼진으로 계산하자 ㅋㅋ")
            samples["preview"] = preview
            check("preview does not commit", before == read_state()["star_interactions"])
            samples["confirmed"] = confirm(preview)
            check("private scene has no public reactions", not any(samples["confirmed"]["item"]["reactions"].get(key) for key in ("boards", "media", "waves", "foreign")))
            check("private player context enriches the bound scene",
                  "후배와 나누는 비공개 메뉴" in samples["confirmed"]["item"]["scene"]["response"])
            stale = chat("다음 홈런 치면 햄버거 사줄게 약속")
            samples["stale"] = confirm(chat("그건 싫어"))
            turn = stale["turn"]
            request("/api/v1/story/chat", {"confirm_turn_id": turn["turn_id"], "confirm_proposal_id": turn["proposed_events"][0]["proposal_id"]}, status=400)
            check("stale confirmation rejected", read_state()["star_interactions"][ident]["promise"] is None)
            samples["promised"] = confirm(chat("다음 홈런 치면 햄버거 사줄게 약속"))
            samples["share_preview"] = chat("기자에게 공개해서 말할래")
            samples["shared"] = confirm(samples["share_preview"])
            public = samples["shared"]["item"]["reactions"]
            public_text = json.dumps(public, ensure_ascii=False)
            check("explicit publication has no private name/place/topic", bool(public.get("media")) and all(word not in public_text for word in ("검증 후배", "비공개 검증 가게", "햄버거")))
            check("private player context does not enter a public projection",
                  "후배와 나누는 비공개 메뉴" not in samples["shared"]["item"]["scene"]["response"])
            check("publication keeps encounter private", read_state()["star_interactions"][ident]["visibility"] == "private")
            snapshot["date"]["day"] = 25
            snapshot["content_hash"] = "synthetic-next-home-run"
            for key, value in (("bat_AB", 4), ("bat_H", 2), ("bat_HR", 1), ("bat_RBI", 2)):
                snapshot["stats"][key] += value
            samples["next_day"] = server.service.check_save(lambda *_args: None)
            state = read_state()
            sealed = copy.deepcopy(state["story_sessions"]["2027-07-24"])
            callbacks = [row for row in state["star_interaction_events"] if row["beat"] == "callback"]
            check("one verified-game reminder, not fulfillment", len(callbacks) == 1 and state["star_interactions"][ident]["promise"]["status"] == "reminded")
            server.service.check_save(lambda *_args: None)
            check("same snapshot cannot repeat reminder", len([row for row in read_state()["star_interaction_events"] if row["beat"] == "callback"]) == 1)
            samples["resumed"] = confirm(chat("어제 이야기 다시 이어가자"))
            check("sealed day remains unchanged", sealed == read_state()["story_sessions"]["2027-07-24"])
            server.service = service.StarModeService()
            samples["restart"] = request("/api/v1/bootstrap")["dashboard"]
            check("restart retains encounter", any(row["interaction_id"] == ident for row in samples["restart"]["story"]["interactions"]))
            check("frozen preview retains role-specific wording", samples["preview"]["response"]["reply"]["blocks"][1:] == samples["confirmed"]["item"]["scene"]["blocks"]
                  and "/hamburger/rookie/" in samples["confirmed"]["item"]["narrative_provenance"]["expression"]["selections"]["reply"])
            remembered = read_state()["community_memory"][editorial_engine.MEMORY_KEY]["articles"]
            check("public editorial metadata survives the story commit", bool(public.get("editorial"))
                  and {article["id"] for article in public["media"]} <= {article["id"] for article in remembered})
            confirm(chat("그 생각에는 반대야"))
            samples["emotional_joke"] = confirm(chat("그래도 농담을 해 볼게"))
            check("a joke does not erase conflict", samples["emotional_joke"]["item"]["narrative_provenance"]["expression"]["mood"] == "strained"
                  and read_state()["star_interactions"][ident]["emotional_continuity"]["tension"] > 0)
            repair = confirm(chat("내가 지나쳤어. 미안해"))
            agreement = confirm(chat("좋아, 그 부분은 동의해"))
            check("repair and agreement remain separate steps", repair["item"]["narrative_provenance"]["expression"]["mood"] == "repairing"
                  and not read_state()["star_interactions"][ident]["emotional_continuity"]["repair_pending"]
                  and agreement["item"]["narrative_provenance"]["expression"]["mood"] == "warm")
            before = read_state()
            denied, question = chat("약속은 취소하지 마"), chat("약속 지켰어?")
            check("negation and recall cannot cancel or fulfill the pending promise", not denied["turn"]["proposed_events"]
                  and not question["turn"]["proposed_events"] and before["star_interactions"] == read_state()["star_interactions"])
            samples["composite_preview"] = chat("기자에게 말하지 말고 농담하자")
            samples["composite_confirmed"] = confirm(samples["composite_preview"])
            check("private composite selects only its affirmative clause", read_state()["star_interaction_events"][-1]["beat"] == "joke"
                  and read_state()["star_interaction_events"][-1]["line"] == "농담하자"
                  and not samples["composite_confirmed"]["item"]["reactions"]["media"])
            before = read_state()
            samples["ambiguous"] = chat("농담도 하고 약속도 할래")
            check("multiple actions remain clarification-only", not samples["ambiguous"]["turn"]["proposed_events"]
                  and before["star_interaction_events"] == read_state()["star_interaction_events"])
            negative_prop = chat("이걸 소재로 만들지 마")
            check("a negated material command cannot bypass the bound parser", not negative_prop["turn"]["proposed_events"]
                  and before["narrative_props"] == read_state()["narrative_props"])
            desk = request("/api/v1/story-desk")["story_desk"]
            check("packaged director catalog and opt-in reference pack", len(desk["catalog"]["actions"]) == 71
                  and len(desk["reference_pack"]["items"]) == 12 and not desk["turns"])
            job = request("/api/v1/jobs/director", {"desk_origin": desk["origin"],
                "request_id": "packaged-director-0001", "text": "햄버거 약속을 떠올리며 삼촌에게 농담한다.",
                "provider": "note"}, status=202)["job"]
            deadline = time.monotonic() + 10
            while job["status"] in ("queued", "running") and time.monotonic() < deadline:
                time.sleep(.02)
                job = request(f"/api/v1/jobs/{job['id']}")["job"]
            check("packaged no-model director job", job["status"] == "completed"
                  and job["result"]["story_desk"]["total"] == 1
                  and not job["result"]["story_desk"]["turns"][0]["model"])
            desk = job["result"]["story_desk"]
            memory = request("/api/v1/story-desk/memory", {"desk_origin":desk["origin"],
                "label":"가족의 회계 농담", "detail":"가상 삼촌은 영수증부터 찾는다.", "kind":"motif"})["dashboard"]["story_desk"]
            check("packaged memory remains local by default", memory["memories"][0]["remote_allowed"] is False)
            capsule = request(f"/api/v1/history/{desk['origin']['game_date']}")["capsule"]
            check("packaged historical desk replay", len(capsule["story_desk"]["turns"]) == 1
                  and len(capsule["story_desk"]["memories"]) == 1)
            samples["director"] = {"catalog_size":71, "turns":capsule["story_desk"]["turns"], "memories":capsule["story_desk"]["memories"]}
            desk_origin = request("/api/v1/story-desk")["story_desk"]["origin"]
            job = request("/api/v1/jobs/director", {"desk_origin":desk_origin, "request_id":"packaged-article-0001",
                "channel":"article", "visibility":"public", "text":"오늘 인터뷰를 바탕으로 후속 보도 방향을 기록한다.", "provider":"note"}, status=202)["job"]
            deadline = time.monotonic() + 10
            while job["status"] in ("queued", "running") and time.monotonic() < deadline:
                time.sleep(.02); job = request(f"/api/v1/jobs/{job['id']}")["job"]
            check("packaged editorial note keeps story channel separate", job["status"] == "completed"
                and job["result"]["editorial_desk"]["article"]["total"] == 1 and job["result"]["story_desk"]["total"] == 1)
            sources = request("/api/v1/editorial/sources?channel=article")["sources"]
            check("packaged public source filter excludes private director input", any(r["id"].startswith("desk:") for r in sources)
                and all(r["visibility"] in ("public", "social", "national", "international", "community") for r in sources))
            period = request("/api/v1/chronicle?kind=month&key=2027-07")["chronicle"]
            check("packaged monthly plan reads without a model", period["pending_chunks"] > 0 and not period["checkpoints"])
            context = request("/api/v1/editorial/context", {"chronicle_origin":period["origin"], "previous_team":"직전 검증팀", "upcoming_team":"다음 검증팀", "people":[]})
            check("packaged opponent context persists only in current world", context["dashboard"]["chronicle"]["context"]["previous_team"] == "직전 검증팀"
                and context["dashboard"]["chronicle"]["context"]["remote_allowed"] is False)
            check("no GUI or hotkeys loaded", "webview" not in sys.modules and "app_shell" not in sys.modules and server.capture_hotkeys is None)
            assets = [path for path in UI_ROOT.rglob("*") if path.is_file()]
            for directory in ("editorial", "interactions", "story"):
                assets.extend((UI_ROOT.parent / "data" / directory).glob("*.json"))
            report["assets"] = {path.relative_to(UI_ROOT.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
        finally:
            server.stop()
            report["normal_shutdown"] = not thread.is_alive() and server.httpd is None
        check("owned backend shut down normally", report["normal_shutdown"])


def main(report_path: str) -> int:
    target = Path(report_path)
    if not target.is_absolute() or target.suffix.lower() != ".json" or not target.parent.is_dir() or target.exists():
        return 64
    # Reserve a new report before doing work; never replace an existing file.
    with target.open("x", encoding="utf-8") as output:
        started = time.monotonic()
        report = {"mode": "synthetic-packaged-backend", "frozen": bool(getattr(sys, "frozen", False)),
                  "pid": os.getpid(), "checks": [], "ok": False,
                  "limits": ["No real-save parser verification", "No native-window or user-game behavior verification", "Synthetic snapshots only; no personal profile input"]}
        try:
            with tempfile.TemporaryDirectory(prefix="StarModeFeed-backend-check-") as temp:
                with patch.dict(os.environ, {"LOCALAPPDATA": temp, "APPDATA": temp}):
                    try:
                        _exercise(Path(temp), report)
                    finally:
                        # Windows cannot remove an open rotating-log file.
                        for handler in list(logging.getLogger("starmodefeed").handlers):
                            logging.getLogger("starmodefeed").removeHandler(handler)
                            handler.close()
            report["ok"] = all(row["passed"] for row in report["checks"])
        except Exception:
            report["error"] = traceback.format_exc()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return 0 if report["ok"] else 1
