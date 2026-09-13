#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Application service layer for verified save checks and local presentation."""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import hashlib
import os
import secrets
import shutil
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import applog
import attachments as attachments_module
import briefing
import community
import community_style_db
import config as config_module
import event_ontology
import game_narrative
import editorial_engine
import gemini_provider
import memory_windows
import narrate as narration_module
import narrative_contracts
import narrative_engine
import narrative_props
import narrative_state
import personal_context
import presentation
import prose_format
import provider_feed
import provider_usage
import provider_expression
import realism_gate
from local_model_manager import LocalModelManager
from job_manager import JobCancelled
from product_version import APP_VERSION
import save_reader
import spotlight_engine
import stat_engine
import star_interactions
import interaction_intent
import story_engine
import story_desk
import chronicle
from story_desk_service import StoryDeskServiceMixin
from chronicle_service import ChronicleServiceMixin
import world_identity
from atomic_io import atomic_write_json, atomic_write_text, load_json
from ledger_v2 import Ledger
from universe_registry import (
    UniverseNotFoundError,
    UniverseRegistry,
    WorldBindingChangedError,
    WorldReadOnlyError,
)
from world_store import WorldStore
from secret_store import SecretStore

try:
    import capture as capture_module
except Exception:
    capture_module = None

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
MAX_NARRATIVE_IMAGES = 8
LLM_PROBE_TTL_SECONDS = 5.0
GEMINI_VERIFY_TTL_SECONDS = 24 * 60 * 60
GEMINI_FAILURE_COOLDOWN_SECONDS = 5 * 60

log = applog.get_logger(__name__)

# Sequential surface batches were the whole reason a feed run took minutes.
# The batches cover disjoint items and merge by identifier, so they are
# written concurrently against the remote provider. The local model stays
# sequential because one loopback server serves one request at a time.
REMOTE_BATCH_CONCURRENCY = 5


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _event_public(event: dict | None) -> dict | None:
    if not event:
        return None
    return {
        "kind": event.get("kind"),
        "delta": event.get("delta", {}),
        "milestones": event.get("milestones", []),
        "career_milestones": event.get("career_milestones", []),
        "role": event.get("role", "no_appearance"),
        "baseline_only": bool(event.get("baseline_only")),
        "game_lines": []
        if event.get("baseline_only")
        else stat_engine.describe_game(event.get("delta", {})),
    }


def _commit_gate(update: Callable | None):
    """Return the job commit gate for *update* or a no-op when not running as a job."""
    gate = getattr(update, "commit", None)
    return gate() if callable(gate) else contextlib.nullcontext()


_ATTACHMENT_REACTION_SCOPES = ("private", "community", "public")


def _attachment_value_phrase(row: dict) -> str:
    """Project one reviewed observation into bounded, human-readable Korean."""
    field = str(row.get("field") or "")
    label = str(row.get("label") or field or "확인 내용")
    value = row.get("value")
    if field == "game.result" and isinstance(value, dict):
        outcome = {"win": "승리", "loss": "패배", "tie": "무승부"}.get(str(value.get("outcome") or ""), "경기 종료")
        score = ""
        if value.get("runs_for") is not None and value.get("runs_against") is not None:
            score = f"{value['runs_for']}대 {value['runs_against']} "
        opponent = f", 상대 {value['opponent']}" if value.get("opponent") else ""
        return f"{label} {score}{outcome}{opponent}".strip()
    if isinstance(value, dict):
        if value.get("name"):
            return f"{label} {value['name']}"
        if value.get("count") is not None:
            return f"{label} {value['count']}개"
        return label
    if isinstance(value, list):
        compact = ", ".join(str(item) for item in value[:8] if item not in (None, ""))
        return f"{label} {compact}".strip()
    if value not in (None, ""):
        return f"{label} {value}"
    return label


def _attachment_reaction_scene(proposal: dict, player_name: str) -> tuple[str, str]:
    """Build the public scene only from choices the user elected to retain."""
    durable = [
        row for row in (proposal.get("proposals") or [])
        if row.get("target") in ("fact_registry", "narrative_prop")
    ]
    manual = next(
        (str(row.get("value") or "").strip() for row in durable if row.get("field") == "manual.description" and str(row.get("value") or "").strip()),
        "",
    )
    details = [
        _attachment_value_phrase(row)
        for row in durable
        if row.get("field") not in ("manual.description", "game.date")
    ][:4]
    if manual:
        summary = manual.rstrip(".。!? ") + "."
        if details:
            summary += " 함께 확인된 내용은 " + ", ".join(details) + "이다."
        label = manual.split(".", 1)[0].strip()
    elif details:
        summary = f"{player_name}과 관련된 장면에서 확인된 내용은 " + ", ".join(details) + "이다."
        label = details[0]
    else:
        raise ValueError("반응으로 확산할 장면 설명이나 확인 항목을 하나 이상 선택해 주세요.")
    summary = story_engine._clean_text(summary, required=True, limit=500)
    label = story_engine._clean_text(label, required=True, limit=72)
    audit = realism_gate.audit_public_prose(summary, protagonist_names=[player_name])
    if not audit.ok:
        reasons = ", ".join(dict.fromkeys(row.label for row in audit.violations))
        raise ValueError(f"공개 반응 설명을 현실 야구 문장으로 고쳐 주세요. 현재 표현: {reasons}")
    return summary, label


def _attachment_reaction_plan(config: dict, spotlight: dict, scope: str) -> tuple[dict, dict]:
    """Keep expression heat separate from stature- and mode-owned volume."""
    visibility = "social" if scope == "community" else "public"
    base = spotlight_engine.story_budget(spotlight, visibility)
    mode = str(config.get("mode") or "standard")
    boards = max(1, int(base.get("boards") or 0))
    media = 0 if scope == "community" else max(1, int(base.get("media") or 0))
    if mode == "quick":
        boards = 1
        media = min(media, 1)
        comments_per_board = 3
        minimum_social = 1
    elif mode == "explosion":
        boards = min(8, boards + 1)
        media = 0 if scope == "community" else min(6, media + 1)
        comments_per_board = 6
        minimum_social = 3
    else:
        comments_per_board = 4
        minimum_social = 2
    budget = {"boards": boards, "media": media, "comments": boards * comments_per_board}
    reaction_spotlight = copy.deepcopy(spotlight)
    reaction_spotlight.setdefault("reaction_budget", {})
    reaction_spotlight["reaction_budget"].update(
        {
            "mode": mode,
            "expression_heat": max(1, min(10, int(config.get("heat") or 7))),
            "minimum_social_posts": minimum_social,
        }
    )
    return budget, reaction_spotlight


def _build_attachment_reaction(
    *,
    config: dict,
    event: dict,
    spotlight: dict,
    memory: dict,
    universe_id: str,
    attachment_id: str,
    scope: str,
    scene_summary: str,
    scene_label: str,
    source_fact_ids: list[str],
) -> dict:
    """Create a deterministic, provider-ready reaction edition for one image review."""
    budget, reaction_spotlight = _attachment_reaction_plan(config, spotlight, scope)
    reaction_spotlight["drivers"] = [
        {
            "key": f"attachment:{attachment_id}",
            "label": scene_label,
            "points": 0,
            "provenance": "user_confirmed_attachment",
        },
        *(reaction_spotlight.get("drivers") or []),
    ]
    reaction_spotlight["memory_anchors"] = list(
        dict.fromkeys([scene_label, *(reaction_spotlight.get("memory_anchors") or [])])
    )[:8]
    story_visibility = "social" if scope == "community" else "public"
    story = {
        "visibility": story_visibility,
        "situation": "postgame_interview",
        "scene_summary": scene_summary,
        "scene_label": scene_label,
        "source_fact_ids": list(source_fact_ids),
    }
    publication = editorial_engine.build(
        event,
        budget=budget,
        memory=memory,
        universe_id=universe_id,
        story=story,
        story_id=f"attachment:{attachment_id}",
        spotlight=reaction_spotlight,
        platforms=config.get("platforms") or (),
        heat=int(config.get("heat") or 7),
        language_level=int(config.get("community_language_level") or 2),
    )
    publication = provider_feed.prepare_canonical(
        publication,
        event,
        reaction_spotlight,
        universe_id=universe_id,
        language_level=config.get("community_language_level"),
        protagonist_aliases=config.get("protagonist_aliases"),
    )
    if not publication.get("boards") or not publication.get("social"):
        raise RuntimeError("장면 반응을 안전하게 구성하지 못했습니다. 설명을 조금 더 구체적으로 적어 다시 시도해 주세요.")
    if scope == "public" and not publication.get("media"):
        raise RuntimeError("기사 반응을 안전하게 구성하지 못했습니다. 설명을 조금 더 구체적으로 적어 다시 시도해 주세요.")
    fact_refs = list(source_fact_ids) or [f"attachment:{attachment_id}"]
    player_name = str((event.get("snapshot", {}).get("player") or {}).get("name") or "선수")
    headline = f"{player_name} · {scene_label}"
    blocks = [
        narrative_contracts.block("eyebrow", "확인한 하루의 장면"),
        narrative_contracts.block("headline", headline),
        narrative_contracts.block("paragraph", scene_summary, fact_ids=fact_refs, evidence_class="fictional_intervention"),
        narrative_contracts.block(
            "fact_callout",
            "사용자가 이미지 검토 화면에서 공개 범위를 직접 선택했습니다.",
            label="공개 범위 확인",
            fact_ids=fact_refs,
            evidence_class="user_confirmed",
        ),
    ]
    counts = provider_feed.bundle_counts(publication)
    return {
        "event_type": "SP.MEDIA.FEATURE",
        "label": "확인한 장면의 공개 반응",
        "visibility": "local" if scope == "community" else "national",
        "headline": headline,
        "renderer": "deterministic",
        "blocks": blocks,
        "text": narrative_contracts.project_blocks(blocks),
        "reactions": {
            "boards": publication.get("boards") or [],
            "media": publication.get("media") or [],
            "social": publication.get("social") or [],
            "foreign": [],
            "waves": [],
            "editorial": publication.get("editorial") or {},
            "budget": {**budget, **counts, "scope": scope},
        },
        "audit": realism_gate.audit_feed(publication, names=[player_name]),
        "signature": {
            "template_id": "attachment-public-scene/1.0.0",
            "persona": "narrator",
            "opening": scene_summary[:80],
            "outline": scope,
            "skeleton": realism_gate.headline_skeleton(headline, [player_name]),
        },
        "provenance": {
            "renderer": "deterministic",
            "renderer_version": "attachment-public-scene/1.0.0",
            "source_attachment_id": attachment_id,
            "source_fact_ids": fact_refs,
            "provider_ready": True,
            "raw_image_in_feed": False,
        },
        "thread_type": "media_cycle",
    }


def _probe_llm_ports() -> bool:
    """Uncached loopback probe of the supported local model ports."""
    for port in (8082, 8081, 1234):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.12):
                return True
        except OSError:
            continue
    return False


_llm_probe_lock = threading.Lock()
_llm_probe_state = {"at": 0.0, "value": False}


def _llm_reachable() -> bool:
    """Probe result cached for a few seconds.

    Every dashboard build used to open three sockets; the gallery poll and
    every mutation rebuild the dashboard, so the probe ran several times a
    second. Paths that need an exact answer call ``_probe_llm_ports``.
    """
    now = time.monotonic()
    with _llm_probe_lock:
        if now - _llm_probe_state["at"] < LLM_PROBE_TTL_SECONDS:
            return bool(_llm_probe_state["value"])
    value = _probe_llm_ports()
    with _llm_probe_lock:
        _llm_probe_state["at"] = time.monotonic()
        _llm_probe_state["value"] = value
    return value


def _publish_llm_probe(value: bool) -> bool:
    """Publish an exact control-panel probe into the shared short cache."""

    with _llm_probe_lock:
        _llm_probe_state["at"] = time.monotonic()
        _llm_probe_state["value"] = bool(value)
    return bool(value)


