#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve save snapshots to isolated, restart-aware world ledgers."""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from atomic_io import JsonStateError, atomic_write_json, load_json
from ledger_v2 import Ledger


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date_tuple(snapshot: dict | None) -> tuple[int, int, int]:
    date = (snapshot or {}).get("date", {})
    return date.get("year") or 0, date.get("month") or 0, date.get("day") or 0


def _career_year(snapshot: dict | None) -> int:
    return ((snapshot or {}).get("date") or {}).get("career_year") or 0


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/")


def _path_fingerprint(save_path: str) -> str:
    return hashlib.sha256(_normalized_path(save_path).encode("utf-8")).hexdigest()[:12]


def _base_key(snapshot: dict, save_path: str) -> str:
    """Stable world identity: save path, slot, and player id only.

    The profile fingerprint is deliberately excluded. It hashes profile bytes
    that include ability values, so it changes as the player grows and differs
    between duplicate profile copies inside one save. Keying on it silently
    branched a healthy career into a new world (see ``_legacy_base_key``).
    """
    source = snapshot.get("source", {})
    player = snapshot.get("player", {})
    payload = "|".join(
        (
            _normalized_path(save_path),
            str(source.get("slot", "")),
            str(player.get("id", "")),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _legacy_base_key(snapshot: dict, save_path: str) -> str:
    """Pre-2026-09-02 key that also hashed the mutable profile fingerprint."""
    source = snapshot.get("source", {})
    player = snapshot.get("player", {})
    payload = "|".join(
        (
            _normalized_path(save_path),
            str(source.get("slot", "")),
            str(player.get("id", "")),
            str(snapshot.get("profile_fingerprint", "")),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _is_career_restart(previous: dict | None, current: dict) -> tuple[bool, str | None]:
    if not previous:
        return False, None
    previous_career = _career_year(previous)
    current_career = _career_year(current)
    if previous_career and current_career and current_career < previous_career:
        return True, "career-year-regression"
    if _date_tuple(current) < _date_tuple(previous):
        return True, "career-date-regression"
    return False, None


class WorldStore:
    def __init__(self, data_dir: str, legacy_ledger_path: str | None = None):
        self.data_dir = Path(data_dir)
        self.worlds_dir = self.data_dir / "worlds"
        self.index_path = self.data_dir / "world-index.json"
        self.legacy_ledger_path = Path(legacy_ledger_path) if legacy_ledger_path else None
        self._lock = threading.RLock()

    @staticmethod
    def _empty_index() -> dict:
        return {"schema_version": 1, "active": {}, "worlds": {}, "updated_at": _now()}

    def _load_index(self) -> tuple[dict, str | None]:
        index, quarantined = load_json(self.index_path, self._empty_index())
        if not isinstance(index, dict):
            index = self._empty_index()
        index.setdefault("active", {})
        index.setdefault("worlds", {})
        index["schema_version"] = 1
        return index, quarantined

    def _world_id(self, base_key: str, generation: int) -> str:
        return hashlib.sha256(f"{base_key}:{generation}".encode()).hexdigest()[:16]

    def _legacy_state(self, snapshot: dict) -> dict | None:
        path = self.legacy_ledger_path
        if not path or not path.exists():
            return None
        try:
            state, _ = load_json(path, None, quarantine_corrupt=False)
        except JsonStateError:
            # The legacy file is optional migration input. Preserve a damaged
            # copy untouched and start a clean isolated world instead.
            return None
        if not isinstance(state, dict):
            return None
        player = state.get("player") or {}
        if player.get("id") != snapshot.get("player", {}).get("id"):
            return None
        return state

    def resolve(self, snapshot: dict, save_path: str) -> tuple[Ledger, dict]:
        """Return the active isolated ledger and resolution metadata."""
        with self._lock:
            self.worlds_dir.mkdir(parents=True, exist_ok=True)
            index, quarantined_index = self._load_index()
            base_key = _base_key(snapshot, save_path)
            active_id = index["active"].get(base_key)
            if not active_id:
                active_id = self._adopt_legacy_world(index, snapshot, save_path, base_key)
            generation = 1
            branch_reason = None

            if active_id:
                active_meta = index["worlds"].get(active_id, {})
                generation = int(active_meta.get("generation", 1))
                active_path = self.worlds_dir / active_id / "ledger.json"
                missing_active_ledger = not active_path.exists()
                recovery_state = self._legacy_state(snapshot) if missing_active_ledger else None
                active_ledger = Ledger(
                    str(active_path), active_id, initial_state=recovery_state
                )
                if missing_active_ledger:
                    active_ledger.save()
                restart, branch_reason = _is_career_restart(
                    active_ledger.state.get("last_snapshot"), snapshot
                )
                if not restart:
                    self._touch(index, base_key, active_id, snapshot, save_path)
                    atomic_write_json(self.index_path, index)
                    return active_ledger, {
                        "world_id": active_id,
                        "generation": generation,
                        "slot": snapshot.get("source", {}).get("slot"),
                        "player_name": snapshot.get("player", {}).get("name"),
                        "team": snapshot.get("player", {}).get("team"),
                        "created": False,
                        "branch_reason": None,
                        "quarantined_index": quarantined_index,
                        "quarantined_ledger": active_ledger.quarantined_path,
                    }
                generation += 1

            world_id = self._world_id(base_key, generation)
            world_path = self.worlds_dir / world_id / "ledger.json"
            initial_state = None
            migrated_legacy = False
            if generation == 1 and not world_path.exists():
                initial_state = self._legacy_state(snapshot)
                migrated_legacy = initial_state is not None
            ledger = Ledger(str(world_path), world_id, initial_state=initial_state)
            ledger.state["world_id"] = world_id
            if not world_path.exists():
                ledger.save()
            index["active"][base_key] = world_id
            index["worlds"][world_id] = {
                "world_id": world_id,
                "base_key": base_key,
                "generation": generation,
                "player_id": snapshot.get("player", {}).get("id"),
                "player_name": snapshot.get("player", {}).get("name"),
                "team": snapshot.get("player", {}).get("team"),
                "slot": snapshot.get("source", {}).get("slot"),
                "save_path_fingerprint": hashlib.sha256(
                    _normalized_path(save_path).encode("utf-8")
                ).hexdigest()[:12],
                "created_at": _now(),
                "last_seen_at": _now(),
                "last_date": snapshot.get("date"),
                "branch_reason": branch_reason,
                "migrated_legacy": migrated_legacy,
            }
            index["updated_at"] = _now()
            atomic_write_json(self.index_path, index)
            return ledger, {
                "world_id": world_id,
                "generation": generation,
                "slot": snapshot.get("source", {}).get("slot"),
                "player_name": snapshot.get("player", {}).get("name"),
                "team": snapshot.get("player", {}).get("team"),
                "created": True,
                "branch_reason": branch_reason,
                "migrated_legacy": migrated_legacy,
                "quarantined_index": quarantined_index,
                "quarantined_ledger": ledger.quarantined_path,
            }

    @staticmethod
    def _adopt_legacy_world(index: dict, snapshot: dict, save_path: str, base_key: str) -> str | None:
        """Re-attach a world that was indexed under the old fingerprint-bound key.

        Existing worlds are never moved or deleted; the new stable key is added
        as an alias so both the old and the new key resolve to the same world.
        """
        worlds = index.get("worlds", {})
        active = index.get("active", {})
        legacy_key = _legacy_base_key(snapshot, save_path)
        candidate_id = active.get(legacy_key)
        if not candidate_id:
            player_id = (snapshot.get("player") or {}).get("id")
            slot = (snapshot.get("source") or {}).get("slot")
            path_fp = _path_fingerprint(save_path)
            candidates = []
            for key, world_id in active.items():
                meta = worlds.get(world_id) or {}
                if (
                    meta.get("player_id") == player_id
                    and str(meta.get("slot")) == str(slot)
                    and meta.get("save_path_fingerprint") == path_fp
                ):
                    candidates.append((str(meta.get("last_seen_at") or ""), key, world_id))
            if not candidates:
                return None
            candidates.sort()
            _seen, legacy_key, candidate_id = candidates[-1]
        meta = worlds.setdefault(candidate_id, {"world_id": candidate_id})
        if meta.get("base_key") != base_key:
            meta["base_key_legacy"] = meta.get("base_key", legacy_key)
            meta["base_key"] = base_key
            meta["identity_key_migrated_at"] = _now()
        active[base_key] = candidate_id
        return candidate_id

    @staticmethod
    def _touch(index: dict, base_key: str, world_id: str, snapshot: dict, save_path: str) -> None:
        meta = index["worlds"].setdefault(world_id, {"world_id": world_id, "base_key": base_key})
        meta["last_seen_at"] = _now()
        meta["last_date"] = snapshot.get("date")
        meta["player_name"] = snapshot.get("player", {}).get("name")
        meta["team"] = snapshot.get("player", {}).get("team")
        meta["save_path_fingerprint"] = hashlib.sha256(
            _normalized_path(save_path).encode("utf-8")
        ).hexdigest()[:12]
        index["updated_at"] = _now()

    def list_worlds(self) -> list[dict]:
        with self._lock:
            index, _ = self._load_index()
            return sorted(
                index.get("worlds", {}).values(),
                key=lambda row: row.get("last_seen_at", ""),
                reverse=True,
            )

    def peek_active_world_id(self, snapshot: dict, save_path: str) -> str | None:
        """Read-only lookup of the world that ``resolve`` would return.

        It never creates, branches, touches, or rewrites the index.
        """
        with self._lock:
            index, _ = self._load_index()
            active = index.get("active", {})
            base_key = _base_key(snapshot, save_path)
            world_id = active.get(base_key) or active.get(_legacy_base_key(snapshot, save_path))
            if not world_id:
                return None
            meta = index.get("worlds", {}).get(world_id) or {}
            world_path = self.worlds_dir / world_id / "ledger.json"
            if not world_path.exists():
                return world_id
            try:
                state, _ = load_json(str(world_path), None, quarantine_corrupt=False)
            except JsonStateError:
                return world_id
            last = (state or {}).get("last_snapshot") if isinstance(state, dict) else None
            restart, _reason = _is_career_restart(last, snapshot)
            if restart:
                # resolve() would branch into a new generation.
                return None
            return world_id if meta else world_id
