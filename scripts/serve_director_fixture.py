"""Serve real desk routes with disposable data and mocked providers for UI QA."""
import copy
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def main():
    with tempfile.TemporaryDirectory(prefix="StarModeFeed-director-ui-") as temp, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA":temp, "APPDATA":temp}))
        import attachments
        import config
        import narrate
        import service
        import story_desk as desk
        from test_story_service import StoryServiceTests
        from ledger_v2 import Ledger
        from ui_server import LocalAppServer
        snap, ledger, event, cfg, world = StoryServiceTests()._fixture(temp)
        cfg.update(data_dir=str(Path(temp) / "data"), gemini_consent=True, gemini_model="gemini-3.5-flash-lite", ai_provider="gemini")
        Path(cfg["shots_dir"]).mkdir()
        snap["player"]["name"] = "서사 검증 선수"
        state = {"reachable": True}
        def generate(system, user, **options):
            packet = json.loads(user)
            if "실패 검증" in packet.get("user_direction", ""):
                raise RuntimeError("합성 모델 실패 · 기존 대화 유지")
            if "changed_material_only" in packet:
                return "## 이어지는 하루\n\n" + "\n\n".join(str(row.get("text"))[:120] for row in packet["changed_material_only"]) + "\n\n## 이어갈 핵심\n\n기사와 팬의 대화가 햄버거 약속으로 이어졌다.", "synthetic-local"
            return "## 치즈버거 약속\n\n동료가 웃으며 고개를 끄덕였다.\n\n다음 원정에서는 함께 가기로 했다.\n\n## 복도의 농담\n\n가상 OB가 식당 지도부터 펼쳤다.", "synthetic-local"
        server = LocalAppServer()
        server.service._prepare = lambda update=None: (cfg, {}, None, Ledger(ledger.path, world["world_id"]), world, event)
        server.service.rescan_saves = lambda: []
        server.service.list_universes = lambda: {"universes":[]}
        server.service._universe_ledger = lambda _cfg, ident: (Ledger(ledger.path, ident, read_only=True), {"state":"preserved_read_only"})
        server.service._secret_store = Mock(return_value=Mock(get_gemini_key=Mock(return_value=("fake-fixture", "test"))))
        server.service._record_gemini_generation = Mock()
        server.service._record_gemini_failure = Mock()
        server.service.auto_activate_gemini = lambda: {"ok":True, "connected":True, "cached":True, "config":public_config(cfg)}
        def public_config(value):
            return {**value, "save_exists":True, "gemini_key_present":True, "gemini_models":[cfg["gemini_model"]], "gemini_usage":{}}
        def remote(system, user, **options):
            text, _model = generate(system, user, **options)
            return Mock(text=text, model=cfg["gemini_model"])
        def fail_network(*args, **kwargs):
            raise AssertionError("Fixture tried an outbound connection")
        for target, name, value in (
            (config, "load", lambda:cfg), (config, "public_config", public_config),
            (service, "_llm_reachable", lambda:state["reachable"]),
            (service, "_probe_llm_ports", lambda:state["reachable"]),
            (narrate, "director_chat", generate), (service.gemini_provider, "generate_text", remote),
            (attachments, "ocr_status", lambda:{"available":False}),
            (socket.socket, "connect", fail_network),
        ):
            stack.enter_context(patch.object(target, name, new=value))
        for i in range(35):
            context = desk.origin(ledger.state, snap, world)
            choice = desk.normalize({"request_id":f"fixture-turn-{i:04}", "text":f"함께 햄버거를 먹은 장면 {i}", "allow_remote_recall":True})
            desk.append_turn(ledger.state, snap, context, choice, "## 오래된 약속\n\n다음에도 같이 가기로 했다.", "synthetic-local", [], {})
        ledger.save()
        url = server.start()
        print(json.dumps({"url":url}), flush=True)
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "quit": break
                if command == "offline": state["reachable"] = False
                if command == "new-world":
                    world["world_id"] = "world-two"; snap["player"]["id"] = 8; snap["player"]["name"] = "다른 검증 선수"
                if command == "original-world":
                    world["world_id"] = "world-one"; snap["player"]["id"] = 7; snap["player"]["name"] = "서사 검증 선수"
                print(json.dumps({"command":command}), flush=True)
        finally:
            server.stop()
            for handler in list(logging.getLogger("starmodefeed").handlers):
                logging.getLogger("starmodefeed").removeHandler(handler); handler.close()


if __name__ == "__main__":
    main()
