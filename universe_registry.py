#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent universe registry and live-binding guard (master plan 3.5, 4.2.1, 17.5).

A universe is a story world identified by ``world_id`` from ``WorldStore``.
The registry outlives the save file: deleting ``StarPlayer.dat`` or putting
another player in the same slot never deletes a universe. It only changes the
universe's availability state.

States: ``live``, ``temporarily_missing``, ``preserved_read_only``,
``relink_pending``, ``live_relinked``. Bindings and status events are
append-only. ``world-index.json`` is migration input and is never rewritten
by this module.
"""

from __future__ import annotations

import copy
import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from atomic_io import atomic_write_json, load_json

REGISTRY_SCHEMA = 1
STATES = ("live", "temporarily_missing", "preserved_read_only", "relink_pending", "live_relinked")
LIVE_STATES = ("live", "live_relinked")
MISSING_CHECKS_BEFORE_PRESERVE = 3
MISSING_SECONDS_BEFORE_PRESERVE = 120.0

PRESERVATION_REASONS = {
    "save_missing": "세이브 삭제됨",
    "slot_identity_replaced": "슬롯의 선수가 교체됨",
    "branched_from_older_save": "과거 백업으로 분기됨",
    "migrated_from_world_index": "이전 색인에서 보존됨",
    "explicit_preserve": "사용자가 보존 처리함",
}


class WorldReadOnlyError(RuntimeError):
    """The target universe is preserved; no mutation may start."""

    code = "WORLD_READ_ONLY"


class WorldBindingChangedError(RuntimeError):
    """The request's binding token no longer matches the active binding."""

    code = "WORLD_BINDING_CHANGED"


class UniverseNotFoundError(FileNotFoundError):
    code = "UNIVERSE_NOT_FOUND"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/")


def save_fingerprint(save_path: str) -> str:
    return hashlib.sha256(_normalized_path(save_path).encode("utf-8")).hexdigest()[:12]


def _date_key(date: dict | None) -> str:
    value = date or {}
    try:
        return f"{int(value.get('year') or 0):04d}-{int(value.get('month') or 0):02d}-{int(value.get('day') or 0):02d}"
    except (TypeError, ValueError):
        return "0000-00-00"


def _date_tuple(date: dict | None) -> tuple[int, int, int]:
    value = date or {}
    return int(value.get("year") or 0), int(value.get("month") or 0), int(value.get("day") or 0)


def summarize_ledger(state: dict | None) -> dict:
    """Frozen card summary derived from a ledger state (no save required)."""
    state = state if isinstance(state, dict) else {}
    profile = state.get("career_profile") or {}
    seasons = [row for row in profile.get("seasons") or [] if isinstance(row, dict)]
    honors = [row for row in profile.get("honors") or [] if isinstance(row, dict)]
    milestones = [row for row in state.get("milestone_ledger") or [] if isinstance(row, dict)]
    archives = [row for row in state.get("daily_archive") or [] if isinstance(row, dict)]
    sessions = {key: value for key, value in (state.get("story_sessions") or {}).items() if isinstance(value, dict)}
    history = [row for row in state.get("history") or [] if isinstance(row, dict)]
    highlights: list[str] = []
    for row in honors:
        title = row.get("title")
        if title and title not in highlights:
            highlights.append(str(title))
    for row in milestones:
        label = row.get("label")
        if label and label not in highlights:
            highlights.append(str(label))
    headline = None
    for session in reversed(list(sessions.values())):
        turns = session.get("turns") or []
        if turns:
            headline = ((turns[-1].get("scene") or {}).get("title")) or headline
            break
    if not headline:
        for row in reversed(history):
            lines = row.get("game_lines") or []
            if lines:
                headline = lines[0]
                break
    last_snapshot = state.get("last_snapshot") or {}
    return {
        "seasons": len(seasons),
        "honors": len(honors),
        "milestones": len(milestones),
        "story_dates": len(sessions),
        "story_turns": sum(len(session.get("turns") or []) for session in sessions.values()),
        "articles": sum(int(row.get("article_count") or 0) for row in archives),
        "community": sum(int(row.get("board_count") or 0) for row in archives),
        "reaction_archives": len(archives),
        "verified_events": len(history),
        "highlights": highlights[:3],
        "headline": headline,
        "last_verified_date": _date_key(last_snapshot.get("date")) if last_snapshot else None,
        "last_content_hash": last_snapshot.get("content_hash") if last_snapshot else None,
        "player": copy.deepcopy(last_snapshot.get("player") or state.get("player") or {}),
        "career_year": (last_snapshot.get("date") or {}).get("career_year") if last_snapshot else None,
    }