class StarModeService(ChronicleServiceMixin, StoryDeskServiceMixin):
    def __init__(self):
        self._latest_dashboard: dict | None = None
        self._capture_lock = threading.Lock()
        self._hotkey_status_provider: Callable[[], dict] | None = None
        self._last_capture_event: dict | None = None
        self._save_scan_cache: dict[tuple[str, int, int], dict] = {}
        self._scan_lock = threading.Lock()
        # Serializes every use case that loads, mutates, and saves the world
        # ledger. The HTTP server handles each request on its own thread and
        # each request used to build a fresh Ledger from disk, so two
        # overlapping mutations lost one of their writes.
        self._state_lock = threading.RLock()
        # One decoded snapshot cached by (path, size, mtime_ns). Reads such as
        # opening an archive no longer decrypt the whole 50 MB save again.
        self._snapshot_lock = threading.Lock()
        self._snapshot_cache: tuple[tuple[str, int, int], dict] | None = None
        # Browser startup can race with a settings refresh. Only one metadata-
        # only Gemini probe is allowed at a time; no world-state lock is held
        # while the network request is in flight.
        self._gemini_probe_lock = threading.Lock()
        # The key fingerprint remains in memory only. Persisted usage metadata
        # never authorizes a new process, so each app launch performs one real
        # metadata probe while browser refreshes reuse the bounded result.
        self._gemini_session_probe: dict | None = None
        # A configured path is never launch consent. Only the explicit local
        # provider API may call this process owner, and it can stop only the
        # exact launcher tree created by this application instance.
        self._local_model = LocalModelManager(
            log_path=Path(config_module.CONFIG_PATH).resolve().parent / "logs" / "local-model-launcher.log"
        )

    def set_hotkey_status_provider(self, provider: Callable[[], dict] | None) -> None:
        self._hotkey_status_provider = provider

    def _world_store(self, config: dict) -> WorldStore:
        return WorldStore(config["data_dir"], legacy_ledger_path=config.get("ledger_path"))

    @staticmethod
    def _secret_store() -> SecretStore:
        return SecretStore(config_module.SECRETS_PATH)

    @staticmethod
    def _provider_usage_store(config: dict) -> provider_usage.ProviderUsageStore:
        return provider_usage.ProviderUsageStore(config_module.provider_usage_path(config))

    @classmethod
    def _record_gemini_generation(cls, config: dict, result, operation: str) -> None:
        try:
            cls._provider_usage_store(config).record_generation(result, operation=operation)
        except Exception as exc:  # pragma: no cover - advisory telemetry only
            log.warning("Gemini usage accounting failed: %s", exc)

    @classmethod
    def _record_gemini_failure(cls, config: dict, error: Exception, operation: str) -> None:
        try:
            cls._provider_usage_store(config).record_failure(
                operation=operation,
                model=config.get("gemini_model"),
                error_code=getattr(error, "code", type(error).__name__),
                calls=getattr(error, "request_count", 1),
                request_bytes=getattr(error, "request_bytes", 0),
                response_bytes=getattr(error, "response_bytes", 0),
                prompt_tokens=getattr(error, "prompt_tokens", 0),
                output_tokens=getattr(error, "output_tokens", 0),
                total_tokens=getattr(error, "total_tokens", 0),
                cached_tokens=getattr(error, "cached_tokens", 0),
                thoughts_tokens=getattr(error, "thoughts_tokens", 0),
                metered_responses=getattr(error, "metered_responses", 0),
            )
        except Exception as exc:  # pragma: no cover - advisory telemetry only
            log.warning("Gemini failure accounting failed: %s", exc)

    @staticmethod
    def _aggregate_gemini_batches(items: list[object], model: object) -> SimpleNamespace:
        """Combine batch-level usage into one logical provider operation."""

        counters = (
            "request_count",
            "request_bytes",
            "response_bytes",
            "prompt_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "thoughts_tokens",
            "metered_responses",
        )
        totals = {key: 0 for key in counters}
        fingerprints = []
        selected_model = str(model or "")
        for item in items:
            item_model = str(getattr(item, "model", "") or "")
            if item_model:
                selected_model = item_model
            fingerprint = str(getattr(item, "request_hash", "") or "")
            if not fingerprint:
                fingerprint = f"error:{getattr(item, 'code', type(item).__name__)}"
            fingerprints.append(fingerprint)
            for key in counters:
                try:
                    totals[key] += max(0, int(getattr(item, key, 0) or 0))
                except (TypeError, ValueError, OverflowError):
                    continue
        digest = hashlib.sha256("|".join(fingerprints).encode("utf-8")).hexdigest()
        return SimpleNamespace(
            text="",
            model=selected_model,
            request_hash=digest,
            finish_reason="STOP",
            **totals,
        )

    @classmethod
    def _gemini_batch_failure(cls, error: Exception, items: list[object], model: object) -> Exception:
        """Attach every completed or failed batch request to one failure row."""

        aggregate = cls._aggregate_gemini_batches(items, model)
        for key in (
            "request_count",
            "request_bytes",
            "response_bytes",
            "prompt_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "thoughts_tokens",
            "metered_responses",
        ):
            setattr(error, key, getattr(aggregate, key))
        return error

    @staticmethod
    def _registry(config: dict) -> UniverseRegistry:
        return UniverseRegistry(config["data_dir"])

    @staticmethod
    def _require_live(config: dict, world: dict) -> dict | None:
        """Shared live-binding mutation guard (master plan 17.5).

        Raises ``WorldReadOnlyError`` or ``WorldBindingChangedError`` before
        any job, model call, output write, or ledger change. Test fixtures
        that bypass ``_prepare`` carry no binding token and are not guarded.
        """
        token = world.get("binding") if isinstance(world, dict) else None
        if not token:
            return None
        return StarModeService._registry(config).require_live_binding(token)

    def assert_mutable(self) -> dict:
        """Pre-flight check used before a mutation job is even queued."""
        with self._state_lock:
            config, _diagnostics, _store, _ledger, world, _event = self._prepare()
            return self._require_live(config, world) or {}

    def _read_snapshot(self, save_path: str) -> dict:
        """Return the verified snapshot, reusing the last decode when the file is unchanged."""
        try:
            stat = os.stat(save_path)
        except OSError:
            return save_reader.read_snapshot(save_path)
        key = (os.path.normcase(os.path.abspath(save_path)), stat.st_size, stat.st_mtime_ns)
        with self._snapshot_lock:
            cached = self._snapshot_cache
            if cached is not None and cached[0] == key:
                return copy.deepcopy(cached[1])
        snapshot = save_reader.read_snapshot(save_path)
        with self._snapshot_lock:
            self._snapshot_cache = (key, copy.deepcopy(snapshot))
        return snapshot

    def _prepare(self, update: Callable | None = None) -> tuple[dict, dict, WorldStore, object, dict, dict]:
        config, config_diagnostics = config_module.load_with_diagnostics()
        save_path = config.get("save_path")
        if not save_path or not os.path.isfile(save_path):
            if save_path:
                # A configured save that cannot be read never deletes its
                # universe; the registry only records the bounded absence.
                try:
                    self._registry(config).observe_missing(save_path)
                except Exception as exc:  # pragma: no cover - registry is best effort here
                    log.warning("universe registry missing-save observation failed: %s", exc)
            raise FileNotFoundError(
                "스타플레이어 세이브를 찾지 못했습니다. 설정에서 자동 검색 또는 수동 지정을 사용하세요."
            )
        if update:
            update("세이브가 안정적으로 저장됐는지 확인합니다.", 10, "세이브 검증")
        snapshot = self._read_snapshot(save_path)
        if update:
            validation = snapshot["validation"]
            update(
                f"세이브 {validation['verified_chunks']}개 청크 무결성 검증 완료.",
                34,
                "세계선 확인",
            )
        store = self._world_store(config)
        ledger, world = store.resolve(snapshot, save_path)
        world["binding"] = self._registry(config).observe_live(
            world, snapshot, save_path, ledger_state=ledger.state
        )
        history_sync, parser_sync = {}, {}
        # Registry observation may require a relink decision. Historical
        # imports and parser migrations are writes too, not harmless reads.
        if world["binding"].get("live"):
            self._require_live(config, world)
            history_sync = ledger.sync_save_history(snapshot)
            parser_sync = ledger.sync_snapshot_parser_baseline(snapshot)
        if update and history_sync.get("imported_years"):
            years = ", ".join(str(year) for year in history_sync["imported_years"])
            update(f"세이브에서 지난 시즌({years})을 검증해 통산 원장에 연결했습니다.", 40, "커리어 복원")
        if update and parser_sync.get("changed"):
            update("기존 원장의 같은 날짜 스냅샷을 검증된 시즌 요약 형식으로 승격했습니다.", 42, "파서 기준 승격")
        event = ledger.classify(snapshot)
        return config, config_diagnostics, store, ledger, world, event

    @staticmethod
    def _world_output_dir(config: dict, world_id: str) -> Path:
        return Path(config["output_dir"]) / "worlds" / world_id

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            value, _ = load_json(path, None, quarantine_corrupt=False)
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    @staticmethod
    def _spotlight(config: dict, ledger, event: dict, career: dict | None = None) -> dict:
        if career is None:
            career = ledger.career_view(event["snapshot"])
        effective_event = event
        if event.get("kind") in ("NO_CHANGE", "REST_DAY", "WORLD_INIT"):
            recent = ledger.recent_events(1)
            latest = recent[-1] if recent else None
            try:
                same_day = isinstance(latest, dict) and story_engine.date_key(latest.get("date")) == story_engine.date_key(
                    event["snapshot"]
                )
            except (TypeError, ValueError):
                same_day = False
            if same_day and latest.get("kind") not in ("NO_CHANGE", "REST_DAY", "WORLD_INIT"):
                effective_event = dict(latest)
                effective_event["snapshot"] = event["snapshot"]
                effective_event["assess"] = event.get("assess") or {}
                effective_event["same_day_echo"] = True
        return spotlight_engine.evaluate(
            event["snapshot"], career, effective_event, config
        )

    @staticmethod
    def _narrative_source(ledger, world: dict, snapshot: dict, narrative: str | None) -> dict:
        if not narrative:
            return {}
        # The append-only cinematic index survives other jobs and app restarts.
        # Do not infer the writer of an old unindexed file from current settings.
        for row in reversed((ledger.state.get("chronicle") or {}).get("cinematics", [])):
            if (row.get("text") == narrative and row.get("world_id") == world["world_id"]
                    and str(row.get("player_id")) == str(snapshot["player"]["id"])):
                model = str(row.get("model") or "")
                provider = "builtin" if model.startswith("builtin:") else "gemini" if model.startswith("gemini:") else "local_llm" if model else "unknown"
                return {"provider": provider, "model": model, "game_date": row.get("game_date")}
        return {"provider": "unknown"}

    def _dashboard(
        self,
        config: dict,
        config_diagnostics: dict,
        ledger,
        world: dict,
        event: dict,
        *,
        feed: dict | None = None,
        narrative: str | None = None,
        run: dict | None = None,
    ) -> dict:
        snapshot = event["snapshot"]
        output_dir = self._world_output_dir(config, world["world_id"])
        if feed is None:
            feed = self._read_json(output_dir / "feed.json")
        if narrative is None:
            narrative = self._read_text(output_dir / "narrative.md")
        story = ledger.story_view(snapshot)
        feed = story_engine.overlay_feed(feed, story.get("current"))
        career = ledger.career_view(snapshot)
        spotlight = self._spotlight(config, ledger, event, career)
        if isinstance(world, dict) and world.get("binding"):
            # Keep the preserved-world card summary current so a universe
            # that loses its save tomorrow still shows today's counts.
            try:
                self._registry(config).update_summary(world["world_id"], ledger.state)
            except Exception as exc:  # pragma: no cover - never block the dashboard
                log.warning("universe summary refresh failed: %s", exc)
        local_model = self._local_model.status(config, reachable=_llm_reachable())
        return {
            "schema_version": 1,
            "app": {
                "name": "StarPlayer Community Simulator",
                "short_name": "스타모드 피드",
                "version": APP_VERSION,
                "offline": True,
                "llm_reachable": local_model["reachable"],
                "local_model": local_model,
                "community_style_database": community_style_db.manifest(),
                "runtime_web_search": False,
            },
            "config": config_module.public_config(config),
            "config_diagnostics": {
                "using_legacy_config": config_diagnostics.get("using_legacy_config", False),
                "save_exists": config_diagnostics.get("save_exists", False),
            },
            "world": world,
            "player": snapshot["player"],
            "date": snapshot["date"],
            "stats": snapshot["stats"],
            "assessment": event["assess"],
            "event": _event_public(event),
            "history": list(reversed(ledger.recent_events(20))),
            "career": career,
            "spotlight": spotlight,
            "daily_archive": list(reversed(ledger.recent_daily_archive(30))),
            "story": story,
            "story_desk": story_desk.view(ledger.state, snapshot, world, channel="story"),
            "editorial_desk": {channel: story_desk.view(ledger.state, snapshot, world, channel=channel, include_catalog=False)
                               for channel in ("article", "community")},
            "chronicle": chronicle.view(ledger.state, snapshot, world),
            "history_vault": ledger.history_index(snapshot),
            "value_lab": ledger.day_context_view(snapshot),
            "captures": self.capture_status(config),
            "provenance": snapshot.get("provenance", {}),
            "validation": snapshot.get("validation", {}),
            "feed": feed,
            "narrative": narrative,
            "narrative_source": self._narrative_source(ledger, world, snapshot, narrative),
            "run": run or {"changed": event["kind"] != "NO_CHANGE"},
            "generated_at": _now(),
        }

    def bootstrap(self) -> dict:
        with self._state_lock:
            return self._bootstrap_locked()

    def _bootstrap_locked(self) -> dict:
        try:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._latest_dashboard = dashboard
            return dashboard
        except FileNotFoundError as exc:
            failure: Exception = exc
            log.info("bootstrap without save: %s", exc)
        except Exception as exc:
            # The UI shows the same empty dashboard as before; the traceback
            # goes to the log so a parser or state failure is not mistaken
            # for a missing save.
            failure = exc
            log.exception("bootstrap failed: %s", exc)
        config, diagnostics = config_module.load_with_diagnostics()
        try:
            universes = self._registry(config).list_universes()
        except Exception as exc:  # pragma: no cover - never hide the empty dashboard
            log.warning("universe registry listing failed: %s", exc)
            universes = {"universes": [], "preserved_count": 0, "live_count": 0}
        local_model = self._local_model.status(config, reachable=_llm_reachable())
        return {
            "schema_version": 1,
            "app": {
                "name": "StarPlayer Community Simulator",
                "short_name": "스타모드 피드",
                "version": APP_VERSION,
                "offline": True,
                "llm_reachable": local_model["reachable"],
                "local_model": local_model,
                "community_style_database": community_style_db.manifest(),
                "runtime_web_search": False,
            },
            "config": config_module.public_config(config),
            "config_diagnostics": diagnostics,
            "empty": True,
            "error": {"type": type(failure).__name__, "message": str(failure)},
            "worlds": self._world_store(config).list_worlds(),
            "universes": universes,
            "generated_at": _now(),
        }

    @staticmethod
    def _merge_run_options(config: dict, options: dict) -> dict:
        merged = dict(config)
        if "heat" in options:
            try:
                merged["heat"] = max(1, min(10, int(options["heat"])))
            except (TypeError, ValueError):
                pass
        if "community_language_level" in options:
            try:
                merged["community_language_level"] = max(
                    1, min(5, int(options["community_language_level"]))
                )
            except (TypeError, ValueError):
                pass
        if options.get("mode") in ("quick", "standard", "explosion"):
            merged["mode"] = options["mode"]
        if isinstance(options.get("platforms"), list):
            platforms = [value for value in options["platforms"] if value in ("dc", "fmk", "mlb")]
            if platforms:
                merged["platforms"] = platforms
        return merged

    @staticmethod
    def _feed_phrases(feed: dict) -> list[str]:
        phrases: list[str] = []
        for article in feed.get("media", []):
            phrases.append(article.get("title", ""))
        for board in feed.get("boards", []):
            phrases.append(board.get("title", ""))
            phrases.extend(comment.get("text", "") for comment in board.get("comments", []))
        for post in feed.get("social", []):
            phrases.append(post.get("text", ""))
            phrases.extend(reply.get("text", "") for reply in post.get("replies", []))
        return [value for value in phrases if value]

    def check_save(self, update: Callable, options: dict | None = None) -> dict:
        with self._state_lock:
            return self._check_save_locked(update, options)

    def _check_save_locked(self, update: Callable, options: dict | None = None) -> dict:
        options = options or {}
        config, diagnostics, _store, ledger, world, event = self._prepare(update)
        self._require_live(config, world)
        config = self._merge_run_options(config, options)
        force = bool(options.get("force"))
        output_dir = self._world_output_dir(config, world["world_id"])
        has_world_feed = (output_dir / "feed.json").is_file()
        existing_feed = self._read_json(output_dir / "feed.json") if has_world_feed else None
        current_spotlight_contract = bool(
            isinstance(existing_feed, dict)
            and (existing_feed.get("spotlight") or {}).get("schema_version") == 1
            and (existing_feed.get("reaction_bundle") or {}).get("contract_version")
            == provider_feed.VERSION
        )
        if (
            event["kind"] == "NO_CHANGE"
            and not force
            and has_world_feed
            and current_spotlight_contract
        ):
            update("새 경기나 기록 변화가 없습니다. 기존 피드를 그대로 유지합니다.", 100, "완료")
            dashboard = self._dashboard(
                config,
                diagnostics,
                ledger,
                world,
                event,
                run={"changed": False, "regenerated": False, "message": "변화 없음"},
            )
            self._latest_dashboard = dashboard
            return dashboard
        spotlight = self._spotlight(config, ledger, event)
        update("검증된 기록으로 기사와 커뮤니티 반응을 구성합니다.", 52, "피드 생성")
        game_plan, game_bundle = self._plan_game_narrative(config, ledger, world, event, spotlight)
        feed = community.build_feed(
            event,
            config,
            ledger.state["community_memory"],
            spotlight=spotlight,
            universe_id=str(world["world_id"]),
            previous=ledger.state.get("last_snapshot"),
            game_bundle=game_bundle,
        )
        feed = game_narrative.overlay(feed, game_bundle)
        feed = provider_feed.prepare_canonical(
            feed,
            event,
            spotlight,
            universe_id=str(world["world_id"]),
            language_level=config.get("community_language_level"),
            protagonist_aliases=config.get("protagonist_aliases"),
        )
        brief_text = briefing.build_briefing(event, config, ledger, spotlight=spotlight)
        with _commit_gate(update):
            self._require_live(config, world)
            if game_plan:
                inputs = self._narrative_inputs(config, ledger, event)
                for world_event in game_plan["events"]:
                    narrative_state.record_world_event(ledger.state, world_event)
                    self._apply_scheduled_callbacks(ledger, world, inputs, world_event)
                memory_windows.consume_verified_game(
                    ledger.state,
                    game_bundle,
                )
                ledger.state.setdefault("reaction_instances", []).append(copy.deepcopy(game_bundle))
            output_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(output_dir / "feed.canonical.json", feed)
            atomic_write_json(output_dir / "feed.json", feed)
            atomic_write_text(output_dir / "feed.html", presentation.render_html(feed))
            atomic_write_text(output_dir / "briefing.md", brief_text)
            archive_row = self._archive_feed(output_dir, event, feed)

            update("세계선 원장에 중복 없이 반영합니다.", 76, "원장 반영")
            for _kind, text in event.get("milestones", []):
                ledger.remember("memes", text)
            for phrase in self._feed_phrases(feed):
                ledger.remember("recent_phrases", phrase)
            editorial_engine.remember(ledger.state["community_memory"], feed)
            ledger.remember_daily_archive(archive_row)
            ledger.commit(event)

            dashboard = self._dashboard(
                config,
                diagnostics,
                ledger,
                world,
                event,
                feed=feed,
                run={
                    "changed": event["kind"] != "NO_CHANGE",
                    "regenerated": True,
                    "message": (
                        "기준선 생성"
                        if event.get("baseline_only")
                        else "세계 주목도 모델로 반응 재구성"
                        if event["kind"] == "NO_CHANGE" and has_world_feed
                        else "기존 기록으로 피드 복원"
                        if event["kind"] == "NO_CHANGE"
                        else "피드 생성 완료"
                    ),
                },
            )
            atomic_write_json(output_dir / "dashboard.json", dashboard)
            self._persist_current_capsule(config, ledger, world, event["snapshot"])
            update("피드와 기록 화면을 갱신했습니다.", 96, "마무리")
            self._latest_dashboard = dashboard
        return dashboard

    def enrich_feed(self, update: Callable, options: dict | None = None) -> dict:
        """Render all or one requested surface through the selected provider.

        The current deterministic bundle remains a sidecar and is the only
        source for each rerun. Gemini failure falls back to the local model;
        local failure preserves an existing partial surface or falls back to
        the unchanged built-in whole bundle. Provider latency never holds the
        world-state lock.
        """

        options = options or {}
        surface_scope = provider_feed.normalize_surface_scope(options.get("surface_scope"))
        surface_label = provider_feed.surface_scope_label(surface_scope)
        usage_operation = {
            "all": "reaction_bundle",
            "articles": "reaction_articles",
            "community": "reaction_community",
        }[surface_scope]
        with self._state_lock:
            config, _diagnostics, _store, ledger, world, event = self._prepare(update)
            self._require_live(config, world)
            config = self._merge_run_options(config, options)
            output_dir = self._world_output_dir(config, world["world_id"])
            canonical_path = output_dir / "feed.canonical.json"
            canonical = self._read_json(canonical_path) or self._read_json(output_dir / "feed.json")
            if not isinstance(canonical, dict):
                raise RuntimeError("먼저 최신 세이브 확인을 실행해 기준 피드를 만들어 주세요.")
            spotlight = self._spotlight(config, ledger, event)
            canonical = provider_feed.prepare_canonical(
                canonical,
                event,
                spotlight,
                universe_id=str(world["world_id"]),
                language_level=config.get("community_language_level"),
                protagonist_aliases=config.get("protagonist_aliases"),
            )
            focus = (canonical.get("reaction_bundle") or {}).get("focus") or {}
            current_hash = str(event["snapshot"].get("content_hash") or "")
            if focus.get("source_hash") and current_hash and focus["source_hash"] != current_hash:
                raise RuntimeError("세이브가 기준 피드보다 앞서 있습니다. 먼저 최신 세이브 확인을 실행해 주세요.")
            selected = str(options.get("provider") or config.get("ai_provider") or "local_auto")
            if selected not in ("local_auto", "local_only", "deterministic", "gemini"):
                selected = "local_auto"
            game_date = story_engine.date_key(event["snapshot"])
            protagonist_id = (event["snapshot"].get("player") or {}).get("id")
            context_by_scope = {}
            memory_by_scope = {}
            origin_by_scope = {}
            for remote in (False, True):
                scope = "remote" if remote else "local"
                context = personal_context.prompt_section(
                    ledger.state,
                    universe_id=str(world["world_id"]),
                    protagonist_id=protagonist_id,
                    game_date=game_date,
                    remote=remote,
                    audience="public",
                )
                memory = memory_windows.prompt_packet(
                    memory_windows.build_view(
                        ledger.state,
                        as_of_date=game_date,
                        universe_id=str(world["world_id"]),
                        protagonist_id=protagonist_id,
                        # Every feed surface is public. Local inference may
                        # see public memories without Google opt-in, while
                        # private/clubhouse memories never enter the prompt.
                        remote=True,
                    )
                )
                origin = self._story_context(config, world, event["snapshot"], game_date)
                origin["personal_context_scope"] = "remote" if remote else "public_local"
                origin["memory_context_scope"] = "public"
                origin["personal_context_hash"] = hashlib.sha256(context.encode("utf-8")).hexdigest()
                origin["memory_context_hash"] = memory["fingerprint"]
                origin["memory_context_refs"] = memory["references"]
                context_by_scope[scope] = context
                memory_by_scope[scope] = memory["text"]
                origin_by_scope[scope] = origin

        attempts: list[dict] = []
        enriched = None
        preserve_existing = False
        used_origin = origin_by_scope["local"]
        used_label = "내장 엔진"
        batches = provider_feed.scoped_expression_batches(canonical, surface_scope)
        if not batches:
            raise RuntimeError(f"현재 고정 피드에는 다시 작성할 {surface_label} 항목이 없습니다.")

        def prompt_for(
            scope: str, batch: dict, *, compact: bool = False, repair_note: str = ""
        ) -> tuple[str, str, str]:
            return provider_feed.build_batch_prompt(
                canonical,
                batch,
                spotlight=spotlight,
                approved_player_context=context_by_scope[scope],
                approved_memory_context=memory_by_scope[scope],
                compact=compact,
                language_level=config.get("community_language_level"),
                repair_note=repair_note,
            )

        # One assembly shared by every provider in this run. A batch that any
        # provider finishes stays here, so a provider that fails midway never
        # discards what it already wrote and the next provider picks up only
        # the batches that are still missing.
        language_level = config.get("community_language_level")
        aggregate_contract = provider_feed.expression_contract(canonical)
        allowed_sources: list[str] = []
        batch_audit: list[dict] = []
        completed_ids: set[str] = set()
        contributors: dict[str, str] = {}
        usage_items: list[object] = []
        key_source: str | None = None
        max_batch_attempts = 3
        accept_lock = threading.Lock()
        check_cancelled = getattr(update, "check_cancelled", None)
        pacer = gemini_provider.BatchRequestPacer()

        def before_remote_request() -> None:
            pacer.wait(check_cancelled)
        # Spellings the model used where the registered ones were absent. Only
        # ever suggested to the user; never accepted on their own.
        spelling_candidates: list[str] = []

        def pending_batches() -> list[tuple[int, dict]]:
            return [
                (ordinal, batch)
                for ordinal, batch in enumerate(batches, start=1)
                if str(batch.get("batch_id") or "") not in completed_ids
            ]

        def accept_batch(
            batch: dict,
            partial: dict,
            allowed_numbers: str,
            provider_key: str,
            model_name: object,
            attempts_used: int,
        ) -> None:
            nonlocal aggregate_contract
            aggregate_contract = provider_feed.merge_batch_contract(
                aggregate_contract, provider_feed.expression_contract(partial), batch
            )
            allowed_sources.append(allowed_numbers)
            completed_ids.add(str(batch.get("batch_id") or ""))
            contributors[provider_key] = str(model_name)
            reverted = ((partial.get("reaction_bundle") or {}).get("reverted_items")) or []
            for row in reverted:
                for reason in row.get("reasons") or []:
                    marker = "모델 표기 추정: "
                    if marker in str(reason):
                        for token in str(reason).split(marker, 1)[1].split(","):
                            token = token.strip()
                            if token and token not in spelling_candidates:
                                spelling_candidates.append(token)
            if reverted:
                log.info(
                    "%s batch %s kept %d deterministic item(s): %s",
                    provider_key,
                    batch.get("batch_id"),
                    len(reverted),
                    [row.get("reasons") for row in reverted],
                )
            batch_audit.append(
                {
                    "batch_id": batch["batch_id"],
                    "surface": batch["kind"],
                    "items": batch["item_count"],
                    "attempts": attempts_used,
                    "provider": provider_key,
                    "model": str(model_name),
                    "reverted_items": copy.deepcopy(reverted),
                }
            )

        if selected == "gemini" and batches:
            api_key, key_source = self._secret_store().get_gemini_key()
            if config.get("gemini_consent") is not True:
                attempts.append({"provider": "gemini", "status": "skipped", "code": "CONSENT_REQUIRED"})
                update("Gemini 전송 동의가 없어 실행 중인 로컬 LLM을 확인합니다.", 28, "로컬 대체 준비")
            elif not api_key:
                attempts.append({"provider": "gemini", "status": "skipped", "code": "KEY_MISSING"})
                update("Gemini 키가 없어 실행 중인 로컬 LLM을 확인합니다.", 28, "로컬 대체 준비")
            else:
                queue = pending_batches()
                workers = max(1, min(int(config.get("gemini_batch_concurrency") or REMOTE_BATCH_CONCURRENCY), REMOTE_BATCH_CONCURRENCY, len(queue)))
                update(
                    f"고정된 {surface_label} 계획 {len(queue)}개 묶음을 Gemini에 "
                    f"{workers}개씩 동시에 보냅니다.",
                    30,
                    "Gemini 병렬 반응",
                )
                done_count = 0
                stopped: Exception | None = None
                skipped: list[str] = []

                def write_remote_batch(ordinal: int, batch: dict) -> dict:
                    """Write one batch, retrying with the refusal reason. Never raises."""
                    compact = False
                    repair_note = ""
                    batch_attempts = 0
                    started = time.monotonic()
                    while batch_attempts < max_batch_attempts:
                        if check_cancelled:
                            check_cancelled()
                        batch_attempts += 1
                        system_text, user_text, allowed_numbers = prompt_for(
                            "remote", batch, compact=compact, repair_note=repair_note
                        )

                        def report_transient_retry(retry: dict) -> None:
                            pacer.defer(retry)
                            retry_number = max(1, int(retry.get("retry", 1) or 1))
                            retry_limit = max(
                                retry_number,
                                int(retry.get("max_retries", retry_number) or retry_number),
                            )
                            delay = max(0.0, float(retry.get("delay_seconds", 0.0) or 0.0))
                            reason = {
                                "QUOTA_EXCEEDED": "사용 한도가 응답해",
                                "REMOTE_TIMEOUT": "처리가 지연되어",
                                "REMOTE_UNAVAILABLE": "서비스가 일시적으로 응답하지 않아",
                            }.get(str(retry.get("code") or ""), "일시 오류가 발생해")
                            update(
                                f"Gemini {batch['label']} 묶음 {reason} 자동 재시도합니다 "
                                f"({retry_number}/{retry_limit}, {delay:.1f}초 후).",
                                None,
                                f"Gemini {batch['label']} 재시도",
                            )

                        try:
                            remote = gemini_provider.generate_text(
                                system_text,
                                user_text,
                                api_key=api_key,
                                model=config.get("gemini_model"),
                                max_tokens=provider_feed.batch_token_budget(
                                    batch, config.get("mode"), compact=compact
                                ),
                                invalid_response_retries=0,
                                validate_korean_and_numbers=False,
                                on_transient_retry=report_transient_retry,
                                check_cancelled=check_cancelled,
                                before_request=before_remote_request,
                            )
                        except gemini_provider.GeminiProviderError as exc:
                            usage_items.append(exc)
                            if exc.code == "TRUNCATED_RESPONSE" and not compact:
                                compact = True
                                update(
                                    f"Gemini {batch['label']} {ordinal}/{len(batches)} 응답만 잘려 "
                                    "분량을 줄여 한 번 다시 요청합니다.",
                                    None,
                                    "잘린 묶음 재요청",
                                )
                                continue
                            log.warning(
                                "gemini batch %s/%s failed after %.1fs (%s): %s",
                                ordinal,
                                len(batches),
                                time.monotonic() - started,
                                getattr(exc, "code", type(exc).__name__),
                                exc,
                            )
                            return {"ordinal": ordinal, "batch": batch, "transport_error": exc}
                        usage_items.append(remote)
                        try:
                            partial = provider_feed.apply_expression(
                                provider_feed.expression_batch_feed(canonical, batch),
                                remote.text,
                                provider="gemini",
                                model=remote.model,
                                allowed_number_source=allowed_numbers,
                                language_level=language_level,
                                on_violation="revert_item",
                            )
                        except provider_feed.ProviderFeedError as exc:
                            log.warning(
                                "gemini batch %s/%s rejected on attempt %s/%s after %.1fs: %s",
                                ordinal,
                                len(batches),
                                batch_attempts,
                                max_batch_attempts,
                                time.monotonic() - started,
                                exc,
                            )
                            repair_note = str(exc)
                            if batch_attempts >= max_batch_attempts:
                                return {"ordinal": ordinal, "batch": batch, "content_error": exc}
                            compact = compact or batch_attempts >= 2
                            continue
                        log.info(
                            "gemini batch %s/%s written in %.1fs on attempt %s (%s prompt / %s output tokens)",
                            ordinal,
                            len(batches),
                            time.monotonic() - started,
                            batch_attempts,
                            getattr(remote, "prompt_tokens", 0),
                            getattr(remote, "output_tokens", 0),
                        )
                        return {
                            "ordinal": ordinal,
                            "batch": batch,
                            "partial": partial,
                            "allowed": allowed_numbers,
                            "model": remote.model,
                            "attempts": batch_attempts,
                        }
                    return {"ordinal": ordinal, "batch": batch, "content_error": RuntimeError("재시도 한도")}

                # Batches cover disjoint items and merge by identifier, so they
                # are written concurrently. Five sequential 45-second requests
                # were the reason a run took minutes.
                run_started = time.monotonic()
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="feed-batch") as pool:
                    futures = [pool.submit(write_remote_batch, ordinal, batch) for ordinal, batch in queue]
                    for future in as_completed(futures):
                        try:
                            outcome = future.result()
                        except JobCancelled:
                            for queued in futures:
                                queued.cancel()
                            raise
                        except Exception as exc:  # noqa: BLE001 - bounded worker
                            log.warning("gemini batch worker failed: %s", exc)
                            stopped = stopped or exc
                            continue
                        done_count += 1
                        ordinal = outcome["ordinal"]
                        batch = outcome["batch"]
                        progress = min(52, 30 + round((done_count / max(1, len(queue))) * 22))
                        if outcome.get("transport_error") is not None:
                            stopped = stopped or outcome["transport_error"]
                            update(
                                f"Gemini {batch['label']} {ordinal}/{len(batches)} 묶음을 받지 못했습니다.",
                                progress,
                                "Gemini 묶음 실패",
                            )
                            continue
                        if outcome.get("content_error") is not None:
                            skipped.append(f"{batch['label']} {ordinal}/{len(batches)}: {outcome['content_error']}")
                            update(
                                f"Gemini {batch['label']} {ordinal}/{len(batches)} 묶음이 검사를 통과하지 못했습니다.",
                                progress,
                                "묶음 보류",
                            )
                            continue
                        with accept_lock:
                            accept_batch(
                                batch,
                                outcome["partial"],
                                outcome["allowed"],
                                "gemini",
                                outcome["model"],
                                outcome["attempts"],
                            )
                        update(
                            f"Gemini 순차 수신 완료 · {batch['label']} {ordinal}/{len(batches)} "
                            f"({done_count}/{len(queue)})",
                            progress,
                            f"Gemini {batch['label']} 완료",
                        )
                gemini_batches = [row for row in batch_audit if row.get("provider") == "gemini"]
                log.info(
                    "gemini wrote %d/%d batches in %.1fs with %d worker(s)",
                    len(gemini_batches),
                    len(queue),
                    time.monotonic() - run_started,
                    workers,
                )
                if stopped is not None:
                    self._gemini_batch_failure(stopped, usage_items, config.get("gemini_model"))
                    self._record_gemini_failure(config, stopped, usage_operation)
                    attempts.append(
                        {
                            "provider": "gemini",
                            "status": "partial" if gemini_batches else "failed",
                            "code": getattr(stopped, "code", type(stopped).__name__),
                            "message": str(stopped)[:300],
                            "generation_mode": "parallel_batches",
                            "surface_scope": surface_scope,
                            "completed_batches": len(gemini_batches),
                            "batch_count": len(batches),
                        }
                    )
                    kept = f" 완료한 {len(gemini_batches)}개 묶음은 그대로 두고" if gemini_batches else ""
                    update(
                        f"Gemini {surface_label} 작성을 완료하지 못해{kept} 실행 중인 로컬 LLM을 확인합니다.",
                        52,
                        "로컬 대체",
                    )
                else:
                    combined = self._aggregate_gemini_batches(usage_items, config.get("gemini_model"))
                    self._record_gemini_generation(config, combined, usage_operation)
                    attempts.append(
                        {
                            "provider": "gemini",
                            "status": "partial" if skipped else "success",
                            "model": combined.model,
                            "message": (" / ".join(skipped)[:300] or None),
                            "generation_mode": "parallel_batches",
                            "surface_scope": surface_scope,
                            "completed_batches": len(gemini_batches),
                            "batch_count": len(batches),
                        }
                    )
                    if skipped:
                        log.warning("gemini skipped %d batch(es): %s", len(skipped), " / ".join(skipped))
                        update(
                            f"Gemini가 통과하지 못한 {len(skipped)}개 묶음을 로컬 LLM으로 이어서 씁니다.",
                            52,
                            "로컬 이어쓰기",
                        )

        if batches and pending_batches() and selected in ("gemini", "local_auto", "local_only"):
            if self._ensure_local_llm(update, config):
                remaining = len(pending_batches())
                update(
                    f"로컬 LLM이 남은 {surface_label} 묶음 {remaining}개를 순차 작성합니다.",
                    56,
                    "로컬 순차 반응",
                )
                local_stopped: Exception | None = None
                local_skipped: list[str] = []
                for ordinal, batch in pending_batches():
                    if local_stopped is not None:
                        break
                    compact = False
                    repair_note = ""
                    batch_attempts = 0
                    progress = min(77, 56 + round(((ordinal - 1) / len(batches)) * 21))
                    while batch_attempts < max_batch_attempts:
                        batch_attempts += 1
                        system_text, user_text, allowed_numbers = prompt_for(
                            "local", batch, compact=compact, repair_note=repair_note
                        )
                        update(
                            f"로컬 LLM 순차 작성 · {batch['label']} {ordinal}/{len(batches)}",
                            progress,
                            f"로컬 {batch['label']} {ordinal}/{len(batches)}",
                        )
                        started = time.monotonic()
                        try:
                            text, model = narration_module.structured_feed(
                                system_text,
                                user_text,
                                max_tokens=provider_feed.batch_token_budget(
                                    batch, config.get("mode"), compact=compact
                                ),
                                # Grammar-constrained decoding roughly halves
                                # local throughput, so the first attempt runs
                                # free and the schema becomes the repair tool
                                # once an answer has actually been refused.
                                json_schema=(
                                    provider_feed.batch_response_schema(canonical, batch)
                                    if batch_attempts > 1
                                    else None
                                ),
                                check_cancelled=check_cancelled,
                            )
                        except JobCancelled:
                            raise
                        except Exception as exc:  # noqa: BLE001 - bounded local transport
                            log.warning("local model transport failed on batch %s: %s", ordinal, exc)
                            local_stopped = exc
                            break
                        if not text:
                            if str(model).startswith("LOCAL_TRUNCATED_RESPONSE") and not compact:
                                compact = True
                                update("로컬 응답이 잘린 묶음만 분량을 줄여 다시 요청합니다.", progress, "로컬 길이 복구")
                                continue
                            log.warning("local model returned no text on batch %s: %s", ordinal, model)
                            local_stopped = RuntimeError(str(model or "NO_RESPONSE"))
                            break
                        try:
                            partial = provider_feed.apply_expression(
                                provider_feed.expression_batch_feed(canonical, batch),
                                text,
                                provider="local_llm",
                                model=str(model),
                                allowed_number_source=allowed_numbers,
                                language_level=language_level,
                                on_violation="revert_item",
                            )
                        except provider_feed.ProviderFeedError as exc:
                            log.warning(
                                "local batch %s (%s/%s) rejected on attempt %s/%s: %s",
                                batch.get("batch_id"),
                                ordinal,
                                len(batches),
                                batch_attempts,
                                max_batch_attempts,
                                exc,
                            )
                            repair_note = str(exc)
                            if batch_attempts >= max_batch_attempts:
                                local_skipped.append(f"{batch['label']} {ordinal}/{len(batches)}: {exc}")
                                update(
                                    f"로컬 {batch['label']} {ordinal}/{len(batches)} 묶음은 검사를 통과하지 못해 "
                                    "이 묶음만 건너뜁니다.",
                                    progress,
                                    "묶음 보류",
                                )
                                break
                            compact = compact or batch_attempts >= 2
                            update(
                                f"로컬 {batch['label']} {ordinal}/{len(batches)} 지적 사항을 반영해 다시 씁니다.",
                                progress,
                                "로컬 묶음 재요청",
                            )
                            continue
                        log.info(
                            "local batch %s/%s written in %.1fs on attempt %s",
                            ordinal,
                            len(batches),
                            time.monotonic() - started,
                            batch_attempts,
                        )
                        accept_batch(batch, partial, allowed_numbers, "local_llm", model, batch_attempts)
                        update(
                            f"로컬 LLM 순차 수신 완료 · {batch['label']} {ordinal}/{len(batches)}",
                            min(78, 56 + round((ordinal / len(batches)) * 22)),
                            f"로컬 {batch['label']} 완료",
                        )
                        break
                local_batches = [row for row in batch_audit if row.get("provider") == "local_llm"]
                if local_stopped is not None or local_skipped:
                    attempts.append(
                        {
                            "provider": "local_llm",
                            "status": "partial" if local_batches else "failed",
                            "code": type(local_stopped).__name__ if local_stopped else "BATCH_REJECTED",
                            "message": (str(local_stopped) if local_stopped else " / ".join(local_skipped))[:300],
                            "generation_mode": "sequential_batches",
                            "surface_scope": surface_scope,
                            "completed_batches": len(local_batches),
                            "batch_count": len(batches),
                        }
                    )
                else:
                    attempts.append(
                        {
                            "provider": "local_llm",
                            "status": "success",
                            "model": str(contributors.get("local_llm") or ""),
                            "generation_mode": "sequential_batches",
                            "surface_scope": surface_scope,
                            "completed_batches": len(local_batches),
                            "batch_count": len(batches),
                        }
                    )
            else:
                attempts.append({"provider": "local_llm", "status": "unavailable", "code": "NOT_REACHABLE"})

        # Completed batches are preserved across providers so the next one
        # only writes what is missing, but a bundle is published only when
        # every batch is in: a half-rewritten feed is never published.
        if completed_ids and pending_batches():
            missing = [f"{ordinal}/{len(batches)}" for ordinal, _batch in pending_batches()]
            log.warning(
                "%d/%d batches were written but %d could not be completed (%s); keeping the current feed",
                len(completed_ids),
                len(batches),
                len(missing),
                ", ".join(missing),
            )
            attempts.append(
                {
                    "provider": "mixed" if len(contributors) > 1 else (sorted(contributors) or ["none"])[0],
                    "status": "incomplete",
                    "code": "BATCHES_MISSING",
                    "message": f"{len(completed_ids)}/{len(batches)}개 묶음만 작성돼 발행하지 않았습니다.",
                    "generation_mode": "sequential_batches",
                    "surface_scope": surface_scope,
                    "completed_batches": len(completed_ids),
                    "batch_count": len(batches),
                }
            )
        elif completed_ids:
            written = sorted(contributors)
            if len(written) == 1:
                provider_name = written[0]
                model_name = contributors[written[0]]
            else:
                provider_name = "mixed"
                model_name = " + ".join(f"{key}:{contributors[key]}" for key in written)
            provider_audit = {
                "provider": provider_name,
                "model": model_name,
                "network_used": "gemini" in contributors,
                "generation_mode": "sequential_batches",
                "surface_scope": surface_scope,
                "batch_count": len(batches),
                "completed_batches": len(completed_ids),
                "batch_plan_hash": provider_feed.batch_plan_hash(batches),
                "batches": sorted(copy.deepcopy(batch_audit), key=lambda row: next(
                    index for index, batch in enumerate(batches) if batch["batch_id"] == row["batch_id"]
                )),
                "contributors": copy.deepcopy(contributors),
            }
            if "gemini" in contributors:
                provider_audit["gemini"] = self._gemini_audit(
                    self._aggregate_gemini_batches(usage_items, config.get("gemini_model")), key_source
                )
            try:
                enriched = provider_feed.finalize_batched_expression(
                    canonical,
                    aggregate_contract,
                    provider=provider_name,
                    model=str(model_name),
                    batches=batches,
                    provider_audit=provider_audit,
                    allowed_number_source="\n".join(
                        [
                            *allowed_sources,
                            json.dumps(provider_feed.expression_contract(canonical), ensure_ascii=False),
                        ]
                    ),
                    language_level=language_level,
                )
            except (provider_feed.ProviderFeedError, ValueError, TypeError) as exc:
                # The assembled bundle failed the whole-feed pass (for example
                # two batches converged on the same headline). Keep the current
                # feed rather than publishing a bundle that broke a guard.
                log.warning("assembled bundle rejected after %d batch(es): %s", len(completed_ids), exc)
                enriched = None
                update(f"응답 수신 후 전체 검증에서 보류했습니다: {str(exc)[:240]}", 80, "전체 검증 보류")
                attempts.append(
                    {
                        "provider": provider_name,
                        "status": "rejected",
                        "code": type(exc).__name__,
                        "message": str(exc)[:300],
                        "generation_mode": "sequential_batches",
                        "surface_scope": surface_scope,
                        "completed_batches": len(completed_ids),
                        "batch_count": len(batches),
                    }
                )
            else:
                # A mixed bundle may contain prose written from the local
                # provider's wider public-only context. Bind publication to
                # that superset so a context edit made while either provider
                # is answering cannot slip past the commit gate. A Gemini-only
                # bundle remains bound to the explicitly remote-approved view.
                used_origin = origin_by_scope[
                    "local" if "local_llm" in contributors else "remote"
                ]
                if len(written) == 1 and written[0] == "gemini":
                    used_label = f"Gemini · {contributors['gemini']} · {len(completed_ids)}개 묶음"
                elif len(written) == 1:
                    used_label = f"로컬 LLM · {contributors['local_llm']} · {len(completed_ids)}개 묶음"
                else:
                    used_label = (
                        f"Gemini+로컬 혼합 · {len(completed_ids)}/{len(batches)}개 묶음"
                    )
                incomplete = len(batches) - len(completed_ids)
                if incomplete > 0:
                    log.info(
                        "published %d/%d batches; %d kept the built-in text",
                        len(completed_ids),
                        len(batches),
                        incomplete,
                    )

        if enriched is None:
            message = (
                f"선택한 모델이 응답하지 않아 현재 {surface_label} 내용을 그대로 유지했습니다."
                if selected != "deterministic"
                else f"검증된 내장 리소스로 {surface_label} 영역을 다시 구성했습니다."
            )
            # A provider-backed rewrite is one atomic publication regardless
            # of scope. If no complete, fully validated replacement exists,
            # retain the current feed byte-for-byte; publishing the canonical
            # fallback here would silently discard an earlier rich result.
            if selected != "deterministic":
                preserve_existing = True
            else:
                enriched = provider_feed.mark_fallback(canonical, attempts, message)
                used_origin = origin_by_scope["local"]
                used_label = "내장 엔진"
        else:
            enriched["reaction_bundle"]["fallback_chain"] = copy.deepcopy(attempts[:-1])

        update("주인공·숫자·공개 범위와 반복 여부를 최종 확인합니다.", 82, f"{surface_label} 검증")
        with _commit_gate(update):
            with self._state_lock:
                current_config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(current_config, world)
                self._require_chat_origin(
                    current_config,
                    world,
                    event["snapshot"],
                    used_origin,
                    ledger=ledger,
                )
                output_dir = self._world_output_dir(current_config, world["world_id"])
                current_feed_path = output_dir / "feed.json"
                current_feed_exists = current_feed_path.exists()
                current_feed = self._read_json(current_feed_path) or canonical
                if preserve_existing and not current_feed_exists and surface_scope == "all":
                    # A first-run fixture can have a canonical sidecar before
                    # feed.json exists. There is nothing to preserve in that
                    # case, so retain the established behaviour of publishing
                    # the verified built-in bundle as the initial feed.
                    enriched = provider_feed.mark_fallback(canonical, attempts, message)
                    used_origin = origin_by_scope["local"]
                    preserve_existing = False
                if preserve_existing:
                    rejected = next((row for row in reversed(attempts) if row.get("status") == "rejected"), None)
                    failure_message = f"{surface_label} 모델 작성 실패 · 기존 내용 유지"
                    if rejected:
                        failure_message += f" · {rejected['message']}"
                    dashboard = self._dashboard(
                        current_config,
                        diagnostics,
                        ledger,
                        world,
                        event,
                        feed=current_feed,
                        run={
                            "changed": False,
                            "regenerated": False,
                            "provider_failed": True,
                            "message": failure_message,
                            "provider": selected,
                            "feed_renderer": (current_feed.get("reaction_bundle") or {}).get("renderer"),
                            "provider_attempts": attempts,
                            "surface_scope": surface_scope,
                            "protagonist_spelling_candidates": list(spelling_candidates[:3]),
                        },
                    )
                    self._latest_dashboard = dashboard
                    update(failure_message, 96, "검증 보류" if rejected else "마무리")
                    return dashboard
                if surface_scope != "all":
                    enriched = provider_feed.merge_scoped_expression(
                        canonical,
                        current_feed,
                        enriched,
                        surface_scope,
                    )
                output_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(output_dir / "feed.canonical.json", canonical)
                atomic_write_json(output_dir / "feed.json", enriched)
                atomic_write_text(output_dir / "feed.html", presentation.render_html(enriched))
                archive_row = self._archive_feed(output_dir, event, enriched)
                for phrase in self._feed_phrases(
                    provider_feed.scoped_feed_view(enriched, surface_scope)
                ):
                    ledger.remember("recent_phrases", phrase)
                ledger.remember_daily_archive(archive_row)
                ledger.save()
                dashboard = self._dashboard(
                    current_config,
                    diagnostics,
                    ledger,
                    world,
                    event,
                    feed=enriched,
                    run={
                        "changed": False,
                        "regenerated": True,
                        "message": f"{surface_label} 풍부화 완료 · {used_label}",
                        "provider": (enriched.get("reaction_bundle") or {}).get("surface_sources", {}).get(
                            surface_scope, {}
                        ).get("provider") or (enriched.get("reaction_bundle") or {}).get("provider"),
                        "feed_renderer": (enriched.get("reaction_bundle") or {}).get("renderer"),
                        "provider_attempts": attempts,
                        "surface_scope": surface_scope,
                        "protagonist_spelling_candidates": list(spelling_candidates[:3]),
                    },
                )
                atomic_write_json(output_dir / "dashboard.json", dashboard)
                self._persist_current_capsule(current_config, ledger, world, event["snapshot"])
                self._latest_dashboard = dashboard
            update(f"{surface_label} 저장 완료 · {used_label}", 96, "마무리")
        return dashboard

    def _plan_game_narrative(self, config: dict, ledger, world: dict, event: dict, spotlight: dict) -> tuple[dict | None, dict | None]:
        source_hash = event["snapshot"].get("content_hash")
        for stored in reversed(ledger.state.get("reaction_instances") or []):
            if stored.get("kind") == "save_delta" and stored.get("source_hash") == source_hash:
                return None, copy.deepcopy(stored)
        plan = game_narrative.plan(event, ledger.state.get("last_snapshot"), universe_id=str(world["world_id"]))
        if not plan:
            return None, None
        bundle = narrative_engine.realize_game_reactions(
            event=plan["event"], snapshot=event["snapshot"], game_date=plan["game_date"], spotlight=spotlight,
            universe_id=str(world["world_id"]), ledger_state=ledger.state, career=ledger.career_view(event["snapshot"]),
        )
        if config.get("mode") == "quick":
            bundle["reactions"]["media"] = []
        bundle.update({"kind": "save_delta", "instance_id": plan["instance_id"], "source_hash": source_hash, "game_date": plan["game_date"], "universe_id": str(world["world_id"]), "protagonist_id": str((event["snapshot"].get("player") or {}).get("id")), "event_ids": [row["event_id"] for row in plan["events"]], "renderer": "deterministic", "bridge_version": game_narrative.VERSION})
        bundle["source_facts"] = list(plan["event"].get("source_facts") or [])
        bundle["observed_delta"] = copy.deepcopy(plan["event"].get("delta") or {})
        # Preserve known zero fields too; the ledger stores changed counters
        # only, while a replay must keep the hitless secondary line visible.
        previous_stats = (ledger.state.get("last_snapshot") or {}).get("stats") or {}
        current_stats = event["snapshot"].get("stats") or {}
        for key in editorial_engine.COUNT_FIELDS:
            if key in previous_stats and key in current_stats and current_stats[key] == previous_stats[key]:
                bundle["observed_delta"][key] = 0
        return plan, bundle

    def _archive_payload(self, config: dict, world: dict, row: dict) -> dict | None:
        output_dir = self._world_output_dir(config, world["world_id"]).resolve()
        target = (output_dir / str(row.get("relative_path") or "")).resolve()
        try:
            target.relative_to(output_dir / "archive")
        except ValueError:
            return None
        return self._read_json(target)

    def _enrich_capsule(self, config: dict, world: dict, capsule: dict) -> dict:
        value = dict(capsule)
        index = list(value.get("reaction_archives") or [])
        payloads = []
        for row in index:
            payload = self._archive_payload(config, world, row)
            if payload:
                payloads.append(payload)
        value["reaction_archive_index"] = index
        value["reaction_archives"] = payloads
        latest_feed = payloads[-1].get("feed") if payloads else None
        value["combined_feed"] = story_engine.overlay_feed(
            latest_feed, value.get("story")
        )
        return value

    def _persist_current_capsule(self, config: dict, ledger, world: dict, snapshot: dict) -> dict:
        game_date = story_engine.date_key(snapshot)
        capsule = self._enrich_capsule(
            config, world, ledger.history_capsule(game_date, snapshot)
        )
        relative = Path("history") / game_date[:4] / game_date[5:7] / game_date[8:10] / "capsule.json"
        atomic_write_json(self._world_output_dir(config, world["world_id"]) / relative, capsule)
        return capsule

    @staticmethod
    def _archive_feed(output_dir: Path, event: dict, feed: dict) -> dict:
        snapshot = event["snapshot"]
        game_date = snapshot.get("date") or {}
        date_text = (
            f"{int(game_date.get('year') or 0):04d}-"
            f"{int(game_date.get('month') or 0):02d}-"
            f"{int(game_date.get('day') or 0):02d}"
        )
        feed_bytes = json.dumps(
            feed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        feed_hash = hashlib.sha256(feed_bytes).hexdigest()
        source_hash = snapshot.get("content_hash") or "no-source"
        archive_id = hashlib.sha256(
            f"{source_hash}:{feed_hash}".encode("utf-8")
        ).hexdigest()[:20]
        relative = Path("archive") / date_text[:4] / date_text[5:7] / date_text[8:10] / f"{archive_id}.json"
        payload = {
            "schema_version": 1,
            "id": archive_id,
            "game_date": date_text,
            "event": _event_public(event),
            "feed": feed,
            "source_hash": source_hash,
            "feed_hash": feed_hash,
            "archived_at": _now(),
        }
        atomic_write_json(output_dir / relative, payload)
        return {
            "id": archive_id,
            "game_date": date_text,
            "event_kind": event.get("kind"),
            "role": event.get("role", "no_appearance"),
            "source_hash": source_hash,
            "feed_hash": feed_hash,
            "relative_path": relative.as_posix(),
            "board_count": len(feed.get("boards", [])),
            "article_count": len(feed.get("media", [])),
            "social_count": len(feed.get("social", [])),
            "headline": next(
                (row.get("title") for row in feed.get("media", []) if row.get("title")),
                next(
                    (row.get("title") for row in feed.get("boards", []) if row.get("title")),
                    "기록 반응 아카이브",
                ),
            ),
            "archived_at": payload["archived_at"],
        }

    @staticmethod
    def _shots(config: dict, limit: int = MAX_NARRATIVE_IMAGES) -> list[str]:
        directory = Path(config["shots_dir"])
        if not directory.is_dir():
            return []
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return [str(path) for path in candidates[:limit]]

    @staticmethod
    def _ensure_local_llm(update: Callable, config: dict | None = None) -> bool:
        """Use only a loopback model that the user has already started.

        The legacy method name is retained for callers and tests, but this is
        deliberately a read-only availability check. StarModeFeed never starts
        a model process, even when an old configuration still contains an
        ``llm_launcher`` path or a remote provider fails.
        """
        del update, config
        return bool(_probe_llm_ports())

    def generate_narrative(self, update: Callable, options: dict | None = None) -> dict:
        options = options or {}
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare(update)
            self._require_live(config, world)
            game_date = story_engine.date_key(event["snapshot"])
            base_origin = self._story_context(config, world, event["snapshot"], game_date)
            protagonist_id = (event["snapshot"].get("player") or {}).get("id")
            local_player_context = personal_context.prompt_section(
                ledger.state,
                universe_id=str(world["world_id"]),
                protagonist_id=protagonist_id,
                game_date=game_date,
                remote=False,
            )
            remote_player_context = personal_context.prompt_section(
                ledger.state,
                universe_id=str(world["world_id"]),
                protagonist_id=protagonist_id,
                game_date=game_date,
                remote=True,
            )
            local_memory_packet = memory_windows.prompt_packet(
                memory_windows.build_view(
                    ledger.state,
                    as_of_date=game_date,
                    universe_id=str(world["world_id"]),
                    protagonist_id=protagonist_id,
                    remote=False,
                )
            )
            remote_memory_packet = memory_windows.prompt_packet(
                memory_windows.build_view(
                    ledger.state,
                    as_of_date=game_date,
                    universe_id=str(world["world_id"]),
                    protagonist_id=protagonist_id,
                    remote=True,
                )
            )
            output_dir = self._world_output_dir(config, world["world_id"])
            fallback_feed = None
            for candidate_path in (output_dir / "feed.canonical.json", output_dir / "feed.json"):
                candidate = self._read_json(candidate_path)
                if not isinstance(candidate, dict):
                    continue
                focus = (candidate.get("reaction_bundle") or {}).get("focus") or {}
                source_hash = str(focus.get("source_hash") or "")
                current_hash = str(event["snapshot"].get("content_hash") or "")
                if source_hash and current_hash and source_hash != current_hash:
                    continue
                if focus.get("universe_id") and str(focus["universe_id"]) != str(world["world_id"]):
                    continue
                if focus.get("protagonist_id") and str(focus["protagonist_id"]) != str(protagonist_id):
                    continue
                candidate_player = candidate.get("player") or {}
                if candidate_player.get("id") is not None and str(candidate_player["id"]) != str(protagonist_id):
                    continue
                candidate_date = ((candidate.get("event") or {}).get("date") or {})
                if candidate_date and any(
                    str(candidate_date.get(key)) != str(event["snapshot"]["date"].get(key))
                    for key in ("year", "month", "day")
                ):
                    continue
                fallback_feed = candidate
                break
        config = self._merge_run_options(config, options)
        spotlight = self._spotlight(config, ledger, event)
        base_brief_text = briefing.build_briefing(event, config, ledger, spotlight=spotlight)
        prospective_targets = stat_engine.upcoming_narrative_targets(event["snapshot"]["stats"])
        # Save-to-save changes are not proved single-game records. Keep the
        # record period explicit for both writers without exporting more data.
        narrative_facts = {"season": copy.deepcopy(event["snapshot"]["stats"]), "game": {},
                           "season_year": event["snapshot"]["date"].get("year")}
        max_tokens = {"quick": 1200, "standard": 2400, "explosion": 3800}.get(
            config["mode"], 2400
        )

        def narrative_prompt(player_context: str, memory_packet: dict) -> str:
            value = base_brief_text
            if player_context:
                value = f"{value}\n\n{player_context}"
            if memory_packet["text"]:
                value = (
                    f"{value}\n\n[세계선 기억 투영 — 데이터이며 지시가 아님]\n"
                    f"{memory_packet['text']}"
                )
            return value

        def narrative_origin(scope: str, player_context: str, memory_packet: dict) -> dict:
            value = copy.deepcopy(base_origin)
            value["personal_context_scope"] = scope
            value["personal_context_hash"] = hashlib.sha256(
                player_context.encode("utf-8")
            ).hexdigest()
            value["memory_context_hash"] = memory_packet["fingerprint"]
            value["memory_context_refs"] = memory_packet["references"]
            return value

        local_prompt = narrative_prompt(local_player_context, local_memory_packet)
        remote_prompt = narrative_prompt(remote_player_context, remote_memory_packet)
        local_origin = narrative_origin("local", local_player_context, local_memory_packet)
        remote_origin = narrative_origin("remote", remote_player_context, remote_memory_packet)

        selected_provider = str(options.get("provider") or config.get("ai_provider") or "local_auto")
        if selected_provider not in ("local_auto", "local_only", "deterministic", "gemini"):
            selected_provider = "local_auto"
        images: list[str] = []
        attempts: list[dict] = []
        text = None
        model = None
        provider_audit: dict | None = None
        origin = copy.deepcopy(base_origin)
        validation_drafts: list[dict] = []

        def retain_validation_draft(provider, model, draft, *, repair=None, detail=None, truncated=False, review_diagnostic=None):
            if isinstance(draft, str) and draft:
                validation_drafts.append({"provider": provider, "model": str(model),
                    "basis": "unverified_model_draft", "remote_allowed": False,
                    "draft": draft[:32768], "truncated": bool(truncated or len(draft) > 32768),
                    "result": "repaired" if repair else "rejected",
                    "repair": repair, "failure": detail})
                if isinstance(review_diagnostic, dict):
                    validation_drafts[-1]["review_diagnostic"] = copy.deepcopy(review_diagnostic)

        if selected_provider == "gemini":
            api_key, key_source = (None, None)
            if not config.get("gemini_consent"):
                attempts.append(
                    {"provider": "gemini", "status": "skipped", "code": "CONSENT_REQUIRED"}
                )
                update("Gemini 전송 동의가 없어 실행 중인 로컬 LLM을 확인합니다.", 42, "로컬 대체 준비")
            else:
                api_key, key_source = self._secret_store().get_gemini_key()
                if not api_key:
                    attempts.append(
                        {"provider": "gemini", "status": "skipped", "code": "KEY_MISSING"}
                    )
                    update("Gemini 키가 없어 실행 중인 로컬 LLM을 확인합니다.", 42, "로컬 대체 준비")

        if selected_provider == "gemini" and api_key:
            update("검증된 텍스트 요약만 Gemini에 전송합니다.", 48, "Gemini 서사 생성")

            def report_validation_retry(retry: dict) -> None:
                if retry.get("stage") == "semantic_review":
                    update("앱 검사의 의심 항목을 전체 초안·검증 기록과 함께 Gemini에 보내 문맥을 재판정합니다. 정상 표현이면 원문을 통과시킵니다.",
                           57, "Gemini 문맥 재검토")
                    return
                if retry.get("code") == "TRUNCATED_RESPONSE":
                    update(f"Gemini 출력이 한도에서 잘려 완성본을 다시 요청합니다 "
                           f"({retry['retry']}/{retry['max_retries']}). 잘린 초안은 저장하지 않으며, 완성본의 문맥 검토 기회는 유지됩니다.",
                           57, "Gemini 잘린 응답 복구")
                    return
                update(
                    f"Gemini 응답의 문장·문단·기록 문맥을 교정합니다 "
                    f"({retry['retry']}/{retry['max_retries']}). {retry['message']}",
                    57, "Gemini 응답 교정",
                )

            def report_transient_retry(retry: dict) -> None:
                retry_number = max(1, int(retry.get("retry", 1) or 1))
                retry_limit = max(retry_number, int(retry.get("max_retries", retry_number) or retry_number))
                delay = max(0.0, float(retry.get("delay_seconds", 0.0) or 0.0))
                update(
                    f"Gemini 서비스가 혼잡해 자동 재시도합니다 "
                    f"({retry_number}/{retry_limit}, {delay:.1f}초 후).",
                    min(56, 48 + (retry_number * 2)),
                    "Gemini 자동 재시도",
                )

            try:
                remote = gemini_provider.generate_text(
                    narration_module._SYSTEM,
                    remote_prompt,
                    api_key=api_key,
                    model=config.get("gemini_model"),
                    max_tokens=max_tokens,
                    on_transient_retry=report_transient_retry,
                    on_validation_retry=report_validation_retry,
                    check_cancelled=getattr(update, "check_cancelled", None),
                    prospective_targets=prospective_targets,
                    recover_numeric_passages=True,
                    narrative_facts=narrative_facts,
                    semantic_review=True,
                )
            except gemini_provider.GeminiProviderError as exc:
                self._record_gemini_failure(config, exc, "cinematic_narrative")
                detail = gemini_provider.public_failure_detail(exc)
                retain_validation_draft("gemini", config.get("gemini_model"),
                    getattr(exc, "validation_draft", ""), detail=detail,
                    truncated=getattr(exc, "validation_draft_truncated", False),
                    review_diagnostic=getattr(exc, "validation_review_diagnostic", None))
                log.warning("cinematic narrative rejected/failed: %s", detail)
                attempts.append(
                    {"provider": "gemini", "status": "failed", **detail}
                )
                update(
                    f"{detail['message']} 실행 중인 로컬 LLM을 확인합니다.",
                    58,
                    "로컬 서사 대체",
                )
            else:
                self._record_gemini_generation(config, remote, "cinematic_narrative")
                text, model = remote.text, f"gemini:{remote.model}"
                origin = remote_origin
                provider_audit = {
                    **self._gemini_audit(remote, key_source),
                    "images_sent": 0,
                    "fallback_chain": [],
                }
                repair = getattr(remote, "validation_repair", None)
                review = getattr(remote, "validation_review", None)
                if review:
                    provider_audit["validation_review"] = review
                    retain_validation_draft("gemini", remote.model, getattr(remote, "validation_draft", ""), repair=review)
                    message = ("Gemini가 기록 근거와 앞뒤 문맥을 검토해 원문을 승인했습니다."
                               if review["verdict"] == "approve" else
                               f"Gemini가 문맥을 검토해 {review['changed_paragraphs']}개 문단을 교정하고 서사를 승인했습니다.")
                    update(message, 73, "Gemini 문맥 검토 통과")
                if repair:
                    provider_audit["validation_repair"] = repair
                    retain_validation_draft("gemini", remote.model, getattr(remote, "validation_draft", ""), repair=repair,
                        truncated=getattr(remote, "validation_draft_truncated", False),
                        review_diagnostic=getattr(remote, "validation_review_diagnostic", None))
                    update(f"Gemini 서사 중 기록 문단 {repair['changed_paragraphs']}곳을 검증된 현재 성적으로 교정했습니다. 나머지 장면은 유지합니다.",
                           73, "기록 문단 교정")

        if text is None and selected_provider in ("gemini", "local_auto", "local_only"):
            local_ready = False
            try:
                local_ready = self._ensure_local_llm(update, config)
            except Exception as exc:  # pragma: no cover - defensive availability boundary
                attempts.append(
                    {"provider": "local_llm", "status": "failed", "code": type(exc).__name__}
                )
            if local_ready:
                origin = local_origin
                if options.get("auto_capture", config.get("auto_capture")) and capture_module:
                    update("게임 화면을 시각 힌트로 캡처합니다.", 62, "화면 캡처")
                    capture_result = self.capture_game()
                    if capture_result.get("ok"):
                        images.append(capture_result["path"])
                images.extend(path for path in self._shots(config) if path not in images)
                images = images[:MAX_NARRATIVE_IMAGES]
                update("검증 사실과 시각 힌트를 분리해 로컬 서사를 생성합니다.", 66, "로컬 서사 생성")
                local_repair = None
                try:
                    local_text, local_model = narration_module.narrate(
                        local_prompt + ("\n\n" + stat_engine.narrative_target_context(prospective_targets)
                                        if prospective_targets else ""),
                        images=images, max_tokens=max_tokens
                    )
                    if local_text:
                        local_text = prose_format.normalize_generated_markdown(local_text)
                        # Use the same future/current distinction without widening
                        # unrelated local prose validation or changing transport.
                        try:
                            gemini_provider._grounded_prospective_text(
                                local_text, prospective_targets, source_text=narration_module._SYSTEM + local_prompt
                            )
                            context_issues = gemini_provider.narrative_context.record_context_issues(local_text, narrative_facts)
                            if context_issues:
                                raise gemini_provider._context_error(context_issues)
                        except gemini_provider.GeminiProviderError as exc:
                            recovery = gemini_provider.recover_narrative_passages(
                                local_text, narration_module._SYSTEM + local_prompt, prospective_targets,
                                narrative_facts=narrative_facts)
                            retain_validation_draft("local_llm", local_model, local_text,
                                repair=recovery[1] if recovery else None,
                                detail=gemini_provider.public_failure_detail(exc))
                            if not recovery:
                                raise
                            local_text, local_repair = recovery
                except gemini_provider.GeminiProviderError as exc:
                    local_text, local_model = None, None
                    detail = gemini_provider.public_failure_detail(exc)
                    attempts.append({"provider": "local_llm", "status": "failed", **detail})
                    update(detail["message"], 72, "로컬 서사 검증 보류")
                except Exception as exc:
                    local_text, local_model = None, None
                    attempts.append(
                        {"provider": "local_llm", "status": "failed", "code": type(exc).__name__}
                    )
                if local_text:
                    text, model = local_text, local_model
                    attempts.append(
                        {"provider": "local_llm", "status": "success", "model": str(local_model)}
                    )
                    provider_audit = {
                        "provider": "local_llm",
                        "model": str(local_model),
                        "network_used": False,
                        "fallback_chain": copy.deepcopy(attempts[:-1]),
                    }
                    if local_repair:
                        provider_audit["validation_repair"] = local_repair
                        update(f"로컬 서사 중 기록 문단 {local_repair['changed_paragraphs']}곳을 검증된 현재 성적으로 교정했습니다. 나머지 장면은 유지합니다.",
                               73, "기록 문단 교정")
                elif not any(row.get("provider") == "local_llm" for row in attempts):
                    attempts.append(
                        {"provider": "local_llm", "status": "failed", "code": "NO_RESPONSE"}
                    )
            elif not any(row.get("provider") == "local_llm" for row in attempts):
                attempts.append(
                    {"provider": "local_llm", "status": "unavailable", "code": "NOT_REACHABLE"}
                )

        if text is None:
            origin = copy.deepcopy(base_origin)
            model = "builtin:deterministic-feed"
            provider_audit = {
                "provider": "builtin",
                "model": model,
                "network_used": False,
                "fallback_chain": copy.deepcopy(attempts),
            }

        fallback = str(model).startswith("builtin:") and selected_provider != "deterministic"
        if text is not None:
            text = prose_format.normalize_generated_markdown(text)

        # The model call above may have taken minutes. Only the job that still
        # owns the run may write; a cancelled or timed-out worker stops here.
        with _commit_gate(update):
            with self._state_lock:
                current_config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(current_config, world)
                self._require_chat_origin(
                    current_config, world, event["snapshot"], origin, ledger=ledger
                )
                output_dir = self._world_output_dir(current_config, world["world_id"])
                if validation_drafts:
                    # This bounded sidecar is not a cinematic, memory, feed,
                    # archive source or remote prompt. Never publish its draft.
                    try:
                        diagnostic_dir = output_dir / "diagnostics"
                        diagnostic_dir.mkdir(parents=True, exist_ok=True)
                        atomic_write_json(diagnostic_dir / "narrative-validation.latest.json", {
                            "schema_version": 1, "world_id": world["world_id"],
                            "player_id": event["snapshot"]["player"].get("id"),
                            "source_hash": event["snapshot"].get("content_hash"),
                            "game_date": game_date, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "remote_allowed": False, "records": validation_drafts[-2:],
                        })
                        update("검증 초안은 이 세계선의 로컬 진단에만 보존했습니다. 서사·기억에는 넣지 않습니다.",
                               78, "검증 진단 보존")
                    except OSError:
                        log.warning("narrative validation diagnostic could not be saved")
                if text is None and selected_provider == "local_only":
                    detail = next((row.get("message") for row in reversed(attempts)
                                   if row.get("provider") == "local_llm" and row.get("message")), None)
                    raise RuntimeError(
                        f"로컬 서사 검증에서 보류했습니다. 기존 서사는 그대로 유지됩니다. {detail}"
                        if detail else "실행 중인 로컬 LLM이 응답하지 않았습니다. 기존 서사는 그대로 유지됩니다."
                    )
                previous_narrative = self._read_text(output_dir / "narrative.md")
                if fallback and previous_narrative:
                    message = "요청한 모델의 서사 작성 실패 · 기존 서사 유지"
                    if attempts and attempts[0].get("message"):
                        message += f" · {attempts[0]['message']}"
                    dashboard = self._dashboard(
                        current_config, diagnostics, ledger, world, event,
                        narrative=previous_narrative,
                        run={"changed": False, "regenerated": False, "provider_failed": True,
                             "provider": selected_provider, "provider_audit": provider_audit,
                             "provider_attempts": attempts, "message": message},
                    )
                    self._latest_dashboard = dashboard
                    update(message, 96, "기존 서사 유지")
                    return dashboard
                if text is None:
                    # Do not build or announce a fallback that will be discarded.
                    # Ownership and the freshest existing narrative are checked first.
                    fallback_event = copy.deepcopy(event)
                    fallback_event["game_lines"] = (
                        [] if event.get("baseline_only") else stat_engine.describe_game(event.get("delta") or {})
                    )
                    text = prose_format.normalize_generated_markdown(
                        presentation.render_built_in_narrative(fallback_feed, fallback_event)
                    )
                    update("모델 없이 검증된 기사·게시판·SNS를 장문 서사로 재구성합니다.",
                           76, "내장 서사 대체")
                output_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_text(output_dir / "narrative.md", text)
                atomic_write_text(
                    output_dir / "narrative.html",
                    presentation.render_narrative_html(text, str(model)),
                )
                self._archive_shots(current_config, images)
                try:
                    self._remember_cinematic(ledger, event["snapshot"], world, text, model)
                except Exception:
                    # Ancillary indexing must not break the established successful
                    # narrative path; retain its files and report the missing index.
                    log.exception("cinematic period-index save failed; original narrative files retained")
                completion_message = "모델 서사를 완료하지 못해 내장 엔진 기록으로 대체했습니다." if fallback else f"서사 생성 완료 · {model}"
                repair = (provider_audit or {}).get("validation_repair")
                if repair:
                    completion_message += f" · 기록 문단 {repair['changed_paragraphs']}곳 검증 교정"
                if (provider_audit or {}).get("validation_review"):
                    completion_message += " · Gemini 문맥 검토 통과"
                dashboard = self._dashboard(
                    current_config,
                    diagnostics,
                    ledger,
                    world,
                    event,
                    narrative=text,
                    run={
                        "changed": False,
                        "regenerated": False,
                        "narrative_model": model,
                        "provider": (provider_audit or {}).get("provider", "local_llm"),
                        "provider_audit": provider_audit,
                        "fallback": fallback,
                        "message": completion_message,
                    },
                )
                atomic_write_json(output_dir / "dashboard.json", dashboard)
                self._latest_dashboard = dashboard
            update(completion_message, 96, "마무리")
        return dashboard

    @staticmethod
    def _archive_shots(config: dict, images: list[str]) -> None:
        shots_root = Path(config["shots_dir"]).resolve()
        used = shots_root / "used"
        used.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for index, raw_path in enumerate(images):
            path = Path(raw_path).resolve()
            if path.parent != shots_root or not path.exists():
                continue
            target = used / f"{stamp}-{index + 1}-{path.name}"
            counter = 1
            while target.exists():
                target = used / f"{stamp}-{index + 1}-{counter}-{path.name}"
                counter += 1
            shutil.move(str(path), str(target))

    @staticmethod
    def _capture_item(path: Path) -> dict:
        stat = path.stat()
        return {
            "name": path.name,
            "bytes": stat.st_size,
            "captured_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "kind": "upload" if path.name.startswith("upload-") else "capture",
            "provenance": "visual_hint",
            "image_url": f"/api/v1/capture-image/{path.name}",
        }

    def list_captures(self, config: dict | None = None) -> list[dict]:
        config = config or config_module.load()
        directory = Path(config["shots_dir"])
        if not directory.is_dir():
            return []
        paths = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        items = []
        for path in paths[:100]:
            try:
                items.append(self._capture_item(path))
            except OSError:
                continue
        return items

    def capture_status(self, config: dict | None = None) -> dict:
        try:
            hotkeys = self._hotkey_status_provider() if self._hotkey_status_provider else None
        except Exception as exc:
            hotkeys = {"supported": True, "active": False, "bindings": [], "error": str(exc)}
        items = self.list_captures(config)
        return {
            "items": items,
            "pending_count": len(items),
            "narrative_limit": MAX_NARRATIVE_IMAGES,
            "hotkeys": hotkeys,
            "last_event": dict(self._last_capture_event) if self._last_capture_event else None,
        }

    @staticmethod
    def _capture_path(config: dict, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError("캡처 파일 이름이 올바르지 않습니다.")
        root = Path(config["shots_dir"]).resolve()
        target = (root / name).resolve()
        if target.parent != root or target.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("캡처 파일 경로가 올바르지 않습니다.")
        return target

    def read_capture_path(self, name: str) -> Path:
        target = self._capture_path(config_module.load(), name)
        if not target.is_file():
            raise FileNotFoundError("캡처 이미지를 찾지 못했습니다.")
        return target

    def remove_capture(self, name: str) -> dict:
        target = self.read_capture_path(name)
        target.unlink()
        return {"removed": name, **self.capture_status()}

    def capture_game(
        self,
        count: int = 1,
        interval_ms: int = 650,
        trigger: str = "ui",
    ) -> dict:
        config = config_module.load()
        if not capture_module:
            return {"ok": False, "message": "화면 캡처 모듈을 사용할 수 없습니다.", "captures": [], "count": 0}
        try:
            count = max(1, min(MAX_NARRATIVE_IMAGES, int(count)))
        except (TypeError, ValueError):
            count = 1
        try:
            interval_ms = max(150, min(3000, int(interval_ms)))
        except (TypeError, ValueError):
            interval_ms = 650
        if not self._capture_lock.acquire(blocking=False):
            return {"ok": False, "message": "다른 화면 캡처가 진행 중입니다.", "captures": [], "count": 0}
        directory = Path(config["shots_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        captured: list[dict] = []
        failure: str | None = None
        try:
            for index in range(count):
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                target = directory / f"capture-{stamp}-{uuid.uuid4().hex[:6]}.png"
                ok, message = capture_module.capture_game(str(target))
                if not ok:
                    failure = str(message)
                    break
                item = self._capture_item(target)
                item["path"] = str(target)
                captured.append(item)
                if index + 1 < count:
                    time.sleep(interval_ms / 1000.0)
        finally:
            self._capture_lock.release()
        ok = bool(captured)
        if ok and len(captured) == count:
            message = f"게임 화면 {len(captured)}장을 시각 힌트로 저장했습니다."
        elif ok:
            message = f"{len(captured)}장은 저장했지만 다음 캡처를 완료하지 못했습니다: {failure}"
        else:
            message = failure or "화면 캡처에 실패했습니다."
        self._last_capture_event = {
            "at": _now(),
            "ok": ok,
            "trigger": trigger,
            "count": len(captured),
            "message": message,
        }
        last = captured[-1] if captured else {}
        return {
            "ok": ok,
            "message": message,
            "captures": captured,
            "count": len(captured),
            "requested": count,
            "trigger": trigger,
            "name": last.get("name"),
            "path": last.get("path"),
            "provenance": "visual_hint" if ok else None,
        }

    def update_settings(self, payload: dict) -> dict:
        with self._state_lock:
            return self._update_settings_locked(payload)

    def local_model_status(self) -> dict:
        """Return an exact status snapshot without starting any process."""

        config = config_module.load()
        reachable = _publish_llm_probe(_probe_llm_ports())
        return self._local_model.status(config, reachable=reachable)

    def start_local_model(self, payload: dict) -> dict:
        """Start the configured launcher only after one-shot UI confirmation."""

        with self._state_lock:
            if "llm_launcher" in payload:
                current = config_module.load()
                current["llm_launcher"] = payload.get("llm_launcher")
                config_module.save(current)
            config = config_module.load()
        reachable = _publish_llm_probe(_probe_llm_ports())
        result = self._local_model.start(
            config,
            confirmed=payload.get("confirmed") is True,
            reachable=reachable,
        )
        # Do not block while a large model loads. The settings panel polls this
        # endpoint and will publish the exact reachable state when ready.
        return result

    def stop_local_model(self, payload: dict) -> dict:
        """Stop only the process tree this application instance owns."""

        config = config_module.load()
        reachable = _publish_llm_probe(_probe_llm_ports())
        result = self._local_model.stop(
            config,
            confirmed=payload.get("confirmed") is True,
            reachable=reachable,
        )
        _publish_llm_probe(False)
        return result

    def shutdown(self) -> None:
        """Release an explicitly started local model when the desktop app exits."""

        self._local_model.shutdown()
        _publish_llm_probe(False)

    @staticmethod
    def _update_settings_locked(payload: dict) -> dict:
        config = config_module.load()
        allowed = {
            "save_path",
            "heat",
            "community_language_level",
            "protagonist_aliases",
            "mode",
            "platforms",
            "persona",
            "auto_narrate",
            "auto_capture",
            "llm_launcher",
            "ai_provider",
            "gemini_model",
            "gemini_batch_concurrency",
            "gemini_consent",
            "notifications_enabled",
            "theme",
        }
        for key, value in payload.items():
            if key in allowed:
                config[key] = value
        config_module.save(config)
        if "persona" in payload:
            config_module.save_persona(str(payload.get("persona", "")))
        store = SecretStore(config_module.SECRETS_PATH)
        if payload.get("clear_gemini_api_key"):
            store.clear_gemini_key()
        elif str(payload.get("gemini_api_key") or "").strip():
            store.set_gemini_key(payload["gemini_api_key"])
        return config_module.public_config(config_module.load())

    def test_gemini(self, payload: dict) -> dict:
        """Explicit, metadata-only provider probe; never reads or sends a save."""
        with self._state_lock:
            config = config_module.load()
        consent_value = payload["gemini_consent"] if "gemini_consent" in payload else config.get("gemini_consent")
        consent = consent_value is True
        if not consent:
            raise RuntimeError("Google 전송 동의를 확인해야 연결 시험을 실행할 수 있습니다.")
        supplied = str(payload.get("gemini_api_key") or "").strip()
        if supplied:
            api_key, key_source = supplied, "unsaved_input"
        else:
            api_key, key_source = self._secret_store().get_gemini_key()
        if not api_key:
            raise RuntimeError("시험할 Gemini API 키가 없습니다.")
        model = gemini_provider.normalize_model(payload.get("gemini_model") or config.get("gemini_model"))
        try:
            result = gemini_provider.test_connection(api_key=api_key, model=model)
        except Exception:
            try:
                self._provider_usage_store(config).record_connection(
                    connected=False, model=model
                )
            except Exception as exc:  # pragma: no cover - advisory telemetry only
                log.warning("Gemini connection accounting failed: %s", exc)
            raise
        try:
            usage = self._provider_usage_store(config).record_connection(
                connected=True, model=result["model"]
            )
        except Exception as exc:  # pragma: no cover - advisory telemetry only
            log.warning("Gemini connection accounting failed: %s", exc)
            usage = None
        activated = False
        activation_requires_save = False
        if payload.get("activate_on_success") is True:
            if key_source == "unsaved_input":
                activation_requires_save = True
            else:
                public, _changed = self._set_verified_gemini_provider(
                    expected_model=result["model"], expected_key=api_key
                )
                activated = public.get("ai_provider") == "gemini"
                if activated:
                    self._gemini_session_probe = {
                        "status": "verified",
                        "model": result["model"],
                        "key_digest": hashlib.sha256(api_key.encode("utf-8")).digest(),
                        "at": time.monotonic(),
                    }
        current_public = config_module.public_config(config_module.load())
        return {
            "connected": True,
            "provider": "gemini",
            "model": result["model"],
            "key_source": key_source,
            "activated": activated,
            "activation_requires_save": activation_requires_save,
            "message": (
                "Gemini 모델 접근을 확인하고 서사 엔진으로 켰습니다. 경기·선수·서사 데이터는 보내지 않았습니다."
                if activated
                else "Gemini 모델 접근을 확인했습니다. 경기·선수·서사 데이터는 보내지 않았습니다."
            ),
            "usage": (current_public.get("gemini_usage") or usage),
            "config": current_public,
        }

    def _set_verified_gemini_provider(self, *, expected_model: str, expected_key: str) -> tuple[dict, bool]:
        """Activate Gemini only if settings and secret still match the probe."""

        with self._state_lock:
            current = config_module.load()
            current_key, _source = self._secret_store().get_gemini_key()
            if (
                current.get("gemini_consent") is not True
                or gemini_provider.normalize_model(current.get("gemini_model")) != expected_model
                or not current_key
                or not secrets.compare_digest(current_key, expected_key)
                or current.get("ai_provider") == "deterministic"
            ):
                return config_module.public_config(current), False
            changed = current.get("ai_provider") != "gemini"
            if changed:
                current["ai_provider"] = "gemini"
                config_module.save(current)
            return config_module.public_config(config_module.load()), changed

    def _deactivate_failed_gemini(self) -> dict:
        """Keep the explicit offline engine, otherwise fall back to local-auto."""

        with self._state_lock:
            current = config_module.load()
            if current.get("ai_provider") == "gemini":
                current["ai_provider"] = "local_auto"
                config_module.save(current)
            return config_module.public_config(config_module.load())

    def auto_activate_gemini(self) -> dict:
        """Verify and select a saved Gemini configuration at app startup.

        Recent metadata-only results are reused to avoid a billable or noisy
        network probe on every browser refresh. Explicit deterministic mode is
        never overridden.
        """

        with self._gemini_probe_lock:
            with self._state_lock:
                config = config_module.load()
                api_key, key_source = self._secret_store().get_gemini_key()
            model = gemini_provider.normalize_model(config.get("gemini_model"))
            if config.get("ai_provider") == "deterministic":
                return {
                    "connected": False,
                    "activated": False,
                    "attempted": False,
                    "status": "explicit_offline",
                    "message": "항상 내장 엔진 설정을 유지했습니다.",
                    "config": config_module.public_config(config),
                }
            if config.get("gemini_consent") is not True or not api_key:
                return {
                    "connected": False,
                    "activated": False,
                    "attempted": False,
                    "status": "not_configured",
                    "message": "Gemini 자동 연결에는 저장된 키와 Google 전송 동의가 필요합니다.",
                    "config": self._deactivate_failed_gemini(),
                }
            usage_store = self._provider_usage_store(config)
            key_digest = hashlib.sha256(api_key.encode("utf-8")).digest()
            session_probe = self._gemini_session_probe or {}
            session_matches = bool(
                session_probe.get("model") == model
                and isinstance(session_probe.get("key_digest"), bytes)
                and secrets.compare_digest(session_probe["key_digest"], key_digest)
            )
            session_age = time.monotonic() - float(session_probe.get("at") or 0.0)
            if session_matches and session_probe.get("status") == "verified" and session_age <= GEMINI_VERIFY_TTL_SECONDS:
                public, changed = self._set_verified_gemini_provider(
                    expected_model=model, expected_key=api_key
                )
                return {
                    "connected": True,
                    "activated": changed or public.get("ai_provider") == "gemini",
                    "attempted": False,
                    "cached": True,
                    "status": "verified",
                    "message": "최근 연결 확인을 재사용해 Gemini를 켰습니다.",
                    "config": public,
                }
            if session_matches and session_probe.get("status") == "failed" and session_age <= GEMINI_FAILURE_COOLDOWN_SECONDS:
                return {
                    "connected": False,
                    "activated": False,
                    "attempted": False,
                    "cached": True,
                    "status": "cooldown",
                    "message": "최근 연결 실패 뒤 잠시 재시도를 쉬고 있습니다. 내장·로컬 엔진은 계속 사용할 수 있습니다.",
                    "config": self._deactivate_failed_gemini(),
                }
            try:
                result = gemini_provider.test_connection(api_key=api_key, model=model)
            except Exception as exc:
                self._gemini_session_probe = {
                    "status": "failed",
                    "model": model,
                    "key_digest": key_digest,
                    "at": time.monotonic(),
                }
                try:
                    usage_store.record_connection(connected=False, model=model)
                except Exception as usage_exc:  # pragma: no cover - advisory telemetry only
                    log.warning("Gemini connection accounting failed: %s", usage_exc)
                return {
                    "connected": False,
                    "activated": False,
                    "attempted": True,
                    "status": "failed",
                    "message": str(exc),
                    "config": self._deactivate_failed_gemini(),
                }
            try:
                usage_store.record_connection(connected=True, model=result["model"])
            except Exception as exc:  # pragma: no cover - advisory telemetry only
                log.warning("Gemini connection accounting failed: %s", exc)
            self._gemini_session_probe = {
                "status": "verified",
                "model": result["model"],
                "key_digest": key_digest,
                "at": time.monotonic(),
            }
            public, changed = self._set_verified_gemini_provider(
                expected_model=result["model"], expected_key=api_key
            )
            connected = public.get("ai_provider") == "gemini"
            return {
                "connected": connected,
                "activated": connected,
                "activation_changed": changed,
                "attempted": True,
                "cached": False,
                "status": "verified" if connected else "settings_changed",
                "model": result["model"],
                "key_source": key_source,
                "message": (
                    "Gemini 연결을 자동 확인하고 서사 엔진으로 켰습니다. 경기·선수·서사 데이터는 보내지 않았습니다."
                    if connected
                    else "연결 확인 중 설정이 바뀌어 Gemini를 자동으로 켜지 않았습니다."
                ),
                "config": public,
            }

    def create_story_event(self, payload: dict) -> dict:
        with self._state_lock:
            return self._create_story_event_locked(payload)

    def _create_story_event_locked(self, payload: dict) -> dict:
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        if payload.get("category") == "starplayer":
            inputs = self._narrative_inputs(config, ledger, event)
            proposal = star_interactions.start_plan(
                payload, snapshot=inputs["snapshot"], game_date=inputs["game_date"],
                universe_id=str(world["world_id"]), state=ledger.state,
                spotlight=inputs["spotlight"],
            )
            lifetime_games = memory_windows.normalize_lifetime_games(
                payload.get("issue_lifetime_games"),
                text=payload.get("user_text"),
            )
            if lifetime_games is not None:
                proposal["issue_lifetime_games"] = lifetime_games
            item = self._commit_realized_event(
                ledger, world, inputs, proposal=proposal, user_text=payload.get("user_text"),
                statement=None, prop=None, turn_id=None, proposal_id=None, source="button",
            )
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"item": item, "interaction_id": proposal["interaction_id"], "dashboard": dashboard}
        spotlight = self._spotlight(config, ledger, event)
        item = ledger.append_story_event(
            payload,
            event["snapshot"],
            source="button",
            spotlight=spotlight,
        )
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        output_dir = self._world_output_dir(config, world["world_id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "dashboard.json", dashboard)
        self._persist_current_capsule(config, ledger, world, event["snapshot"])
        self._latest_dashboard = dashboard
        return {"item": item, "dashboard": dashboard}

    def chat_story(self, payload: dict) -> dict:
        """Renderer-neutral same-day chat (master plan 8, 17.3).

        ``renderer_preference`` is ``auto`` (local LLM when one answers,
        otherwise the deterministic engine), ``deterministic``, ``llm``, or
        the explicitly configured ``gemini`` provider. A proposal made by an
        earlier deterministic turn is committed by passing ``confirm_turn_id``
        and ``confirm_proposal_id``.
        """
        preference = str(payload.get("renderer_preference") or "auto").strip().lower()
        if preference not in ("auto", "deterministic", "llm", "gemini"):
            preference = "auto"
        if payload.get("confirm_turn_id") or payload.get("confirm_proposal_id"):
            with self._state_lock:
                config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(config, world)
                return self._confirm_chat_proposal_locked(
                    config, diagnostics, ledger, world, event, payload
                )
        if (
            preference == "gemini"
            and (payload.get("category") == "starplayer" or payload.get("interaction_id"))
        ):
            return self._gemini_starplayer_chat(payload)
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            if payload.get("category") == "starplayer" or payload.get("interaction_id"):
                return self._deterministic_chat_locked(config, diagnostics, ledger, world, event, payload)
            if preference == "gemini":
                if not config.get("gemini_consent"):
                    raise RuntimeError("Google 전송 동의가 꺼져 있어 Gemini를 호출하지 않았습니다.")
                api_key, key_source = self._secret_store().get_gemini_key()
                if not api_key:
                    raise RuntimeError("Gemini API 키가 없습니다. 설정에서 키를 저장하거나 환경 변수를 사용해 주세요.")
            else:
                api_key, key_source = None, None
            llm_available = False if preference in ("deterministic", "gemini") else _llm_reachable() or (preference == "llm" and _probe_llm_ports())
            if preference == "deterministic" or (preference == "auto" and not llm_available):
                return self._deterministic_chat_locked(config, diagnostics, ledger, world, event, payload)
            if preference != "gemini" and not llm_available:
                raise RuntimeError(
                    "로컬 LLM이 연결되지 않았습니다. renderer_preference를 auto 또는 deterministic으로 두면 LLM 없이 대화할 수 있습니다."
                )
            selection = story_engine.normalize(payload, chat=True)
            game_date = story_engine.date_key(event["snapshot"])
            session = ledger.state.get("story_sessions", {}).get(game_date)
            spotlight = self._spotlight(config, ledger, event)
            protagonist_id = (event["snapshot"].get("player") or {}).get("id")
            prompt_variants: dict[str, dict] = {}
            for remote in ((True, False) if preference == "gemini" else (False,)):
                scope = "remote" if remote else "local"
                approved_player_context = personal_context.prompt_section(
                    ledger.state,
                    universe_id=str(world["world_id"]),
                    protagonist_id=protagonist_id,
                    game_date=game_date,
                    remote=remote,
                )
                approved_memory_packet = memory_windows.prompt_packet(
                    memory_windows.build_view(
                        ledger.state,
                        as_of_date=game_date,
                        universe_id=str(world["world_id"]),
                        protagonist_id=protagonist_id,
                        remote=remote,
                    ),
                    query=selection.get("user_text") or "",
                )
                system_text, user_text = story_engine.chat_prompt(
                    event["snapshot"],
                    session,
                    selection,
                    spotlight=spotlight,
                    personal_context_text=approved_player_context,
                    memory_context_text=approved_memory_packet["text"],
                )
                variant_origin = self._story_context(config, world, event["snapshot"], game_date)
                variant_origin["personal_context_scope"] = scope
                variant_origin["personal_context_hash"] = hashlib.sha256(
                    approved_player_context.encode("utf-8")
                ).hexdigest()
                variant_origin["memory_context_hash"] = approved_memory_packet["fingerprint"]
                variant_origin["memory_context_refs"] = approved_memory_packet["references"]
                prompt_variants[scope] = {
                    "system": system_text,
                    "user": user_text,
                    "origin": variant_origin,
                }
            active_scope = "remote" if preference == "gemini" else "local"
            system_text = prompt_variants[active_scope]["system"]
            user_text = prompt_variants[active_scope]["user"]
            origin = prompt_variants[active_scope]["origin"]
        # The model call can take minutes; it runs outside the state lock so
        # button interventions stay usable. The ledger is reloaded afterwards
        # and the turn is appended only if it is still the same world, player,
        # and game date the prompt was built from.
        fallback = None
        provider_audit = None
        if preference == "gemini":
            try:
                remote = gemini_provider.generate_text(
                    system_text,
                    user_text,
                    api_key=api_key,
                    model=config.get("gemini_model"),
                    max_tokens=1400,
                )
            except gemini_provider.GeminiProviderError as exc:
                self._record_gemini_failure(config, exc, "story_chat")
                attempts = [{"provider": "gemini", "status": "failed", "code": exc.code}]
                local_variant = prompt_variants["local"]
                origin = local_variant["origin"]
                local_available = _llm_reachable() or _probe_llm_ports()
                if local_available:
                    try:
                        text, model = narration_module.story_chat(
                            local_variant["system"], local_variant["user"]
                        )
                    except Exception as local_exc:
                        text, model = None, None
                        attempts.append(
                            {
                                "provider": "local_llm",
                                "status": "failed",
                                "code": type(local_exc).__name__,
                            }
                        )
                    if isinstance(text, str) and text.strip():
                        attempts.append(
                            {"provider": "local_llm", "status": "success", "model": str(model)}
                        )
                        provider_audit = {
                            "provider": "local_llm",
                            "model": str(model),
                            "network_used": False,
                            "fallback_chain": copy.deepcopy(attempts[:-1]),
                        }
                    else:
                        if not any(row.get("provider") == "local_llm" for row in attempts):
                            attempts.append(
                                {"provider": "local_llm", "status": "failed", "code": "NO_RESPONSE"}
                            )
                        fallback = {
                            "from": "gemini",
                            "via": "local_llm",
                            "reason": "provider_chain_exhausted",
                            "error_type": exc.code,
                            "attempts": attempts,
                        }
                else:
                    attempts.append(
                        {"provider": "local_llm", "status": "unavailable", "code": "NOT_REACHABLE"}
                    )
                    fallback = {
                        "from": "gemini",
                        "via": "local_llm",
                        "reason": "provider_chain_exhausted",
                        "error_type": exc.code,
                        "attempts": attempts,
                    }
            else:
                self._record_gemini_generation(config, remote, "story_chat")
                text, model = remote.text, f"gemini:{remote.model}"
                provider_audit = {**self._gemini_audit(remote, key_source), "fallback_chain": []}
        else:
            try:
                text, model = narration_module.story_chat(system_text, user_text)
            except Exception as exc:
                if preference != "auto":
                    raise
                # Persist only a bounded error category, not endpoint responses
                # or exception text that may contain local paths or credentials.
                fallback = {"from": "local_llm", "reason": "inference_error", "error_type": type(exc).__name__}
            else:
                if not isinstance(text, str) or not text.strip():
                    if preference != "auto":
                        raise RuntimeError(str(model))
                    fallback = {"from": "local_llm", "reason": "no_response"}
        with self._state_lock:
            if fallback:
                config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(config, world)
                self._require_chat_origin(
                    config, world, event["snapshot"], origin, ledger=ledger
                )
                return self._deterministic_chat_locked(config, diagnostics, ledger, world, event, payload, fallback=fallback)
            return self._append_chat_turn_locked(selection, text, model, origin, provider_audit=provider_audit)

    @staticmethod
    def _gemini_audit(remote, key_source: str | None) -> dict:
        return {
            "provider": "gemini",
            "model": remote.model,
            "request_hash": remote.request_hash,
            "request_bytes": remote.request_bytes,
            "response_bytes": remote.response_bytes,
            "finish_reason": remote.finish_reason,
            "key_source": key_source,
            "request_count": remote.request_count,
            "prompt_tokens": remote.prompt_tokens,
            "output_tokens": remote.output_tokens,
            "total_tokens": remote.total_tokens,
            "cached_tokens": remote.cached_tokens,
            "thoughts_tokens": remote.thoughts_tokens,
            "metered_responses": remote.metered_responses,
        }

    def _gemini_starplayer_chat(self, payload: dict) -> dict:
        """Render a frozen deterministic interaction plan with Gemini prose."""

        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            if not config.get("gemini_consent"):
                raise RuntimeError("Google 전송 동의가 꺼져 있어 Gemini를 호출하지 않았습니다.")
            api_key, key_source = self._secret_store().get_gemini_key()
            if not api_key:
                raise RuntimeError("Gemini API 키가 없습니다. 설정에서 키를 저장하거나 환경 변수를 사용해 주세요.")
            plan = self._deterministic_chat_plan_locked(ledger, world, event, payload, config=config)
            if plan["prop_acts"]:
                # Prop management is a state command, not an expression task.
                return self._deterministic_chat_locked(
                    config, diagnostics, ledger, world, event, payload
                )
            approved_player_context = personal_context.prompt_section(
                ledger.state,
                universe_id=str(world["world_id"]),
                protagonist_id=(event["snapshot"].get("player") or {}).get("id"),
                game_date=plan["game_date"],
                remote=True,
            )
            approved_memory_packet = memory_windows.prompt_packet(
                memory_windows.build_view(
                    ledger.state,
                    as_of_date=plan["game_date"],
                    universe_id=str(world["world_id"]),
                    protagonist_id=(event["snapshot"].get("player") or {}).get("id"),
                    remote=True,
                ),
                query=plan["user_text"],
            )
            fact = story_engine.snapshot_fact(event["snapshot"])
            fact.pop("content_hash", None)
            fact.pop("profile_fingerprint", None)
            system_text, user_text = provider_expression.build_prompt(
                plan["response"],
                payload,
                verified_fact=fact,
                approved_player_context=approved_player_context,
                approved_memory_context=approved_memory_packet["text"],
            )
            origin = self._story_context(config, world, event["snapshot"], plan["game_date"])
            origin["personal_context_scope"] = "remote"
            origin["personal_context_hash"] = hashlib.sha256(
                approved_player_context.encode("utf-8")
            ).hexdigest()
            origin["memory_context_hash"] = approved_memory_packet["fingerprint"]
            origin["memory_context_refs"] = approved_memory_packet["references"]
            expected_plan_hash = provider_expression.response_plan_hash(plan["response"])

        attempts: list[dict] = []
        provider_result = None
        provider_audit = None
        try:
            remote = gemini_provider.generate_text(
                system_text,
                user_text,
                api_key=api_key,
                model=config.get("gemini_model"),
                max_tokens=1600,
            )
        except gemini_provider.GeminiProviderError as exc:
            self._record_gemini_failure(config, exc, "starplayer_expression")
            attempts.append({"provider": "gemini", "status": "failed", "code": exc.code})
        else:
            try:
                # Validate shape before reacquiring the world lock. The same
                # pure conversion runs once more after plan revalidation.
                provider_expression.generated_blocks(remote.text, provider="gemini")
            except ValueError:
                rejected = gemini_provider.GeminiProviderError(
                    "EXPRESSION_INVALID",
                    "Gemini 표현의 문단 구조를 확인하지 못해 로컬 표현으로 이어갑니다.",
                )
                for field in (
                    "request_count", "request_bytes", "response_bytes", "prompt_tokens",
                    "output_tokens", "total_tokens", "cached_tokens", "thoughts_tokens",
                    "metered_responses",
                ):
                    setattr(rejected, field, getattr(remote, field, 0))
                self._record_gemini_failure(config, rejected, "starplayer_expression")
                attempts.append(
                    {"provider": "gemini", "status": "rejected", "code": "EXPRESSION_INVALID"}
                )
            else:
                self._record_gemini_generation(config, remote, "starplayer_expression")
                provider_result = remote
                provider_audit = {**self._gemini_audit(remote, key_source), "fallback_chain": []}

        if provider_result is None:
            local_available = _llm_reachable() or _probe_llm_ports()
            if local_available:
                try:
                    local_text, local_model = narration_module.story_chat(
                        system_text,
                        user_text,
                        max_tokens=1600,
                    )
                except Exception as exc:
                    local_text, local_model = None, None
                    attempts.append(
                        {"provider": "local_llm", "status": "failed", "code": type(exc).__name__}
                    )
                if local_text:
                    try:
                        provider_expression.generated_blocks(local_text, provider="local_llm")
                    except ValueError:
                        attempts.append(
                            {"provider": "local_llm", "status": "rejected", "code": "EXPRESSION_INVALID"}
                        )
                    else:
                        provider_result = gemini_provider.ProviderResult(
                            text=str(local_text),
                            model=str(local_model),
                            request_hash="",
                            request_bytes=0,
                            response_bytes=0,
                            finish_reason=None,
                        )
                        attempts.append(
                            {"provider": "local_llm", "status": "success", "model": str(local_model)}
                        )
                        provider_audit = {
                            "provider": "local_llm",
                            "model": str(local_model),
                            "network_used": False,
                            "fallback_chain": copy.deepcopy(attempts[:-1]),
                        }
                elif not any(row.get("provider") == "local_llm" for row in attempts):
                    attempts.append(
                        {"provider": "local_llm", "status": "failed", "code": "NO_RESPONSE"}
                    )
            else:
                attempts.append(
                    {"provider": "local_llm", "status": "unavailable", "code": "NOT_REACHABLE"}
                )

        if provider_result is None:
            with self._state_lock:
                config, diagnostics, _store, ledger, world, event = self._prepare()
                self._require_live(config, world)
                self._require_chat_origin(config, world, event["snapshot"], origin, ledger=ledger)
                return self._deterministic_chat_locked(
                    config,
                    diagnostics,
                    ledger,
                    world,
                    event,
                    payload,
                    fallback={
                        "from": "gemini",
                        "via": "local_llm",
                        "reason": "provider_chain_exhausted",
                        "error_type": str((attempts[0] if attempts else {}).get("code") or "NO_RESPONSE"),
                        "attempts": attempts,
                    },
                )

        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            self._require_chat_origin(config, world, event["snapshot"], origin, ledger=ledger)
            return self._deterministic_chat_locked(
                config,
                diagnostics,
                ledger,
                world,
                event,
                payload,
                provider_result=provider_result,
                provider_audit=provider_audit,
                expected_plan_hash=expected_plan_hash,
            )

    # ------------------------------------------------------------------
    # Deterministic chat (no LLM) and proposal confirmation
    # ------------------------------------------------------------------

    def _narrative_inputs(self, config: dict, ledger, event: dict) -> dict:
        snapshot = event["snapshot"]
        game_date = story_engine.date_key(snapshot)
        return {
            "snapshot": snapshot,
            "game_date": game_date,
            "spotlight": self._spotlight(config, ledger, event),
            "career": ledger.career_view(snapshot),
            "day_context": ledger.state.get("day_contexts", {}).get(game_date),
            "session": ledger.current_session(snapshot),
        }

    def _deterministic_chat_plan_locked(self, ledger, world: dict, event: dict, payload: dict, *, config: dict) -> dict:
        user_text = story_engine._clean_text(payload.get("user_text"), required=True)
        inputs = self._narrative_inputs(config, ledger, event)
        game_date = inputs["game_date"]
        session = inputs["session"]
        if isinstance(session, dict) and session.get("status") == "sealed":
            raise ValueError("이미 마감된 날짜에는 새 대화를 추가할 수 없습니다.")
        universe_id = str(world.get("world_id"))
        protagonist_id = (inputs["snapshot"].get("player") or {}).get("id")
        conversation_state = narrative_state.conversation_state(ledger.state, game_date)
        bound_interaction = star_interactions.check_chat_binding(
            ledger.state, payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date,
        )
        if bound_interaction:
            conversation_state["last_target"] = bound_interaction["participant_role"]
        sequence = sum(1 for row in ledger.state.get("conversation_turns", []) if row.get("game_date") == game_date) + 1
        # Prop commands keep their existing independent confirmation workflow.
        # Bound activity turns never require an LLM or mutate on preview.
        understanding = narrative_engine.understand(user_text, conversation_state)
        lifetime_games = memory_windows.normalize_lifetime_games(
            payload.get("issue_lifetime_games"),
            text=user_text,
        )
        prop_command = bool(understanding.get("prop_acts"))
        if bound_interaction and not any(word in user_text for word in ("소재", "별명", "반복", "농담으로", "이걸 기억")):
            prop_command = False
        if (bound_interaction or payload.get("category") == "starplayer") and prop_command and interaction_intent.prop_needs_review(user_text):
            prop_command = False
        response = None if prop_command else star_interactions.chat_plan(
            ledger.state, payload, snapshot=inputs["snapshot"], game_date=game_date,
            universe_id=universe_id, sequence=sequence,
            spotlight=inputs["spotlight"],
        )
        if response is None:
            response = narrative_engine.generate_turn(
                user_text=user_text,
                snapshot=inputs["snapshot"],
                game_date=game_date,
                spotlight=inputs["spotlight"],
                universe_id=universe_id,
                ledger_state=ledger.state,
                session=session,
                career=inputs["career"],
                day_context=inputs["day_context"],
                conversation_state=conversation_state,
                tone=payload.get("tone") if payload.get("tone") in ("calm", "honest", "fiery", "witty", "cold", "warm") else None,
                sequence=sequence,
            )
        return {
            "user_text": user_text,
            "inputs": inputs,
            "game_date": game_date,
            "session": session,
            "universe_id": universe_id,
            "protagonist_id": protagonist_id,
            "conversation_state": conversation_state,
            "understanding": understanding,
            "lifetime_games": lifetime_games,
            "prop_command": prop_command,
            "prop_acts": list((response.get("understanding") or {}).get("prop_acts") or []),
            "response": response,
        }

    def _deterministic_chat_locked(
        self,
        config: dict,
        diagnostics: dict,
        ledger,
        world: dict,
        event: dict,
        payload: dict,
        *,
        fallback: dict | None = None,
        provider_result=None,
        provider_audit: dict | None = None,
        expected_plan_hash: str | None = None,
    ) -> dict:
        plan = self._deterministic_chat_plan_locked(
            ledger, world, event, payload, config=config
        )
        user_text = plan["user_text"]
        inputs = plan["inputs"]
        game_date = plan["game_date"]
        universe_id = plan["universe_id"]
        protagonist_id = plan["protagonist_id"]
        conversation_state = plan["conversation_state"]
        lifetime_games = plan["lifetime_games"]
        response = plan["response"]
        if provider_result is not None:
            if plan["prop_acts"]:
                raise RuntimeError("모델 응답을 기다리는 동안 대화가 상태 명령으로 바뀌어 저장하지 않았습니다.")
            current_hash = provider_expression.response_plan_hash(response)
            if expected_plan_hash != current_hash:
                raise RuntimeError("모델 응답을 기다리는 동안 대화 순서나 사건 제안이 바뀌어 저장하지 않았습니다. 다시 시도해 주세요.")
            response = provider_expression.apply_to_response(
                response,
                provider_result.text,
                model=provider_result.model,
                provider_audit=provider_audit or {},
                provider=str((provider_audit or {}).get("provider") or "gemini"),
            )
        if fallback:
            response.setdefault("provenance", {})["renderer_fallback"] = copy.deepcopy(fallback)
        committed_item = None
        prop_acts = list((response.get("understanding") or {}).get("prop_acts") or [])
        if prop_acts:
            result = narrative_props.handle_prop_act(
                ledger.state,
                act=prop_acts[0],
                text=user_text,
                understanding=narrative_engine.understand(user_text, conversation_state),
                game_date=game_date,
                universe_id=universe_id,
                protagonist_id=protagonist_id,
                last_prop_id=conversation_state.get("last_prop_id"),
                confirm=bool(payload.get("confirm")),
                lifetime_games=lifetime_games,
            )
            prop = result.get("prop")
            if result.get("applied") and result.get("change", {}).get("trigger") and prop:
                # A reservation cannot consume a game already visible when
                # the user made it, even if check_save has not committed yet.
                prop["recurrence_rules"]["trigger_events"][-1]["after_snapshot_hash"] = inputs["snapshot"].get("content_hash")
            response["prop_change"] = {
                "act": result.get("act"),
                "applied": bool(result.get("applied")),
                "message": result.get("message"),
                "requires_confirmation": bool(result.get("requires_confirmation")),
                "requires_clarification": bool(result.get("requires_clarification")),
                "change": result.get("change") or {},
                "prop": narrative_props.summary(prop) if prop else None,
            }
            if result.get("message"):
                response["reply"]["blocks"].insert(0, narrative_contracts.block("fact_callout", result["message"], label="소재 상태", fact_ids=[f"prop:{prop['prop_id']}"] if prop else ["prop"]))
                response["reply"]["text"] = narrative_contracts.project_blocks(response["reply"]["blocks"])
            if result.get("requires_confirmation") and result.get("proposal"):
                proposal = dict(result["proposal"], requires_confirmation=True, consequential=True, type="SP.USER.PROP", act=result.get("act"), target=None, target_label=None, thread_type="private_life", source="prop")
                response["proposed_events"].append(proposal)
                response["creates_event"] = True
                response["mode"] = "사건 제안"
            if result.get("applied") and result.get("scene_event_type") and prop:
                committed_item = self._commit_realized_event(
                    ledger, world, inputs,
                    proposal={"type": result["scene_event_type"], "act": result.get("act"), "target": (prop.get("participants") or [None])[0], "visibility": prop.get("visibility"), "thread_type": "private_life", "label": f"{prop.get('name')} · {narrative_engine.nc_state_label(prop.get('state'))}", "issue_lifetime_games": lifetime_games},
                    user_text=user_text, statement=None, prop=prop, turn_id=None, proposal_id=None,
                )
        if lifetime_games is not None:
            for proposal in response.get("proposed_events") or []:
                proposal["issue_lifetime_games"] = lifetime_games
        turn = narrative_state.append_conversation_turn(
            ledger.state, game_date=game_date, user_text=user_text, response=response,
            universe_id=universe_id, protagonist_id=protagonist_id,
        )
        if committed_item is not None:
            narrative_state.mark_proposal_committed(ledger.state, turn["turn_id"], "prop-beat", committed_item["id"])
        narrative_state.remember_signature(ledger.state, (response.get("provenance") or {}).get("signature"))
        ledger.save()
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        self._write_dashboard(config, world, ledger, event, dashboard)
        public_turn = ledger._public_turn(turn)
        renderer = str(response.get("renderer") or "deterministic")
        result = {
            "turn": public_turn,
            "response": response,
            "item": committed_item,
            "renderer": renderer,
            "provider_audit": copy.deepcopy(provider_audit) if provider_audit else None,
            "dashboard": dashboard,
        }
        if fallback:
            result["fallback"] = copy.deepcopy(fallback)
        return result

    def _confirm_chat_proposal_locked(self, config: dict, diagnostics: dict, ledger, world: dict, event: dict, payload: dict) -> dict:
        turn_id = str(payload.get("confirm_turn_id") or "")
        proposal_id = str(payload.get("confirm_proposal_id") or "")
        turn = narrative_state.find_turn(ledger.state, turn_id)
        if turn is None:
            raise ValueError("확정할 대화 차례를 찾지 못했습니다.")
        inputs = self._narrative_inputs(config, ledger, event)
        if turn.get("game_date") != inputs["game_date"]:
            raise ValueError("이미 마감된 날짜의 제안은 확정할 수 없습니다.")
        if narrative_state.proposal_is_committed(turn, proposal_id):
            raise ValueError("이 제안은 이미 오늘의 기록으로 확정됐습니다.")
        proposal = next((row for row in turn.get("proposed_events") or [] if row.get("proposal_id") == proposal_id), None)
        if proposal is None:
            raise ValueError("해당 제안을 찾지 못했습니다.")
        universe_id = str(world.get("world_id"))
        protagonist_id = (inputs["snapshot"].get("player") or {}).get("id")
        prop = None
        if proposal.get("source") == "prop" and proposal.get("kind") in ("prop_visibility", "prop_escalation"):
            prop = (ledger.state.get("narrative_props") or {}).get(str(proposal.get("prop_id")))
            if prop is None:
                raise ValueError("확대할 소재를 찾지 못했습니다.")
            result = narrative_props.escalate(ledger.state, prop, str(proposal.get("visibility") or "local"), game_date=inputs["game_date"], note=turn.get("user_text") or "")
            if not result.get("applied"):
                raise ValueError(str(result.get("message")))
            proposal = dict(proposal, type=result.get("scene_event_type") or "SP.USER.PROP.ESCALATION", visibility=prop.get("visibility"), label=f"{prop.get('name')} · 확대")
        item = self._commit_realized_event(
            ledger, world, inputs, proposal=proposal, user_text=turn.get("user_text"),
            statement=payload.get("statement"), prop=prop, turn_id=turn_id, proposal_id=proposal_id,
            tone=payload.get("tone") if payload.get("tone") in ("calm", "honest", "fiery", "witty", "cold", "warm") else None,
        )
        ledger.save()
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        self._write_dashboard(config, world, ledger, event, dashboard)
        return {
            "item": item,
            "turn": ledger._public_turn(narrative_state.find_turn(ledger.state, turn_id)),
            "renderer": str((item.get("scene") or {}).get("renderer") or "deterministic"),
            "dashboard": dashboard,
        }

    def _commit_realized_event(self, ledger, world: dict, inputs: dict, *, proposal: dict, user_text: str | None, statement: str | None, prop: dict | None, turn_id: str | None, proposal_id: str | None, tone: str | None = None, trigger_event: dict | None = None, source: str = "chat") -> dict:
        snapshot = inputs["snapshot"]
        game_date = inputs["game_date"]
        universe_id = str(world.get("world_id"))
        protagonist_id = (snapshot.get("player") or {}).get("id")
        session = ledger.current_session(snapshot) or {}
        interaction = None
        if proposal.get("source") == "star_interaction":
            if trigger_event:
                proposal = star_interactions.prepare_expression(ledger.state, proposal)
            interaction = star_interactions.validate_plan(ledger.state, proposal, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
            statement = story_engine._clean_text(statement) if statement else None
            realized = star_interactions.realized_event(
                ledger.state, proposal, snapshot=snapshot, game_date=game_date,
                universe_id=universe_id, spotlight=inputs["spotlight"], statement=statement,
            )
            realized = provider_expression.apply_to_realized(realized, proposal)
            if realized.get("renderer") in ("gemini_expression", "local_llm_expression"):
                realized["audit"] = realism_gate.audit_public_prose(
                    " ".join(
                        str(block.get("text") or "")
                        for block in realized.get("blocks") or []
                        if block.get("type") != "fact_callout"
                    )
                ).as_dict()
        else:
            realized = narrative_engine.realize_event(
                proposal=proposal, snapshot=snapshot, game_date=game_date, spotlight=inputs["spotlight"],
                universe_id=universe_id, user_text=user_text, ledger_state=ledger.state, career=inputs["career"],
                sequence=int(session.get("sequence") or 0) + 1, prop=prop, statement=statement, tone=tone,
            )
        if trigger_event:
            trigger_label = (trigger_event.get("payload") or {}).get("label") or event_ontology.label_of(trigger_event["event_type"])
            realized["blocks"].append(narrative_contracts.block("fact_callout", str(trigger_label), label="다시 떠오른 계기", fact_ids=list(trigger_event.get("source_facts") or [trigger_event["event_id"]]), evidence_class=trigger_event["evidence_class"]))
            realized["text"] = narrative_contracts.project_blocks(realized["blocks"])
        selection = event_ontology.legacy_selection_for(realized["event_type"])
        selection.update(
            {
                "target": proposal.get("target") if proposal.get("target") in story_engine._TARGETS else "self",
                "tone": tone or "honest",
                "visibility": story_engine.legacy_visibility(realized["visibility"]),
                "user_text": statement or "",
            }
        )
        lifetime_games = memory_windows.normalize_lifetime_games(
            proposal.get("issue_lifetime_games")
        )
        if lifetime_games is not None:
            selection["issue_lifetime_games"] = lifetime_games
        item = ledger.append_story_event(selection, snapshot, source="scheduled" if trigger_event else source, spotlight=inputs["spotlight"], realized=realized, save=False)
        if trigger_event:
            item["trigger_event_id"] = trigger_event["event_id"]
            item["narrative_provenance"]["trigger_event_id"] = trigger_event["event_id"]
        state = ledger.state
        thread, _created = narrative_state.ensure_thread(
            state, thread_type=realized["thread_type"],
            label=f"{interaction['participant_label']} · {interaction['topic']}" if interaction else realized["label"], universe_id=universe_id,
            protagonist_id=protagonist_id, game_date=game_date, participants=[proposal.get("target")] if proposal.get("target") else [], opened_by_event=item["id"],
            scope_key=interaction["interaction_id"] if interaction else None,
        )
        if interaction:
            # Ordinary conversation is not an automatic escalation to pressure.
            beat = proposal["beat"]
            next_state = "noticed" if thread["state"] == "seeded" else None
            if beat in ("pause", "close") and "dormant" in narrative_contracts.THREAD_TRANSITIONS[thread["state"]]:
                next_state = "dormant"
            elif beat == "resume" and thread["state"] in ("dormant", "resolved", "remembered"):
                next_state = "resurfaced"
            elif beat == "disagree" and thread["state"] in ("noticed", "resurfaced"):
                next_state = "developing"
            if next_state:
                narrative_state.advance_thread(state, thread["thread_id"], next_state, game_date=game_date, event_id=item["id"], authorized=True, note=f"interaction {beat}")
            thread["last_event_on"] = game_date
            interaction = star_interactions.commit(state, proposal, snapshot=snapshot, game_date=game_date, universe_id=universe_id, event_id=item["id"], trigger_event=trigger_event)
            interaction["thread_id"] = thread["thread_id"]
            if interaction["participant_key"]:
                world_identity.ensure_interaction_entity(
                    state,
                    entity_id=interaction["participant_key"],
                    label=interaction["participant_label"],
                    role=interaction["participant_role"],
                    universe_id=universe_id,
                    protagonist_id=protagonist_id,
                    game_date=game_date,
                    event_id=item["id"],
                    source=interaction.get("participant_source") or "user_label",
                )
            deltas = star_interactions.relationship_deltas(beat)
        else:
            narrative_state.touch_thread_for_event(state, thread, game_date=game_date, event_id=item["id"])
            deltas = narrative_state.relationship_deltas_for(proposal.get("act"), proposal.get("target"))
        if deltas and proposal.get("target"):
            narrative_state.apply_relationship(state, target=str(proposal.get("target")), deltas=deltas, game_date=game_date, event_id=item["id"], explanation=f"{realized['label']} ({proposal.get('act')})", memory_id=item["id"])
        world_event = narrative_state.record_world_event(
            state,
            narrative_contracts.new_event(
                event_type=realized["event_type"], game_date=game_date, universe_id=universe_id, protagonist_id=protagonist_id,
                actor="protagonist", target=proposal.get("target"), visibility=realized["visibility"], evidence_class="fictional_intervention",
                protagonist_relation="subject", source_facts=[f"snapshot:{snapshot.get('content_hash')}"], thread_links=[{"thread_id": thread["thread_id"], "action": "advanced"}],
                payload={"story_event_id": item["id"], "turn_id": turn_id, "proposal_id": proposal_id, "prop_id": (prop or {}).get("prop_id"), "trigger_event_id": (trigger_event or {}).get("event_id"),
                         **({"interaction_id": interaction["interaction_id"], "activity_fact_id": interaction.get("action_fact_id"), "beat": proposal["beat"]} if interaction else {})}, event_id=item["id"],
            ),
        )
        narrative_state.remember_signature(state, realized.get("signature"))
        if not trigger_event:
            callbacks = self._apply_scheduled_callbacks(ledger, world, inputs, world_event, skip_prop_id=(prop or {}).get("prop_id"))
            for callback in callbacks:
                item.setdefault("effects", []).append({"kind": "prop_callback", "event_id": callback["id"], "label": callback["scene"]["title"]})
        if turn_id and proposal_id:
            narrative_state.mark_proposal_committed(state, turn_id, proposal_id, item["id"])
        # append_story_event stores a defensive copy. Persist the causal links
        # and effects added after it without altering any earlier scene.
        turns = state["story_sessions"][game_date]["turns"]
        for index, stored in enumerate(turns):
            if stored.get("id") == item["id"]:
                turns[index] = copy.deepcopy(item)
                break
        return item

    def _apply_scheduled_callbacks(self, ledger, world: dict, inputs: dict, event: dict, *, skip_prop_id: str | None = None) -> list[dict]:
        state = ledger.state
        source_hash = (event.get("payload") or {}).get("source_hash")
        due = narrative_props.due_callbacks(state, event["event_type"], game_date=inputs["game_date"], source_hash=source_hash)
        callbacks = []
        for prop in due:
            if prop.get("prop_id") == skip_prop_id:
                continue
            if str(prop.get("universe_id")) != str(world["world_id"]) or str(prop.get("protagonist_id")) != str((inputs["snapshot"].get("player") or {}).get("id")):
                continue
            fired = narrative_props.fire_callback(state, prop, event["event_type"], game_date=inputs["game_date"], event_id=event["event_id"], source_hash=source_hash)
            if not fired.get("applied"):
                continue
            callbacks.append(self._commit_realized_event(
                ledger, world, inputs,
                proposal={"type": fired.get("scene_event_type") or "SP.USER.PROP.CALLBACK", "act": "prop_schedule_callback", "target": (prop.get("participants") or [None])[0], "visibility": prop.get("visibility"), "thread_type": "private_life"},
                user_text=None, statement=None, prop=prop, turn_id=None, proposal_id=None, trigger_event=event,
            ))
        for proposal in star_interactions.due_callbacks(
            state, event, universe_id=str(world["world_id"]),
            protagonist_id=inputs["snapshot"]["player"]["id"], game_date=inputs["game_date"],
        ):
            callbacks.append(self._commit_realized_event(
                ledger, world, inputs, proposal=proposal, user_text=None, statement=None,
                prop=None, turn_id=None, proposal_id=None, trigger_event=event,
            ))
        return callbacks

    def _write_dashboard(self, config: dict, world: dict, ledger, event: dict, dashboard: dict) -> None:
        output_dir = self._world_output_dir(config, world["world_id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "dashboard.json", dashboard)
        self._persist_current_capsule(config, ledger, world, event["snapshot"])
        self._latest_dashboard = dashboard

    @staticmethod
    def _story_context(config: dict, world: dict, snapshot: dict, game_date: str) -> dict:
        save_path = config.get("save_path") or ""
        return {
            "save_path": os.path.normcase(os.path.abspath(save_path)) if save_path else "",
            "world_id": world.get("world_id"),
            "generation": world.get("generation"),
            "player_id": (snapshot.get("player") or {}).get("id"),
            "game_date": game_date,
            # A game can finish or a corrected save can arrive while a slow
            # provider is answering without changing the calendar date.
            # Never attach prose built from the older fact snapshot.
            "snapshot_hash": snapshot.get("content_hash"),
        }

    def _require_chat_origin(
        self,
        config: dict,
        world: dict,
        snapshot: dict,
        origin: dict,
        *,
        ledger=None,
    ) -> None:
        current = self._story_context(
            config, world, snapshot, story_engine.date_key(snapshot)
        )
        if "personal_context_hash" in origin:
            if ledger is None:
                raise RuntimeError("선수 설정의 현재 상태를 확인할 수 없어 모델 응답을 저장하지 않았습니다.")
            raw_scope = str(origin.get("personal_context_scope") or "local")
            context_scope = raw_scope if raw_scope in {"remote", "public_local"} else "local"
            current_context = personal_context.prompt_section(
                ledger.state,
                universe_id=str(world.get("world_id") or ""),
                protagonist_id=(snapshot.get("player") or {}).get("id"),
                game_date=story_engine.date_key(snapshot),
                remote=context_scope == "remote",
                audience="public" if context_scope == "public_local" else "private",
            )
            current["personal_context_scope"] = context_scope
            current["personal_context_hash"] = hashlib.sha256(
                current_context.encode("utf-8")
            ).hexdigest()
        if "memory_context_hash" in origin:
            if ledger is None:
                raise RuntimeError("세계선 기억의 현재 상태를 확인할 수 없어 모델 응답을 저장하지 않았습니다.")
            memory_scope = (
                origin.get("memory_context_scope") == "public"
                or origin.get("personal_context_scope") == "remote"
            )
            if "memory_context_scope" in origin:
                current["memory_context_scope"] = origin.get("memory_context_scope")
            current["memory_context_refs"] = copy.deepcopy(origin.get("memory_context_refs") or [])
            current["memory_context_hash"] = memory_windows.reference_fingerprint(
                ledger.state,
                as_of_date=story_engine.date_key(snapshot),
                universe_id=str(world.get("world_id") or ""),
                protagonist_id=(snapshot.get("player") or {}).get("id"),
                references=origin.get("memory_context_refs") or [],
                remote=memory_scope,
            )
        if current != origin:
            # Review finding 2026-09-02 P2 #3: never bind a response to a
            # different save, player, world generation, or game date.
            changed = ", ".join(sorted(key for key in origin if origin[key] != current.get(key)))
            raise RuntimeError(
                "모델 응답을 기다리는 동안 세이브 문맥이 바뀌어 응답을 저장하지 않았습니다"
                f" (변경: {changed}). 같은 세이브·날짜에서 다시 시도하세요."
            )

    def _append_chat_turn_locked(self, selection: dict, text: str, model, origin: dict, *, provider_audit: dict | None = None) -> dict:
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        self._require_chat_origin(
            config, world, event["snapshot"], origin, ledger=ledger
        )
        spotlight = self._spotlight(config, ledger, event)
        text = prose_format.normalize_generated_markdown(text)
        item = ledger.append_story_event(
            selection,
            event["snapshot"],
            source="llm",
            llm_text=text,
            model=str(model),
            spotlight=spotlight,
        )
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        output_dir = self._world_output_dir(config, world["world_id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "dashboard.json", dashboard)
        self._persist_current_capsule(config, ledger, world, event["snapshot"])
        self._latest_dashboard = dashboard
        renderer = "gemini" if str(model).startswith("gemini:") else "llm"
        return {
            "item": item,
            "renderer": renderer,
            "provider_audit": copy.deepcopy(provider_audit) if provider_audit else None,
            "dashboard": dashboard,
        }

    def upsert_day_context(self, payload: dict) -> dict:
        with self._state_lock:
            return self._upsert_day_context_locked(payload)

    def upsert_personal_context(self, payload: dict) -> dict:
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            snapshot = event["snapshot"]
            game_date = story_engine.date_key(snapshot)
            player = snapshot.get("player") or {}
            row = personal_context.upsert_user_context(
                ledger.state,
                payload,
                universe_id=str(world["world_id"]),
                protagonist_id=player.get("id"),
                player_label=str(player.get("name") or "선수"),
                game_date=game_date,
            )
            ledger._remember_day_snapshot(snapshot)
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"item": row, "dashboard": dashboard}

    def retire_personal_context(self, payload: dict) -> dict:
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            snapshot = event["snapshot"]
            row = personal_context.retire_user_context(
                ledger.state,
                payload,
                universe_id=str(world["world_id"]),
                protagonist_id=(snapshot.get("player") or {}).get("id"),
                game_date=story_engine.date_key(snapshot),
            )
            ledger._remember_day_snapshot(snapshot)
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"item": row, "dashboard": dashboard}

    def upsert_counterpart(self, payload: dict) -> dict:
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            snapshot = event["snapshot"]
            kwargs = {
                "universe_id": str(world["world_id"]),
                "protagonist_id": (snapshot.get("player") or {}).get("id"),
                "game_date": story_engine.date_key(snapshot),
            }
            row = (
                world_identity.update_counterpart(ledger.state, payload, **kwargs)
                if payload.get("entity_id")
                else world_identity.create_counterpart(ledger.state, payload, **kwargs)
            )
            ledger._remember_day_snapshot(snapshot)
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"item": row, "dashboard": dashboard}

    def retire_counterpart(self, payload: dict) -> dict:
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            snapshot = event["snapshot"]
            row = world_identity.retire_counterpart(
                ledger.state,
                payload,
                universe_id=str(world["world_id"]),
                protagonist_id=(snapshot.get("player") or {}).get("id"),
                game_date=story_engine.date_key(snapshot),
            )
            ledger._remember_day_snapshot(snapshot)
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {"item": row, "dashboard": dashboard}

    def _upsert_day_context_locked(self, payload: dict) -> dict:
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        item = ledger.upsert_day_context(payload, event["snapshot"])
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        output_dir = self._world_output_dir(config, world["world_id"])
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "dashboard.json", dashboard)
        self._persist_current_capsule(config, ledger, world, event["snapshot"])
        self._latest_dashboard = dashboard
        return {"item": item, "dashboard": dashboard}

    def upsert_career_season(self, payload: dict) -> dict:
        with self._state_lock:
            return self._upsert_career_season_locked(payload)

    def _upsert_career_season_locked(self, payload: dict) -> dict:
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        row = ledger.upsert_career_season(payload, event["snapshot"])
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        atomic_write_json(
            self._world_output_dir(config, world["world_id"]) / "dashboard.json",
            dashboard,
        )
        self._latest_dashboard = dashboard
        return {"item": row, "dashboard": dashboard}

    def upsert_career_honor(self, payload: dict) -> dict:
        with self._state_lock:
            return self._upsert_career_honor_locked(payload)

    def _upsert_career_honor_locked(self, payload: dict) -> dict:
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        row = ledger.upsert_career_honor(payload, event["snapshot"])
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        atomic_write_json(
            self._world_output_dir(config, world["world_id"]) / "dashboard.json",
            dashboard,
        )
        self._latest_dashboard = dashboard
        return {"item": row, "dashboard": dashboard}

    def remove_career_item(self, payload: dict) -> dict:
        with self._state_lock:
            return self._remove_career_item_locked(payload)

    def _remove_career_item_locked(self, payload: dict) -> dict:
        entity = str(payload.get("entity") or "")
        item_id = str(payload.get("id") or "")
        config, diagnostics, _store, ledger, world, event = self._prepare()
        self._require_live(config, world)
        ledger.remove_career_item(entity, item_id, event["snapshot"])
        dashboard = self._dashboard(config, diagnostics, ledger, world, event)
        atomic_write_json(
            self._world_output_dir(config, world["world_id"]) / "dashboard.json",
            dashboard,
        )
        self._latest_dashboard = dashboard
        return {"removed": True, "dashboard": dashboard}

    def read_daily_archive(self, archive_id: str) -> dict:
        with self._state_lock:
            return self._read_daily_archive_locked(archive_id)

    def _read_daily_archive_locked(self, archive_id: str) -> dict:
        config, _diagnostics, _store, ledger, world, _event = self._prepare()
        row = next(
            (
                item
                for item in ledger.state.get("daily_archive", [])
                if item.get("id") == archive_id
            ),
            None,
        )
        if not row:
            raise FileNotFoundError("반응 아카이브를 찾지 못했습니다.")
        output_dir = self._world_output_dir(config, world["world_id"]).resolve()
        target = (output_dir / str(row.get("relative_path") or "")).resolve()
        try:
            target.relative_to(output_dir / "archive")
        except ValueError as exc:
            raise ValueError("반응 아카이브 경로가 올바르지 않습니다.") from exc
        value = self._read_json(target)
        if not value:
            raise FileNotFoundError("반응 아카이브 파일을 읽지 못했습니다.")
        return value

    def read_history_capsule(self, game_date: str) -> dict:
        with self._state_lock:
            config, _diagnostics, _store, ledger, world, event = self._prepare()
            capsule = ledger.history_capsule(game_date, event["snapshot"])
            return self._enrich_capsule(config, world, capsule)

    # ------------------------------------------------------------------
    # Preserved-world (museum mode) reads and relink (master plan 3.5, 17.5)
    # ------------------------------------------------------------------

    def _universe_ledger(self, config: dict, universe_id: str) -> tuple[Ledger, dict]:
        registry = self._registry(config)
        row = registry.get(universe_id)
        if row is None:
            raise UniverseNotFoundError("알 수 없는 세계선입니다.")
        path = Path(config["data_dir"]) / "worlds" / str(universe_id) / "ledger.json"
        if not path.is_file():
            raise UniverseNotFoundError("보존된 세계선의 원장 파일을 찾지 못했습니다.")
        return Ledger(str(path), str(universe_id), read_only=True), row

    def list_universes(self) -> dict:
        config = config_module.load()
        try:
            candidates = self.rescan_saves()
        except Exception as exc:  # pragma: no cover - discovery must not block the library
            log.warning("save discovery failed during universe listing: %s", exc)
            candidates = []
        return self._registry(config).list_universes(candidates)

    def read_universe(self, universe_id: str) -> dict:
        config = config_module.load()
        registry = self._registry(config)
        row = registry.get(universe_id)
        if row is None:
            raise UniverseNotFoundError("알 수 없는 세계선입니다.")
        card = registry.card(row)
        card["status_events"] = registry.status_events(universe_id)
        card["bindings"] = [
            {key: value for key, value in binding.items() if key != "save_path"}
            for binding in registry.bindings(universe_id)
        ]
        card["read_only_notice"] = (
            "원본 세이브와 현재 연결되어 있지 않습니다. 기록·날짜별 역사·기사·커뮤니티·서사를 읽고 검색하고 내보낼 수 있지만 새 사건은 만들 수 없습니다."
            if card["capabilities"]["read_only"]
            else None
        )
        return card

    def read_universe_history(self, universe_id: str) -> dict:
        config = config_module.load()
        ledger, row = self._universe_ledger(config, universe_id)
        last = ledger.state.get("last_snapshot")
        rows = ledger.history_index(last) if isinstance(last, dict) else []
        self._registry(config).touch_read(universe_id)
        return {
            "universe_id": str(universe_id),
            "state": row.get("state"),
            "read_only": row.get("state") not in ("live", "live_relinked"),
            "history": rows,
            "career": ledger.career_view(last) if isinstance(last, dict) else None,
            "story_history": ledger.story_view(last)["history"] if isinstance(last, dict) else [],
            "daily_archive": list(reversed(ledger.recent_daily_archive(100000))),
            "chronicle": chronicle.view(ledger.state, last, {"world_id": str(universe_id)}) if isinstance(last, dict) else None,
        }

    def read_universe_capsule(self, universe_id: str, game_date: str) -> dict:
        config = config_module.load()
        ledger, row = self._universe_ledger(config, universe_id)
        last = ledger.state.get("last_snapshot")
        if not isinstance(last, dict):
            raise FileNotFoundError("보존된 세계선에 검증된 스냅샷이 없습니다.")
        capsule = ledger.history_capsule(game_date, last)
        capsule["read_only"] = row.get("state") not in ("live", "live_relinked")
        capsule["editable"] = False if capsule["read_only"] else capsule.get("editable")
        self._registry(config).touch_read(universe_id)
        return self._enrich_capsule(config, {"world_id": str(universe_id)}, capsule)

    def read_universe_reaction(self, universe_id: str, archive_id: str) -> dict:
        config = config_module.load()
        ledger, _row = self._universe_ledger(config, universe_id)
        row = next(
            (item for item in ledger.state.get("daily_archive", []) if item.get("id") == archive_id),
            None,
        )
        if not row:
            raise FileNotFoundError("반응 아카이브를 찾지 못했습니다.")
        payload = self._archive_payload(config, {"world_id": str(universe_id)}, row)
        if not payload:
            raise FileNotFoundError("반응 아카이브 파일을 읽지 못했습니다.")
        return payload

    def _candidate_snapshot(self, config: dict) -> tuple[dict | None, str | None]:
        save_path = config.get("save_path")
        if not save_path or not os.path.isfile(save_path):
            return None, save_path
        try:
            return self._read_snapshot(save_path), save_path
        except Exception as exc:
            log.info("relink candidate could not be read: %s", exc)
            return None, save_path

    def relink_preview(self, universe_id: str) -> dict:
        config = config_module.load()
        snapshot, save_path = self._candidate_snapshot(config)
        preview = self._registry(config).relink_preview(universe_id, snapshot, save_path)
        if snapshot and preview.get("can_relink"):
            active = self._world_store(config).peek_active_world_id(snapshot, save_path)
            if active and active != str(universe_id):
                preview["can_relink"] = False
                preview["reason"] = "이 세이브는 이미 다른 세계선(분기)에 연결돼 있습니다."
                preview["conflicting_universe_id"] = active
        return preview

    def relink_confirm(self, universe_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self._state_lock:
            config = config_module.load()
            preview = self.relink_preview(universe_id)
            if not preview.get("can_relink"):
                raise WorldReadOnlyError(f"재연결할 수 없습니다: {preview.get('reason')}")
            snapshot, save_path = self._candidate_snapshot(config)
            ledger, _row = self._universe_ledger(config, universe_id)
            token = self._registry(config).relink_confirm(
                universe_id, snapshot, save_path, note=str(payload.get("note") or ""), ledger_state=ledger.state
            )
            return {"binding": token, "preview": preview}

    def export_universe(self, universe_id: str) -> dict:
        config = config_module.load()
        bundle = self._registry(config).export_bundle(universe_id)
        target_dir = self._world_output_dir(config, str(universe_id)) / "export"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = target_dir / f"universe-{universe_id}-{stamp}.json"
        atomic_write_json(target, bundle)
        return {"path": str(target), "bytes": target.stat().st_size, "universe_id": str(universe_id)}

    # ------------------------------------------------------------------
    # Local attachment understanding (master plan 8.12, 16.7, 17.4)
    # ------------------------------------------------------------------

    def attachment_status(self) -> dict:
        status = attachments_module.ocr_status()
        local_vision_ready = bool(narration_module.pick_endpoint(need_vision=True)[0])
        config = config_module.public_config(config_module.load())
        connection = (config.get("gemini_usage") or {}).get("connection") or {}
        gemini_vision_ready = bool(
            config.get("gemini_consent")
            and config.get("gemini_key_present")
            and connection.get("status") == "verified"
            and connection.get("model") == config.get("gemini_model")
        )
        selected = str(config.get("ai_provider") or "local_auto")
        if selected == "gemini":
            vision_provider = "gemini" if gemini_vision_ready else None
        else:
            vision_provider = "local_llm" if local_vision_ready else None
        return {
            "local_ocr": status,
            "parser_profiles": [
                {"profile_id": row.get("profile_id"), "screen_type": row.get("screen_type"), "label": row.get("label")}
                for row in (attachments_module.load_profiles().get("profiles") or [])
            ],
            "decisions": [{"id": key, "label": value} for key, value in attachments_module.DECISION_LABELS.items()],
            "retention": [{"id": key, "label": value} for key, value in attachments_module.RETENTION_LABELS.items()],
            "local_llm_vision_reachable": local_vision_ready,
            "gemini_vision_ready": gemini_vision_ready,
            "vision_provider": vision_provider,
            # Backward-compatible aggregate used by existing UI clients.
            "llm_vision_reachable": bool(vision_provider),
        }

    @staticmethod
    def _protagonist_names(snapshot: dict) -> list[str]:
        player = snapshot.get("player") or {}
        names = [str(player.get("name") or "")]
        names.extend(str(part) for part in (player.get("name_parts") or []) if part)
        return [name for name in names if name]

    @staticmethod
    def _day_delta(ledger, game_date: str) -> dict:
        day = (ledger.state.get("day_snapshots") or {}).get(game_date) or {}
        return dict(((day.get("event") or {}).get("delta")) or {})

    @staticmethod
    def _cross_check(record: dict, delta: dict) -> dict:
        """Compare parsed protagonist numbers with the verified save delta of that date."""
        checks = {"bat_HR": ("game.protagonist_home_runs", "batting.home_runs"), "bat_H": ("batting.hits",), "bat_AB": ("batting.at_bats",)}
        for observation in record.get("observations") or []:
            for stat, fields in checks.items():
                if observation.get("field") in fields and stat in delta and isinstance(observation.get("value"), int):
                    if int(delta[stat]) == int(observation["value"]):
                        observation["cross_check"] = f"세이브 변화량과 일치 ({stat} {delta[stat]})"
                        observation["confidence"] = round(min(1.0, observation["confidence"] + 0.1), 3)
                    else:
                        observation["cross_check"] = f"세이브 변화량과 다름 ({stat} {delta[stat]} vs 화면 {observation['value']})"
                        observation["validation"] = "warn" if observation.get("validation") == "ok" else observation.get("validation")
                        observation["note"] = (observation.get("note") or "") + " 세이브가 기록한 값과 다릅니다. 화면이 일부만 보이거나 다른 경기일 수 있습니다."
                        observation["suggested_decision"] = "ignore"
        return record

    def analyze_attachments(self, payload: dict) -> dict:
        names = payload.get("names") if isinstance(payload.get("names"), list) else [payload.get("name")]
        names = [str(name) for name in names if name]
        if not names:
            raise ValueError("분석할 이미지 이름이 없습니다.")
        with self._state_lock:
            config, _diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            snapshot = event["snapshot"]
            game_date = story_engine.date_key(snapshot)
            player = snapshot.get("player") or {}
            records = []
            for name in names[:8]:
                path = self.read_capture_path(name)
                record = attachments_module.analyze_local(
                    path,
                    protagonist_names=self._protagonist_names(snapshot),
                    protagonist_team=str(player.get("team") or ""),
                    protagonist_id=player.get("id"),
                    universe_id=str(world.get("world_id")),
                    current_game_date=game_date,
                    ocr=payload.get("_ocr_fixture") if isinstance(payload.get("_ocr_fixture"), dict) else None,
                )
                delta = self._day_delta(ledger, str((record.get("binding") or {}).get("game_date") or game_date))
                self._cross_check(record, delta)
                records.append(record)
            records = attachments_module.detect_duplicates_and_conflicts(records)
            store = ledger.state.setdefault("attachment_records", {})
            reviewed = []
            changed = False
            for record in records:
                existing = store.get(record["attachment_id"]) or {}
                if existing.get("status") == "committed":
                    # Content-addressed review evidence is immutable after
                    # confirmation, including its original date and decisions.
                    reviewed.append(copy.deepcopy(existing))
                else:
                    record["status"] = "analyzed"
                    store[record["attachment_id"]] = copy.deepcopy(record)
                    reviewed.append(record)
                    changed = True
            if changed:
                ledger.save()
            return {"attachments": reviewed, "status": self.attachment_status()}

    def propose_attachment(self, payload: dict) -> dict:
        attachment_id = str(payload.get("attachment_id") or "")
        with self._state_lock:
            _config, _diagnostics, _store, ledger, _world, _event = self._prepare()
            record = (ledger.state.get("attachment_records") or {}).get(attachment_id)
            if not record:
                raise FileNotFoundError("분석된 첨부를 찾지 못했습니다. 먼저 이미지 내용을 읽으세요.")
            return attachments_module.propose_application(record, payload.get("decisions") or {}, manual_description=payload.get("manual_description"))

    def commit_attachment(self, payload: dict) -> dict:
        attachment_id = str(payload.get("attachment_id") or "")
        retention = str(payload.get("retention") or "keep_original")
        if retention not in attachments_module.RETENTION:
            raise ValueError("보관 방식이 올바르지 않습니다.")
        reaction_scope = str(payload.get("reaction_scope") or "private")
        if reaction_scope not in _ATTACHMENT_REACTION_SCOPES:
            raise ValueError("반응 확산 범위가 올바르지 않습니다.")
        with self._state_lock:
            config, diagnostics, _store, ledger, world, event = self._prepare()
            self._require_live(config, world)
            state = ledger.state
            record = (state.get("attachment_records") or {}).get(attachment_id)
            if not record:
                raise FileNotFoundError("분석된 첨부를 찾지 못했습니다. 먼저 이미지 내용을 읽으세요.")
            if record.get("status") == "committed":
                raise ValueError("이미 반영된 첨부입니다.")
            snapshot = event["snapshot"]
            current_date = story_engine.date_key(snapshot)
            binding = dict(record.get("binding") or {})
            target_date = str(binding.get("game_date") or current_date)
            if str(binding.get("universe_id")) != str(world.get("world_id")) or str(binding.get("protagonist_id")) != str((snapshot.get("player") or {}).get("id")):
                raise ValueError("첨부가 분석된 세계선·선수와 현재 세이브가 다릅니다. 다시 읽어 주세요.")
            if target_date != current_date:
                raise ValueError(f"첨부의 날짜({target_date})가 열린 날짜({current_date})와 다릅니다. 마감된 날짜에는 반영할 수 없습니다.")
            if (binding.get("requires_confirmation") or binding.get("date_conflict")) and not payload.get("binding_confirmed"):
                raise ValueError("선수·날짜 연결이 확실하지 않습니다. 검토 화면에서 연결을 직접 확인해 주세요.")
            proposal = attachments_module.propose_application(record, payload.get("decisions") or {}, manual_description=payload.get("manual_description"))
            forced = set(str(row) for row in (payload.get("force_observation_ids") or []))
            if proposal["blocked"] and not all(row["observation_id"] in forced for row in proposal["blocked"]):
                raise ValueError("검증에 실패한 항목은 사실로 반영할 수 없습니다. 무시하거나 창작 소재로만 남기세요.")
            universe_id = str(world.get("world_id"))
            protagonist_id = (snapshot.get("player") or {}).get("id")
            reaction_scene = None
            if reaction_scope != "private":
                reaction_scene = _attachment_reaction_scene(
                    proposal,
                    str((snapshot.get("player") or {}).get("name") or "선수"),
                )
            links = []
            facts = []
            props = []
            for row in proposal["proposals"]:
                if row["target"] == "fact_registry":
                    fact = narrative_contracts.new_fact(
                        kind=str(row["field"]), label=str(row.get("label") or row["field"]), value=row.get("value"),
                        evidence_class="user_confirmed", game_date=current_date, scope="game", source=f"attachment:{attachment_id}",
                    )
                    narrative_state.register_fact(state, fact)
                    facts.append(fact)
                    links.append({"attachment_id": attachment_id, "observation_id": row["observation_id"], "fact_id": fact["fact_id"], "prop_id": None, "evidence_class": "user_confirmed", "game_date": current_date, "at": _now()})
                elif row["target"] == "narrative_prop":
                    name = str(row.get("value") if isinstance(row.get("value"), str) else row.get("label") or "첨부 소재")[:60]
                    prop_name = narrative_props.parse_prop_name(name) or name
                    prop = narrative_contracts.new_prop(
                        universe_id=universe_id, protagonist_id=protagonist_id, name=prop_name,
                        prop_type=narrative_props.guess_type(name, prop_name), game_date=current_date,
                        emotional_roles=narrative_props.parse_roles(name) or ["symbolic"], visibility="private",
                    )
                    prop["origin_attachment_id"] = attachment_id
                    state.setdefault("narrative_props", {})[prop["prop_id"]] = prop
                    narrative_contracts.transition_prop(prop, "establish", game_date=current_date, note=f"attachment {attachment_id}")
                    props.append(prop)
                    links.append({"attachment_id": attachment_id, "observation_id": row["observation_id"], "fact_id": None, "prop_id": prop["prop_id"], "evidence_class": "fictional_intervention", "game_date": current_date, "at": _now()})
            result_fact = next((row for row in proposal["proposals"] if row["field"] == "game.result" and row["target"] == "fact_registry" and isinstance(row.get("value"), dict)), None)
            if result_fact:
                outcome = str(result_fact["value"].get("outcome") or "")
                event_type = {"win": "SP.GAME.POST.WIN", "loss": "SP.GAME.POST.LOSS"}.get(outcome, "SP.GAME.POST.RECAP")
                narrative_state.record_world_event(
                    state,
                    narrative_contracts.new_event(
                        event_type=event_type, game_date=current_date, universe_id=universe_id, protagonist_id=protagonist_id,
                        actor="protagonist", visibility="national", evidence_class="user_confirmed", protagonist_relation="direct_actor",
                        source_facts=[row["fact_id"] for row in facts], payload={"attachment_id": attachment_id, "result": result_fact["value"]},
                    ),
                )
            reaction_item = None
            reaction_counts = {
                "articles": 0,
                "boards": 0,
                "comments": 0,
                "social_posts": 0,
                "social_replies": 0,
            }
            if reaction_scene:
                spotlight = self._spotlight(config, ledger, event)
                realized = _build_attachment_reaction(
                    config=config,
                    event=event,
                    spotlight=spotlight,
                    memory=state.get("community_memory") or {},
                    universe_id=universe_id,
                    attachment_id=attachment_id,
                    scope=reaction_scope,
                    scene_summary=reaction_scene[0],
                    scene_label=reaction_scene[1],
                    source_fact_ids=[row["fact_id"] for row in facts],
                )
                selection = {
                    "category": "media",
                    "situation": "postgame_interview",
                    "target": "reporter",
                    "tone": "honest",
                    "visibility": "social" if reaction_scope == "community" else "public",
                    "user_text": "",
                }
                reaction_item = ledger.append_story_event(
                    selection,
                    snapshot,
                    source="attachment",
                    spotlight=spotlight,
                    realized=realized,
                    save=False,
                )
                reaction_counts = provider_feed.bundle_counts(realized["reactions"])
                narrative_state.record_world_event(
                    state,
                    narrative_contracts.new_event(
                        event_type="SP.MEDIA.FEATURE",
                        game_date=current_date,
                        universe_id=universe_id,
                        protagonist_id=protagonist_id,
                        actor="protagonist",
                        visibility=realized["visibility"],
                        evidence_class="fictional_intervention",
                        protagonist_relation="subject",
                        source_facts=[row["fact_id"] for row in facts] or [f"attachment:{attachment_id}"],
                        reaction_plan={**reaction_counts, "scope": reaction_scope, "expression_heat": int(config.get("heat") or 7)},
                        payload={
                            "attachment_id": attachment_id,
                            "story_event_id": reaction_item["id"],
                            "scene_summary": reaction_scene[0],
                            "raw_image_published": False,
                        },
                        event_id=reaction_item["id"],
                    ),
                )
            observations = state.setdefault("attachment_observations", [])
            decided = {row["observation_id"]: row["decision"] for row in proposal["proposals"]}
            for observation in record.get("observations") or []:
                observation["decision"] = decided.get(observation["observation_id"], "ignore")
                observations.append({**copy.deepcopy(observation), "attachment_id": attachment_id, "game_date": current_date, "committed_at": _now()})
            state.setdefault("attachment_fact_links", []).extend(links)
            record["status"] = "committed"
            record["committed_at"] = _now()
            record["retention"] = retention
            record["decisions"] = decided
            record["manual_description"] = str(payload.get("manual_description") or "") or None
            record["reaction_scope"] = reaction_scope
            record["reaction_event_id"] = reaction_item.get("id") if reaction_item else None
            if retention in ("analysis_only", "delete_after"):
                try:
                    self.read_capture_path(record["file_name"]).unlink()
                    record["original_deleted_at"] = _now()
                except (FileNotFoundError, ValueError, OSError):
                    record["original_deleted_at"] = None
            if retention == "delete_after" and isinstance(record.get("ocr"), dict):
                record["ocr"] = {key: value for key, value in record["ocr"].items() if key != "lines"}
                record["text_signature"] = []
            ledger.save()
            dashboard = self._dashboard(config, diagnostics, ledger, world, event)
            self._write_dashboard(config, world, ledger, event, dashboard)
            return {
                "attachment": copy.deepcopy(record),
                "facts": facts,
                "props": [narrative_props.summary(row) for row in props],
                "links": links,
                "reaction": {
                    "scope": reaction_scope,
                    "event_id": reaction_item.get("id") if reaction_item else None,
                    "counts": reaction_counts,
                },
                "dashboard": dashboard,
                "capture": self.capture_status(),
            }

    def discard_attachment(self, payload: dict) -> dict:
        attachment_id = str(payload.get("attachment_id") or "")
        with self._state_lock:
            config, _diagnostics, _store, ledger, world, _event = self._prepare()
            self._require_live(config, world)
            store = ledger.state.setdefault("attachment_records", {})
            record = store.get(attachment_id)
            if not record:
                raise FileNotFoundError("분석된 첨부를 찾지 못했습니다.")
            if record.get("status") == "committed":
                raise ValueError("이미 반영된 첨부는 취소할 수 없습니다. 기록은 보존됩니다.")
            record["status"] = "discarded"
            record["discarded_at"] = _now()
            record["observations"] = []
            record["ocr"] = None
            record["text_signature"] = []
            ledger.save()
            return {"attachment_id": attachment_id, "status": "discarded"}

    def analyze_attachment_llm(self, payload: dict) -> dict:
        """Optional, visibly separate vision path. Never used by analyze-local."""
        if not payload.get("consent"):
            raise ValueError("LLM 해석 보강은 별도 동의가 필요합니다. consent를 확인하세요.")
        name = str(payload.get("name") or "")
        with self._state_lock:
            config, _diagnostics, _store, _ledger, world, _event = self._prepare()
            self._require_live(config, world)
            path = self.read_capture_path(name)
            provider = str(payload.get("provider") or config.get("ai_provider") or "local_auto")
            image = attachments_module.strip_jpeg_metadata(path.read_bytes())
        prompt = (
            "보이는 화면 종류, 읽히는 숫자와 이름, 눈에 띄는 사물이나 장면을 한국어로 짧게 나열하라. "
            "확실하지 않은 값은 '불확실'이라고 표시하고, 보이지 않는 것은 지어내지 마라. "
            "이 결과는 사용자가 검토할 힌트이므로 사실 확정이나 서사 사건 생성을 하지 마라."
        )
        if provider == "gemini":
            if config.get("gemini_consent") is not True:
                raise RuntimeError("Google 전송 동의가 꺼져 있어 이미지를 Gemini로 보내지 않았습니다.")
            api_key, key_source = self._secret_store().get_gemini_key()
            if not api_key:
                raise RuntimeError("Gemini API 키가 없습니다. 설정에서 키를 저장해 주세요.")
            image_type = attachments_module.sniff_image(image)
            mime_type = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(image_type)
            if not mime_type:
                raise ValueError("이 이미지 형식은 Gemini로 직접 보낼 수 없습니다. PNG, JPEG 또는 WebP를 사용해 주세요.")
            try:
                remote = gemini_provider.generate_vision(
                    "너는 이미지에서 실제로 보이는 정보만 읽는 한국어 보조 분석기다. 추측을 사실처럼 쓰지 않는다.",
                    prompt,
                    image_bytes=image,
                    mime_type=mime_type,
                    api_key=api_key,
                    model=config.get("gemini_model"),
                    max_tokens=600,
                )
            except gemini_provider.GeminiProviderError as exc:
                self._record_gemini_failure(config, exc, "attachment_vision")
                raise
            self._record_gemini_generation(config, remote, "attachment_vision")
            return {
                "name": name,
                "llm_used": True,
                "provider": "gemini",
                "network_used": True,
                "model": f"gemini:{remote.model}",
                "observations_text": remote.text,
                "provider_audit": self._gemini_audit(remote, key_source),
                "provenance": "gemini_vision_hint",
                "note": "이 결과는 검증되지 않은 힌트이며 사실이나 사건으로 자동 반영되지 않습니다.",
            }
        chat, model = narration_module.pick_endpoint(need_vision=True)
        if not chat:
            raise RuntimeError("로컬 비전 모델이 연결되지 않았습니다. 로컬 읽기와 직접 설명은 계속 사용할 수 있습니다.")
        text, used = narration_module.narrate(prompt, images=[str(path)], max_tokens=600, temperature=0.2, timeout=240)
        if not text:
            raise RuntimeError(str(used))
        return {
            "name": name,
            "llm_used": True,
            "provider": "local_llm",
            "network_used": False,
            "model": str(used),
            "observations_text": str(text),
            "provenance": "llm_vision_hint",
            "note": "이 결과는 검증되지 않은 힌트이며 사실로 자동 반영되지 않습니다.",
        }

    def rescan_saves(self) -> list[dict]:
        with self._scan_lock:
            return self._rescan_saves_locked()

    def _rescan_saves_locked(self) -> list[dict]:
        values = []
        current_cache: dict[tuple[str, int, int], dict] = {}
        for path in config_module.autodetect_saves():
            try:
                stat = os.stat(path)
            except OSError:
                continue
            key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns)
            identity = self._save_scan_cache.get(key)
            if identity is None:
                try:
                    summary = save_reader.read_identity_summary(path)
                    identity = {
                        "identity_status": "verified",
                        "identity_message": "선수 프로필 검증 완료",
                        "player": summary.get("player"),
                        "date": summary.get("date"),
                        "provenance": summary.get("provenance"),
                        "validation": {
                            "container": summary.get("validation", {}).get("container"),
                            "verified_chunks": summary.get("validation", {}).get("verified_chunks"),
                            "chunk_count": summary.get("validation", {}).get("chunk_count"),
                            "identity_method": summary.get("validation", {}).get("identity_method"),
                        },
                    }
                except save_reader.SaveBusyError:
                    identity = {
                        "identity_status": "busy",
                        "identity_message": "세이브 저장 중 · 잠시 후 다시 검색하세요.",
                        "player": None,
                        "date": None,
                    }
                except (save_reader.SaveIntegrityError, save_reader.SnapshotParseError):
                    identity = {
                        "identity_status": "unsupported",
                        "identity_message": "현재 버전에서 검증할 수 없는 세이브입니다.",
                        "player": None,
                        "date": None,
                    }
                except (OSError, ValueError):
                    identity = {
                        "identity_status": "unavailable",
                        "identity_message": "선수 정보를 확인하지 못했습니다.",
                        "player": None,
                        "date": None,
                    }
            current_cache[key] = identity
            values.append(
                {
                    "path": path,
                    "slot": Path(path).parent.name,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).replace(microsecond=0).isoformat(),
                    "bytes": stat.st_size,
                    **identity,
                }
            )
        self._save_scan_cache = current_cache
        return values
