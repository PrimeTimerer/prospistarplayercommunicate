"""Additive service adapter for the director desk; existing providers stay intact."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import attachments
import career_engine
import config as config_module
import gemini_provider
import memory_windows
import narrate
import personal_context
import story_desk as desk
import spotlight_engine


class StoryDeskServiceMixin:
    def read_story_desk(self, *, before=None, day=None, channel="story"):
        with self._state_lock:
            _config, _diagnostics, _store, ledger, world, event = self._prepare()
            if channel not in {"story", "article", "community"}:
                raise ValueError("대화 채널을 확인해 주세요.")
            return desk.view(ledger.state, event["snapshot"], world, before=before, day=day, channel=channel)

    def _desk_context(self, state, snapshot, world, selection, config):
        context = desk.origin(state, snapshot, world)
        remote = selection["provider"] == "gemini"
        public = selection.get("channel", "story") in {"article", "community"}
        personal = personal_context.prompt_section(state, universe_id=context["world_id"],
            protagonist_id=snapshot["player"]["id"], game_date=context["game_date"], remote=remote,
            **({"audience": "public"} if public else {}))
        packet = memory_windows.prompt_packet(memory_windows.build_view(state,
            universe_id=context["world_id"], protagonist_id=snapshot["player"]["id"],
            as_of_date=context["game_date"], remote=remote or public), query=selection["text"])
        career = career_engine.build_view(state, snapshot)
        attention = spotlight_engine.evaluate(snapshot, career, {"kind": "NO_CHANGE"}, config)
        result = desk.prompt(state, snapshot, context, selection, personal_text=personal,
            memory_text=json.dumps(packet, ensure_ascii=False),
            spotlight={"score": attention["score"], "label": attention["label"],
                "drivers": attention["drivers"], "player_grade": career["player_grade"],
                "honor_summary": career["honor_summary"], "tracking": career["tracking"],
                "honors": [{key: row.get(key) for key in ("title", "season_year", "occurred_on", "provenance")}
                           for row in career["honors"][:12]],
                "language_level": config.get("community_language_level", 2), "heat": config.get("heat", 7)})
        return self._connected_prompt(result, state, snapshot, world, selection, config)

    def _desk_images(self, config, selection):
        parts, refs = [], []
        total = 0
        for name in selection["images"]:
            path = self._capture_path(config, name)
            if not path.is_file() or path.stat().st_size > 12 * 1024 * 1024:
                raise ValueError("선택한 이미지를 찾지 못했거나 12MB를 넘었습니다.")
            original = path.read_bytes()
            raw = attachments.strip_jpeg_metadata(original)
            image_type = attachments.sniff_image(raw)
            mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(image_type)
            if not mime:
                raise ValueError("서사 첨부는 PNG, JPEG, WebP를 사용해 주세요.")
            total += len(raw)
            if total > 12 * 1024 * 1024:
                raise ValueError("서사 이미지 합계는 12MB 이하여야 합니다.")
            fingerprint = hashlib.sha256(raw).hexdigest()
            parts.append({"data": raw, "mime": mime})
            refs.append({"name": name, "sha256": fingerprint, "original_sha256": hashlib.sha256(original).hexdigest(),
                         "file": f"{fingerprint}.{image_type}", "bytes": len(raw), "provenance": "visual_hint"})
        return parts, refs

    def _desk_save_images(self, config, world, parts, refs):
        directory = self._world_output_dir(config, world["world_id"]) / "story-images"
        for part, ref in zip(parts, refs):
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / ref["file"]
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != ref["sha256"]:
                    raise ValueError("보관 이미지 무결성을 확인하지 못했습니다. 기존 파일을 덮어쓰지 않았습니다.")
                continue
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".story-", delete=False) as temp:
                temp.write(part["data"])
                temp.flush()
                os.fsync(temp.fileno())
                staged = Path(temp.name)
            try:
                os.replace(staged, target)
            finally:
                if staged.exists():
                    staged.unlink()

    def read_story_image(self, name, *, universe_id=None):
        if not re.fullmatch(r"[a-f0-9]{64}\.(png|jpg|webp)", str(name)):
            raise ValueError("서사 이미지 식별자가 올바르지 않습니다.")
        with self._state_lock:
            if universe_id is not None:
                config = config_module.load()
                ledger, _row = self._universe_ledger(config, universe_id)
                world = {"world_id": str(universe_id)}
                snapshot = ledger.state.get("last_snapshot")
                if not isinstance(snapshot, dict):
                    raise FileNotFoundError("보존된 선수의 마지막 스냅샷이 없습니다.")
            else:
                config, _diagnostics, _store, ledger, world, event = self._prepare()
                snapshot = event["snapshot"]
            # Archive viewing is not prompt recall: a later archived day remains
            # readable even when the game has since loaded an earlier snapshot.
            allowed = {ref["file"] for turn in (ledger.state.get(desk.STORE) or {}).get("turns", [])
                       if turn.get("world_id") == str(world["world_id"])
                       and str(turn.get("player_id")) == str(snapshot["player"]["id"])
                       for ref in turn.get("images", [])}
            if name not in allowed:
                raise FileNotFoundError("현재 세계선에서 보관한 이미지가 아닙니다.")
            target = self._world_output_dir(config, world["world_id"]) / "story-images" / name
            if not target.is_file():
                raise FileNotFoundError("보관 이미지를 찾지 못했습니다.")
            return target

    def write_story_memory(self, payload):
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            context = desk.origin(ledger.state, event["snapshot"], world)
            desk.require_origin(payload, context)
            candidate = copy.deepcopy(ledger.state)
            operation = str(payload.get("operation") or "save")
            if operation == "retire":
                desk.retire_memory(candidate, str(payload.get("id") or ""), context)
            elif operation == "import_reference":
                requested = payload.get("ids")
                if not isinstance(requested, list) or not requested or len(set(requested)) != len(requested):
                    raise ValueError("가져올 참고 메모를 선택해 주세요.")
                lookup = {row["id"]: row for row in desk.reference_pack()["items"]}
                if any(value not in lookup for value in requested):
                    raise ValueError("참고 메모 항목을 확인해 주세요.")
                for value in requested:
                    item = lookup[value]
                    # No source character's canon is silently injected into another world.
                    desk.add_memory(candidate, {**item, "id": None,
                        "remote_allowed": payload.get("remote_allowed") is True, "pinned": False}, context)
            elif operation == "save":
                desk.add_memory(candidate, payload, context)
            else:
                raise ValueError("기억 작업을 확인해 주세요.")
            original = ledger.state
            ledger.state = candidate
            try:
                ledger._remember_day_snapshot(event["snapshot"])
                ledger.save()
            except Exception:
                ledger.state = original
                raise
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"dashboard": dashboard}

    def write_director_turn(self, update, options=None):
        options = options or {}
        selection = desk.normalize(options)
        channel_label = {"article": "기사", "community": "커뮤니티"}.get(selection.get("channel"), "서사")
        with self._state_lock:
            config, _diagnostics, _store, ledger, world, event = self._prepare(update)
            self._require_live(config, world)
            snapshot = event["snapshot"]
            context = desk.origin(ledger.state, snapshot, world)
            existing = desk.duplicate(ledger.state, selection, context)
            if existing:
                return self._dashboard(config, _diagnostics, ledger, world, event,
                    run={"message": "이미 저장한 장면입니다. 다시 호출하지 않았습니다.", "director_request_id": selection["request_id"], "director_channel": selection.get("channel", "story")})
            desk.require_origin(options, context)
            save_context = self._story_context(config, world, snapshot, context["game_date"])
            parts, refs = self._desk_images(config, selection)
            system, user, recall = self._desk_context(ledger.state, snapshot, world, selection, config)
            prompt_hash = desk.digest([system, user])
            provider = selection["provider"]
            if provider == "gemini":
                if config.get("gemini_consent") is not True or options.get("remote_consent") is not True:
                    raise ValueError("Gemini 전송 동의가 필요합니다. 선택한 장면·허용 기억·이미지만 전송합니다.")
                api_key, _key_source = self._secret_store().get_gemini_key()
                if not api_key:
                    raise ValueError("설정에서 Gemini API 키를 저장해 주세요.")
        update(f"최근·관련 장면 {len(recall['turn_ids'])}개, 고정 기억 {len(recall['memory_ids'])}개 · 연결 자료 {len(recall.get('source_ids', []))}개 · 선택 이미지 {len(refs)}장", 20, f"{channel_label} 문맥 준비")
        check = getattr(update, "check_cancelled", None)
        if check:
            check()
        if provider == "note":
            reply, model = selection["text"], None
            recall = {"turn_ids": [], "memory_ids": [], "remote": False, "images_interpreted": False}
        elif provider == "local":
            update("실행 중인 로컬 모델에 선택한 문맥을 전달합니다. 서버는 자동으로 켜지지 않습니다.", 35, "로컬 서사 작성")
            reply, model = narrate.director_chat(system, user, image_parts=parts,
                max_tokens=desk.LENGTHS[selection["length"]], check_cancelled=check)
            reply = desk.validate_reply(reply)
        else:
            update("선택한 장면과 전송 허용된 기억·이미지만 Gemini에 전달합니다.", 35, "Gemini 서사 작성")
            try:
                result = gemini_provider.generate_text(system, user, api_key=api_key, model=config.get("gemini_model"),
                    max_tokens=desk.LENGTHS[selection["length"]], image_parts=parts,
                    check_cancelled=check, validate_korean_and_numbers=False)
            except gemini_provider.GeminiProviderError as exc:
                self._record_gemini_failure(config, exc, "director_chat")
                raise
            self._record_gemini_generation(config, result, "director_chat")
            reply, model = desk.validate_reply(result.text), f"gemini:{result.model}"
        if check:
            check()
        update("선수·날짜·이전 대화·기억이 그대로인지 확인한 뒤 원문을 저장합니다.", 85, "세계선 확인")
        gate = getattr(update, "commit", None)
        with gate() if callable(gate) else contextlib.nullcontext():
            with self._state_lock:
                current_config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(current_config, world)
                desk.require_origin(options, desk.origin(ledger.state, event["snapshot"], world))
                self._require_chat_origin(current_config, world, event["snapshot"], save_context)
                new_system, new_user, _recall = self._desk_context(ledger.state, event["snapshot"], world, selection, current_config)
                if desk.digest([new_system, new_user]) != prompt_hash:
                    raise ValueError("작성 중 인물·기억 문맥이 바뀌어 응답을 저장하지 않았습니다. 다시 시도해 주세요.")
                if provider == "gemini" and current_config.get("gemini_consent") is not True:
                    raise ValueError("외부 전송 동의가 해제되어 응답을 저장하지 않았습니다.")
                _current_parts, current_refs = self._desk_images(current_config, selection)
                if current_refs != refs:
                    raise ValueError("작성 중 선택 이미지가 바뀌었습니다. 응답을 저장하지 않았습니다.")
                candidate = copy.deepcopy(ledger.state)
                desk.append_turn(candidate, event["snapshot"], context, selection, reply, model, refs, recall)
                self._desk_save_images(current_config, world, parts, refs)
                original = ledger.state
                ledger.state = candidate
                try:
                    ledger._remember_day_snapshot(event["snapshot"])
                    ledger.save()
                except Exception:
                    ledger.state = original
                    raise
                dashboard = self._dashboard(current_config, diagnostics, ledger, world, event, run={
                    "message": f"{channel_label} 입력과 답변을 이 세계선에 저장했습니다." if model else "직접 쓴 내용을 저장했습니다. 모델 생성·이미지 해석은 하지 않았습니다.",
                    "director_request_id": selection["request_id"], "director_channel": selection.get("channel", "story")})
                self._write_dashboard(current_config, world, ledger, event, dashboard)
        update("원문과 선택 이미지 보관 완료 · 기존 기사·커뮤니티·검증 기록 유지", 100, "서사 저장 완료")
        return dashboard