class UniverseRegistry:
    def __init__(self, data_dir: str, *, clock=None):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "universe-registry.json"
        self.index_path = self.data_dir / "world-index.json"
        self.worlds_dir = self.data_dir / "worlds"
        self._lock = threading.RLock()
        self._clock = clock or time.time

    # ------------------------------------------------------------ storage
    @staticmethod
    def _empty() -> dict:
        return {
            "schema_version": REGISTRY_SCHEMA,
            "universes": {},
            "bindings": [],
            "status_events": [],
            "updated_at": _now(),
        }

    def _load(self) -> dict:
        registry, _quarantined = load_json(self.path, self._empty())
        if not isinstance(registry, dict):
            registry = self._empty()
        registry.setdefault("universes", {})
        registry.setdefault("bindings", [])
        registry.setdefault("status_events", [])
        registry["schema_version"] = REGISTRY_SCHEMA
        if self._migrate_index(registry):
            self._write(registry)
        return registry

    def _write(self, registry: dict) -> None:
        registry["updated_at"] = _now()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, registry)

    def _migrate_index(self, registry: dict) -> bool:
        """Adopt every world from world-index.json as a universe row.

        The index is read, never rewritten. A universe that already exists in
        the registry is left untouched.
        """
        if not self.index_path.exists():
            return False
        try:
            index, _ = load_json(self.index_path, None, quarantine_corrupt=False)
        except Exception:
            return False
        if not isinstance(index, dict):
            return False
        changed = False
        for world_id, meta in (index.get("worlds") or {}).items():
            if not isinstance(meta, dict) or world_id in registry["universes"]:
                continue
            ledger_state = self._read_ledger_state(world_id)
            summary = summarize_ledger(ledger_state)
            row = {
                "universe_id": world_id,
                "state": "preserved_read_only",
                "preservation_reason": "migrated_from_world_index",
                "generation": int(meta.get("generation") or 1),
                "base_key": meta.get("base_key"),
                "player_id": meta.get("player_id"),
                "player_name": meta.get("player_name") or (summary.get("player") or {}).get("name"),
                "team": meta.get("team") or (summary.get("player") or {}).get("team"),
                "slot": meta.get("slot"),
                "save_fingerprint": meta.get("save_path_fingerprint"),
                "save_path": None,
                "last_date": meta.get("last_date"),
                "created_at": meta.get("created_at") or _now(),
                "last_seen_at": meta.get("last_seen_at"),
                "last_read_at": None,
                "missing_checks": 0,
                "first_missing_at": None,
                "branch_reason": meta.get("branch_reason"),
                "branched_from": None,
                "summary": summary,
                "migrated_from_index_at": _now(),
            }
            registry["universes"][world_id] = row
            registry["status_events"].append(
                {"universe_id": world_id, "kind": "migrated", "reason": "migrated_from_world_index", "at": _now()}
            )
            changed = True
        return changed

    def _read_ledger_state(self, universe_id: str) -> dict | None:
        path = self.worlds_dir / str(universe_id) / "ledger.json"
        if not path.is_file():
            return None
        try:
            state, _ = load_json(path, None, quarantine_corrupt=False)
        except Exception:
            return None
        return state if isinstance(state, dict) else None

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _status(registry: dict, universe_id: str, kind: str, reason: str | None = None, **extra) -> None:
        registry["status_events"].append(
            {"universe_id": universe_id, "kind": kind, "reason": reason, "at": _now(), **extra}
        )

    @staticmethod
    def _active_binding(registry: dict, universe_id: str) -> dict | None:
        for row in reversed(registry["bindings"]):
            if row.get("universe_id") == universe_id and row.get("active"):
                return row
        return None

    @staticmethod
    def _token(row: dict, binding: dict | None) -> dict:
        state = row.get("state")
        return {
            "universe_id": row.get("universe_id"),
            "protagonist_id": str(row.get("player_id")),
            "save_fingerprint": row.get("save_fingerprint"),
            "generation": int(row.get("generation") or 1),
            "binding_id": (binding or {}).get("binding_id"),
            "state": state,
            "live": state in LIVE_STATES,
            "preservation_reason": row.get("preservation_reason"),
            "preservation_label": PRESERVATION_REASONS.get(str(row.get("preservation_reason") or ""), None),
        }

    def _bind(self, registry: dict, row: dict, save_path: str, reason: str) -> dict:
        for existing in registry["bindings"]:
            if existing.get("universe_id") == row["universe_id"] and existing.get("active"):
                existing["active"] = False
                existing["deactivated_at"] = _now()
                existing["deactivated_reason"] = reason
        binding = {
            "binding_id": hashlib.sha256(
                f"{row['universe_id']}|{save_fingerprint(save_path)}|{row.get('player_id')}|{row.get('generation')}|{_now()}|{len(registry['bindings'])}".encode("utf-8")
            ).hexdigest()[:16],
            "universe_id": row["universe_id"],
            "save_fingerprint": save_fingerprint(save_path),
            "save_path": _normalized_path(save_path),
            "slot": row.get("slot"),
            "protagonist_id": str(row.get("player_id")),
            "generation": int(row.get("generation") or 1),
            "reason": reason,
            "bound_at": _now(),
            "active": True,
        }
        registry["bindings"].append(binding)
        return binding

    # ------------------------------------------------------------ live path
    def observe_live(self, world: dict, snapshot: dict, save_path: str, ledger_state: dict | None = None) -> dict:
        """Record a successful verified read and return the binding token."""
        with self._lock:
            registry = self._load()
            universe_id = str(world.get("world_id"))
            player = snapshot.get("player") or {}
            source = snapshot.get("source") or {}
            fingerprint = save_fingerprint(save_path)
            slot = str(source.get("slot") or world.get("slot") or "")
            summary = summarize_ledger(ledger_state) if ledger_state is not None else None
            row = registry["universes"].get(universe_id)
            created = row is None
            if created:
                row = {
                    "universe_id": universe_id,
                    "state": "live",
                    "preservation_reason": None,
                    "generation": int(world.get("generation") or 1),
                    "base_key": None,
                    "player_id": player.get("id"),
                    "player_name": player.get("name"),
                    "team": player.get("team"),
                    "slot": slot,
                    "save_fingerprint": fingerprint,
                    "save_path": _normalized_path(save_path),
                    "last_date": copy.deepcopy(snapshot.get("date")),
                    "created_at": _now(),
                    "last_seen_at": None,
                    "last_read_at": None,
                    "missing_checks": 0,
                    "first_missing_at": None,
                    "branch_reason": world.get("branch_reason"),
                    "branched_from": None,
                    "summary": summary or summarize_ledger(None),
                }
                registry["universes"][universe_id] = row
                self._status(registry, universe_id, "created", world.get("branch_reason"))

            # WorldStore writes the index before the registry observes the
            # world, so a brand-new branch usually arrives pre-registered by
            # the index migration. "Never bound" is the real novelty signal.
            never_bound = not any(binding.get("universe_id") == universe_id for binding in registry["bindings"])
            if never_bound and world.get("branch_reason"):
                row["branch_reason"] = world.get("branch_reason")
                self._mark_branch_source(registry, row, snapshot, fingerprint, slot)

            state = row.get("state") or "live"
            relink_required = False
            if state in ("preserved_read_only", "relink_pending"):
                relink_required = self._returning_save(registry, row, snapshot, ledger_state)
            if not relink_required:
                if state not in LIVE_STATES:
                    previous_reason = row.get("preservation_reason")
                    row["state"] = "live_relinked" if state == "relink_pending" or row.get("relinked_at") else "live"
                    if previous_reason == "migrated_from_world_index":
                        kind = "activated"
                    elif state == "temporarily_missing":
                        kind = "recovered"
                    else:
                        kind = "relinked"
                    self._status(registry, universe_id, kind, previous_reason)
                    row["preservation_reason"] = None
                row["missing_checks"] = 0
                row["first_missing_at"] = None
                row["player_id"] = player.get("id", row.get("player_id"))
                row["player_name"] = player.get("name") or row.get("player_name")
                row["team"] = player.get("team") or row.get("team")
                row["slot"] = slot or row.get("slot")
                row["save_fingerprint"] = fingerprint
                row["save_path"] = _normalized_path(save_path)
                row["generation"] = int(world.get("generation") or row.get("generation") or 1)
                row["last_date"] = copy.deepcopy(snapshot.get("date"))
                row["last_seen_at"] = _now()
                if summary is not None:
                    row["summary"] = summary
                binding = self._active_binding(registry, universe_id)
                if (
                    binding is None
                    or binding.get("save_fingerprint") != fingerprint
                    or binding.get("protagonist_id") != str(player.get("id"))
                    or int(binding.get("generation") or 0) != int(row["generation"])
                ):
                    binding = self._bind(registry, row, save_path, "observed_live" if created else "rebound")
                self._replace_slot_siblings(registry, row, fingerprint, slot)
            else:
                binding = None
            self._write(registry)
            return self._token(row, binding)

    def _mark_branch_source(self, registry: dict, row: dict, snapshot: dict, fingerprint: str, slot: str) -> None:
        """A new generation was created from an older save: preserve the source."""
        candidates = [
            other
            for other in registry["universes"].values()
            if other is not row
            and other.get("save_fingerprint") == fingerprint
            and str(other.get("slot")) == slot
            and other.get("player_id") == (snapshot.get("player") or {}).get("id")
        ]
        candidates.sort(key=lambda other: int(other.get("generation") or 0))
        if candidates:
            source = candidates[-1]
            row["branched_from"] = source.get("universe_id")
            if source.get("state") in LIVE_STATES or source.get("state") == "temporarily_missing":
                source["state"] = "preserved_read_only"
                source["preservation_reason"] = "branched_from_older_save"
                self._status(registry, source["universe_id"], "preserved", "branched_from_older_save", branch=row["universe_id"])
                for binding in registry["bindings"]:
                    if binding.get("universe_id") == source["universe_id"] and binding.get("active"):
                        binding["active"] = False
                        binding["deactivated_at"] = _now()
                        binding["deactivated_reason"] = "branched_from_older_save"

    def _replace_slot_siblings(self, registry: dict, row: dict, fingerprint: str, slot: str) -> None:
        """Another player now occupies this path/slot: preserve the former universes."""
        for other in registry["universes"].values():
            if other is row:
                continue
            if other.get("save_fingerprint") != fingerprint or str(other.get("slot")) != slot:
                continue
            if other.get("player_id") == row.get("player_id"):
                continue
            if other.get("state") in LIVE_STATES or other.get("state") == "temporarily_missing":
                other["state"] = "preserved_read_only"
                other["preservation_reason"] = "slot_identity_replaced"
                other["missing_checks"] = 0
                other["first_missing_at"] = None
                self._status(registry, other["universe_id"], "preserved", "slot_identity_replaced", replaced_by=row["universe_id"])
                for binding in registry["bindings"]:
                    if binding.get("universe_id") == other["universe_id"] and binding.get("active"):
                        binding["active"] = False
                        binding["deactivated_at"] = _now()
                        binding["deactivated_reason"] = "slot_identity_replaced"

    def _returning_save(self, registry: dict, row: dict, snapshot: dict, ledger_state: dict | None) -> bool:
        """Decide whether a returning save needs an explicit relink.

        Returns True when the universe must stay read-only pending a decision.
        Identical content (same verified hash as the preserved last snapshot)
        resumes automatically because nothing diverged. Anything newer needs
        an explicit `relink_confirm`. Older dates never reach here: WorldStore
        already created a chronology-safe branch generation.
        """
        summary = row.get("summary") or {}
        preserved_hash = summary.get("last_content_hash") or ((ledger_state or {}).get("last_snapshot") or {}).get("content_hash")
        preserved_date = summary.get("last_verified_date") or _date_key(row.get("last_date"))
        current_hash = snapshot.get("content_hash")
        current_date = _date_key(snapshot.get("date"))
        if row.get("preservation_reason") == "migrated_from_world_index":
            return False
        if preserved_hash and current_hash == preserved_hash:
            row["relink_candidate"] = None
            row["relinked_at"] = _now()
            self._status(registry, row["universe_id"], "relinked", "identical_content_resumed")
            return False
        if current_date < preserved_date:
            # Defensive: an older save should have branched. Keep read-only.
            row["state"] = "relink_pending"
            row["relink_candidate"] = {"content_hash": current_hash, "game_date": current_date, "compatible": False, "reason": "older_than_preserved"}
            return True
        if row.get("state") != "relink_pending":
            row["state"] = "relink_pending"
            self._status(registry, row["universe_id"], "relink_pending", row.get("preservation_reason"))
        row["relink_candidate"] = {
            "content_hash": current_hash,
            "game_date": current_date,
            "preserved_date": preserved_date,
            "compatible": True,
            "reason": "newer_compatible_save",
            "player_id": (snapshot.get("player") or {}).get("id"),
        }
        return True

    # ------------------------------------------------------------ missing path
    def observe_missing(self, save_path: str) -> list[dict]:
        """A configured save could not be read. Never deletes or rewrites history."""
        with self._lock:
            registry = self._load()
            fingerprint = save_fingerprint(save_path)
            affected = []
            now_seconds = float(self._clock())
            for row in registry["universes"].values():
                if row.get("save_fingerprint") != fingerprint or row.get("state") not in (*LIVE_STATES, "temporarily_missing"):
                    continue
                if row.get("state") in LIVE_STATES:
                    row["state"] = "temporarily_missing"
                    row["missing_checks"] = 1
                    row["first_missing_at"] = now_seconds
                    self._status(registry, row["universe_id"], "temporarily_missing", "file_not_found")
                else:
                    row["missing_checks"] = int(row.get("missing_checks") or 0) + 1
                    first = float(row.get("first_missing_at") or now_seconds)
                    if (
                        row["missing_checks"] >= MISSING_CHECKS_BEFORE_PRESERVE
                        and now_seconds - first >= MISSING_SECONDS_BEFORE_PRESERVE
                    ):
                        row["state"] = "preserved_read_only"
                        row["preservation_reason"] = "save_missing"
                        self._status(registry, row["universe_id"], "preserved", "save_missing", checks=row["missing_checks"])
                        for binding in registry["bindings"]:
                            if binding.get("universe_id") == row["universe_id"] and binding.get("active"):
                                binding["active"] = False
                                binding["deactivated_at"] = _now()
                                binding["deactivated_reason"] = "save_missing"
                affected.append(copy.deepcopy(row))
            if affected:
                self._write(registry)
            return affected

    # ------------------------------------------------------------ guard
    def require_live_binding(self, token: dict | None, *, universe_id: str | None = None, protagonist_id: object = None, save_fingerprint: str | None = None, generation: int | None = None) -> dict:
        """Verify the shared mutation guard (17.5). Raises before any side effect."""
        with self._lock:
            registry = self._load()
            token = token or {}
            target = str(universe_id or token.get("universe_id") or "")
            row = registry["universes"].get(target)
            if row is None:
                raise UniverseNotFoundError("알 수 없는 세계선입니다.")
            if row.get("state") not in LIVE_STATES:
                label = PRESERVATION_REASONS.get(str(row.get("preservation_reason") or ""), row.get("state"))
                raise WorldReadOnlyError(
                    f"이 세계선은 보존·관람 전용입니다 ({label}). 기록을 읽고 검색하고 내보낼 수는 있지만 새 사건은 만들 수 없습니다."
                )
            binding = self._active_binding(registry, target)
            if binding is None:
                raise WorldBindingChangedError("활성 세이브 연결이 없습니다. 최신 세이브 확인을 다시 실행하세요.")
            expected = {
                "protagonist_id": str(protagonist_id if protagonist_id is not None else token.get("protagonist_id")),
                "save_fingerprint": save_fingerprint or token.get("save_fingerprint"),
                "generation": int(generation if generation is not None else token.get("generation") or 0),
            }
            actual = {
                "protagonist_id": str(binding.get("protagonist_id")),
                "save_fingerprint": binding.get("save_fingerprint"),
                "generation": int(binding.get("generation") or 0),
            }
            if expected != actual:
                changed = ", ".join(sorted(key for key in expected if expected[key] != actual[key]))
                raise WorldBindingChangedError(f"세이브 연결이 바뀌었습니다 (변경: {changed}). 같은 세이브에서 다시 시도하세요.")
            return self._token(row, binding)

    # ------------------------------------------------------------ reads
    def get(self, universe_id: str) -> dict | None:
        with self._lock:
            registry = self._load()
            row = registry["universes"].get(str(universe_id))
            return copy.deepcopy(row) if row else None

    def touch_read(self, universe_id: str) -> None:
        with self._lock:
            registry = self._load()
            row = registry["universes"].get(str(universe_id))
            if row:
                row["last_read_at"] = _now()
                self._write(registry)

    def update_summary(self, universe_id: str, ledger_state: dict | None) -> bool:
        """Refresh the frozen card summary after a mutation; writes only on change."""
        with self._lock:
            registry = self._load()
            row = registry["universes"].get(str(universe_id))
            if row is None:
                return False
            summary = summarize_ledger(ledger_state)
            if row.get("summary") == summary:
                return False
            row["summary"] = summary
            if summary.get("last_verified_date"):
                last = (ledger_state or {}).get("last_snapshot") or {}
                if last.get("date"):
                    row["last_date"] = copy.deepcopy(last.get("date"))
            self._write(registry)
            return True

    def _capabilities(self, row: dict, live_paths: set[str]) -> dict:
        state = row.get("state")
        path = row.get("save_path")
        file_present = bool(path and os.path.isfile(path))
        return {
            "can_continue": state in LIVE_STATES and file_present,
            "can_view": True,
            "can_search": True,
            "can_export": True,
            "can_relink": state in ("preserved_read_only", "relink_pending") and bool(row.get("relink_candidate") or file_present),
            "read_only": state not in LIVE_STATES,
        }

    def card(self, row: dict, live_paths: set[str] | None = None) -> dict:
        summary = row.get("summary") or {}
        state = row.get("state")
        badge = {
            "live": "진행 가능",
            "live_relinked": "진행 가능",
            "temporarily_missing": "세이브 확인 중",
            "preserved_read_only": "보존·관람 전용",
            "relink_pending": "재연결 검토",
        }.get(str(state), "보존·관람 전용")
        ledger_path = self.worlds_dir / str(row.get("universe_id")) / "ledger.json"
        try:
            archive_bytes = ledger_path.stat().st_size
        except OSError:
            archive_bytes = 0
        return {
            "universe_id": row.get("universe_id"),
            "state": state,
            "availability_badge": badge,
            "preservation_reason": row.get("preservation_reason"),
            "preservation_label": PRESERVATION_REASONS.get(str(row.get("preservation_reason") or ""), None),
            "player_name": row.get("player_name"),
            "player_id": row.get("player_id"),
            "team": row.get("team"),
            "slot": row.get("slot"),
            "generation": row.get("generation"),
            "career_year": summary.get("career_year"),
            "last_date": row.get("last_date"),
            "last_verified_date": summary.get("last_verified_date"),
            "last_seen_at": row.get("last_seen_at"),
            "last_read_at": row.get("last_read_at"),
            "headline": summary.get("headline"),
            "highlights": list(summary.get("highlights") or []),
            "counts": {
                key: summary.get(key, 0)
                for key in ("seasons", "honors", "milestones", "story_dates", "story_turns", "articles", "community", "reaction_archives", "verified_events")
            },
            "archive_bytes": archive_bytes,
            "branched_from": row.get("branched_from"),
            "branch_reason": row.get("branch_reason"),
            "relink_candidate": copy.deepcopy(row.get("relink_candidate")),
            "save_path": row.get("save_path"),
            "capabilities": self._capabilities(row, live_paths or set()),
        }

    def list_universes(self, live_candidates: list[dict] | None = None) -> dict:
        """Merge preserved registry rows with live discovery candidates."""
        with self._lock:
            registry = self._load()
            live_paths = {
                _normalized_path(str(row.get("path")))
                for row in (live_candidates or [])
                if row.get("path")
            }
            cards = [self.card(row, live_paths) for row in registry["universes"].values()]
            cards.sort(key=lambda row: (row.get("last_read_at") or row.get("last_seen_at") or ""), reverse=True)
            return {
                "schema_version": REGISTRY_SCHEMA,
                "universes": cards,
                "live_candidates": copy.deepcopy(live_candidates or []),
                "preserved_count": sum(1 for row in cards if row["capabilities"]["read_only"]),
                "live_count": sum(1 for row in cards if not row["capabilities"]["read_only"]),
            }

    def status_events(self, universe_id: str) -> list[dict]:
        with self._lock:
            registry = self._load()
            return [copy.deepcopy(row) for row in registry["status_events"] if row.get("universe_id") == str(universe_id)]

    def bindings(self, universe_id: str) -> list[dict]:
        with self._lock:
            registry = self._load()
            return [copy.deepcopy(row) for row in registry["bindings"] if row.get("universe_id") == str(universe_id)]

    # ------------------------------------------------------------ relink
    def relink_preview(self, universe_id: str, snapshot: dict | None, save_path: str | None) -> dict:
        with self._lock:
            registry = self._load()
            row = registry["universes"].get(str(universe_id))
            if row is None:
                raise UniverseNotFoundError("알 수 없는 세계선입니다.")
            summary = row.get("summary") or {}
            preserved_date = summary.get("last_verified_date") or _date_key(row.get("last_date"))
            if not snapshot:
                return {
                    "universe_id": row["universe_id"],
                    "state": row.get("state"),
                    "identity_match": False,
                    "chronology": "unknown",
                    "can_relink": False,
                    "reason": "복원된 세이브를 읽지 못했습니다.",
                    "preserved_date": preserved_date,
                }
            player = snapshot.get("player") or {}
            identity_match = player.get("id") == row.get("player_id")
            current_date = _date_key(snapshot.get("date"))
            if current_date < preserved_date:
                chronology = "older"
            elif snapshot.get("content_hash") == summary.get("last_content_hash"):
                chronology = "identical"
            else:
                chronology = "newer"
            can_relink = identity_match and chronology in ("identical", "newer") and row.get("state") in ("preserved_read_only", "relink_pending")
            return {
                "universe_id": row["universe_id"],
                "state": row.get("state"),
                "identity_match": identity_match,
                "preserved_player": {"id": row.get("player_id"), "name": row.get("player_name"), "team": row.get("team")},
                "candidate_player": {"id": player.get("id"), "name": player.get("name"), "team": player.get("team")},
                "preserved_date": preserved_date,
                "candidate_date": current_date,
                "chronology": chronology,
                "can_relink": can_relink,
                "reason": (
                    "선수 정체성이 다릅니다." if not identity_match
                    else "보존된 마지막 날짜보다 오래된 세이브입니다. 재연결 대신 분기됩니다." if chronology == "older"
                    else "같은 선수, 같은 시점 이후의 세이브입니다."
                ),
                "save_fingerprint": save_fingerprint(save_path) if save_path else None,
            }

    def relink_confirm(self, universe_id: str, snapshot: dict, save_path: str, *, note: str = "", ledger_state: dict | None = None) -> dict:
        preview = self.relink_preview(universe_id, snapshot, save_path)
        if not preview.get("can_relink"):
            raise WorldReadOnlyError(f"재연결할 수 없습니다: {preview.get('reason')}")
        with self._lock:
            registry = self._load()
            row = registry["universes"][str(universe_id)]
            inactive_since = row.get("last_seen_at")
            row["state"] = "live_relinked"
            row["preservation_reason"] = None
            row["relink_candidate"] = None
            row["relinked_at"] = _now()
            row["missing_checks"] = 0
            row["first_missing_at"] = None
            row["save_fingerprint"] = save_fingerprint(save_path)
            row["save_path"] = _normalized_path(save_path)
            row["last_date"] = copy.deepcopy(snapshot.get("date"))
            row["last_seen_at"] = _now()
            if ledger_state is not None:
                row["summary"] = summarize_ledger(ledger_state)
            binding = self._bind(registry, row, save_path, "relink_confirmed")
            self._status(
                registry, row["universe_id"], "relinked", "user_confirmed",
                note=str(note or ""), inactive_since=inactive_since, candidate_date=preview.get("candidate_date"),
            )
            self._write(registry)
            return self._token(row, binding)

    # ------------------------------------------------------------ export
    def export_bundle(self, universe_id: str) -> dict:
        row = self.get(universe_id)
        if row is None:
            raise UniverseNotFoundError("알 수 없는 세계선입니다.")
        return {
            "schema_version": REGISTRY_SCHEMA,
            "exported_at": _now(),
            "universe": row,
            "bindings": self.bindings(universe_id),
            "status_events": self.status_events(universe_id),
            "ledger": self._read_ledger_state(universe_id),
        }
