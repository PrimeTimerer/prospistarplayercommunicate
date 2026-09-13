#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atomic per-world state ledger and conservative snapshot diff engine."""

from __future__ import annotations

import copy
import glob
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone

import career_engine
import editorial_engine
import memory_windows
import narrative_state
import personal_context
import stat_engine
import star_interactions
import story_engine
import story_desk
import chronicle
import value_engine
import world_identity
from atomic_io import atomic_write_json, load_json

SCHEMA_VERSION = 5

# Ledger v5 (master plan 13.2) adds narrative stores next to the v4 stores.
# Every v5 store is additive; v4 rows are never rewritten by migration.
V5_LIST_STORES = (
    "conversation_turns",
    "relationship_events",
    "thread_events",
    "world_events",
    "reaction_instances",
    "memory_pins",
    "narrative_prop_events",
    "migration_log",
    "attachment_observations",
    "attachment_fact_links",
    "star_interaction_events",
    "narrative_lifetime_events",
)
V5_DICT_STORES = (
    "world_entities",
    "relationship_edges",
    "narrative_threads",
    "fact_registry",
    "narrative_props",
    "attachment_records",
    "visual_parser_profiles",
    "star_interactions",
)

COUNTING_STATS = (
    "pit_IP",
    "pit_TBF",
    "pit_H",
    "pit_K",
    "pit_W",
    "bat_AB",
    "bat_H",
    "bat_HR",
    "bat_RBI",
    "bat_R",
    "bat_SO",
    "bat_SB",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date_tuple(date: dict | None) -> tuple[int, int, int]:
    date = date or {}
    return date.get("year") or 0, date.get("month") or 0, date.get("day") or 0


def _default_narrative_memory() -> dict:
    return {"signatures": [], "recent_openings": [], "recent_outlines": []}


def _default_state(world_id: str | None = None) -> dict:
    state = {
        "schema_version": SCHEMA_VERSION,
        "world_id": world_id,
        "player": None,
        "last_snapshot": None,
        "history": [],
        "career_profile": career_engine.empty_profile(),
        "milestone_ledger": [],
        "daily_archive": [],
        "parser_migrations": [],
        "day_snapshots": {},
        "story_sessions": {},
        "day_contexts": {},
        "community_memory": {
            "nicknames": [],
            "memes": [],
            "open_loops": [],
            "recent_phrases": [],
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    for key in V5_LIST_STORES:
        state[key] = []
    for key in V5_DICT_STORES:
        state[key] = {}
    state["narrative_memory"] = _default_narrative_memory()
    return state


def _event_role(delta: dict) -> str:
    batting = any(delta.get(key, 0) > 0 for key in ("bat_AB", "bat_H", "bat_HR", "bat_RBI"))
    pitching = any(delta.get(key, 0) > 0 for key in ("pit_IP", "pit_TBF", "pit_K", "pit_H"))
    if batting and pitching:
        return "two_way"
    if batting:
        return "batting"
    if pitching:
        return "pitching"
    return "no_appearance"


def _looks_like_accumulator_reset(previous: dict, current: dict, date_advanced: bool) -> bool:
    if not date_advanced:
        return False
    comparable = [(previous.get(key, 0), current.get(key, 0)) for key in COUNTING_STATS]
    meaningful = [(old, new) for old, new in comparable if isinstance(old, (int, float)) and old >= 5]
    if not meaningful:
        return False
    large_drops = sum(1 for old, new in meaningful if new <= old * 0.35)
    return large_drops >= max(2, len(meaningful) // 2)


class Ledger:
    """One world ledger. World selection is owned by ``WorldStore``."""

    def __init__(
        self,
        path: str,
        world_id: str | None = None,
        initial_state: dict | None = None,
        *,
        read_only: bool = False,
    ):
        self.path = path
        self.read_only = bool(read_only)
        default = copy.deepcopy(initial_state) if initial_state else _default_state(world_id)
        loaded, quarantined = load_json(path, default, quarantine_corrupt=not self.read_only)
        self.quarantined_path = quarantined
        loaded_version = self._loaded_version(loaded)
        self.migration_backup: str | None = None
        if (
            not self.read_only
            and loaded_version is not None
            and loaded_version < SCHEMA_VERSION
            and os.path.isfile(path)
        ):
            self.migration_backup = self._backup_before_migration(path, loaded_version)
        self.state = self._migrate(loaded, world_id)
        if loaded_version is not None and loaded_version < SCHEMA_VERSION:
            self.state.setdefault("migration_log", []).append(
                {
                    "from_schema": loaded_version,
                    "to_schema": SCHEMA_VERSION,
                    "backup": self.migration_backup,
                    "at": _now(),
                    "policy": "additive; v4 rows are never rewritten",
                }
            )

    @staticmethod
    def _loaded_version(state: object) -> int | None:
        if not isinstance(state, dict):
            return None
        try:
            return int(state.get("schema_version") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _backup_before_migration(path: str, loaded_version: int) -> str | None:
        """Copy the pre-migration ledger once so a rollback can reopen it."""
        directory = os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        pattern = os.path.join(directory, f"{stem}.v{loaded_version}-backup-*.json")
        existing = sorted(glob.glob(pattern))
        if existing:
            return existing[-1]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = os.path.join(directory, f"{stem}.v{loaded_version}-backup-{stamp}.json")
        try:
            shutil.copy2(path, target)
        except OSError:
            return None
        return target

    @staticmethod
    def _migrate(state: dict, world_id: str | None) -> dict:
        migrated = _default_state(world_id)
        if isinstance(state, dict):
            for key in migrated:
                if key in state:
                    migrated[key] = state[key]
            # Optional v5 extension: older ledgers need no rewrite, but a saved
            # director journal must survive every reopen of the allowlist loader.
            if story_desk.STORE in state:
                if not isinstance(state[story_desk.STORE], dict):
                    raise ValueError("서사 작업실의 보관 형식이 올바르지 않습니다. 원본 원장을 유지했습니다.")
                migrated[story_desk.STORE] = copy.deepcopy(state[story_desk.STORE])
            if chronicle.STORE in state:
                if not isinstance(state[chronicle.STORE], dict):
                    raise ValueError("종합 스토리 보관 형식이 올바르지 않습니다. 원본 원장을 유지했습니다.")
                migrated[chronicle.STORE] = copy.deepcopy(state[chronicle.STORE])
        migrated["schema_version"] = SCHEMA_VERSION
        if world_id:
            migrated["world_id"] = world_id
        for key in V5_LIST_STORES:
            migrated[key] = migrated.get(key) if isinstance(migrated.get(key), list) else []
        for key in V5_DICT_STORES:
            migrated[key] = migrated.get(key) if isinstance(migrated.get(key), dict) else {}
        narrative_memory = migrated.get("narrative_memory")
        if not isinstance(narrative_memory, dict):
            narrative_memory = _default_narrative_memory()
        for key, default in _default_narrative_memory().items():
            if not isinstance(narrative_memory.get(key), list):
                narrative_memory[key] = default
        migrated["narrative_memory"] = narrative_memory
        memory = migrated.get("community_memory") or {}
        for key in ("nicknames", "memes", "open_loops", "recent_phrases"):
            value = memory.get(key)
            memory[key] = value if isinstance(value, list) else []
        migrated["community_memory"] = memory
        migrated["history"] = migrated.get("history") if isinstance(migrated.get("history"), list) else []
        migrated["career_profile"] = career_engine.migrate_profile(
            migrated.get("career_profile")
        )
        migrated["milestone_ledger"] = (
            migrated.get("milestone_ledger")
            if isinstance(migrated.get("milestone_ledger"), list)
            else []
        )
        migrated["daily_archive"] = (
            migrated.get("daily_archive")
            if isinstance(migrated.get("daily_archive"), list)
            else []
        )
        migrated["parser_migrations"] = (
            migrated.get("parser_migrations")
            if isinstance(migrated.get("parser_migrations"), list)
            else []
        )
        for key in ("day_snapshots", "story_sessions", "day_contexts"):
            migrated[key] = migrated.get(key) if isinstance(migrated.get(key), dict) else {}
        return migrated

    def save(self) -> None:
        if self.read_only:
            raise RuntimeError("read-only ledger view cannot be saved (preserved universe)")
        self.state["updated_at"] = _now()
        atomic_write_json(self.path, self.state)

    def classify(self, snapshot: dict) -> dict:
        """Classify without mutation. Call ``commit`` only after outputs succeed."""
        previous = self.state.get("last_snapshot")
        event = {
            "kind": None,
            "delta": {},
            "milestones": [],
            "career_milestones": [],
            "snapshot": snapshot,
            "assess": stat_engine.assess(snapshot.get("stats", {})),
            "role": "no_appearance",
            "baseline_only": False,
        }
        if previous is None:
            event["kind"] = "WORLD_INIT"
            event["baseline_only"] = True
            return event
        if previous.get("content_hash") == snapshot.get("content_hash"):
            event["kind"] = "NO_CHANGE"
            return event
        if (
            _date_tuple(previous.get("date")) == _date_tuple(snapshot.get("date"))
            and previous.get("stats", {}) == snapshot.get("stats", {})
            and (previous.get("player") or {}).get("id")
            == (snapshot.get("player") or {}).get("id")
        ):
            event["kind"] = "NO_CHANGE"
            return event

        previous_stats = previous.get("stats", {})
        current_stats = snapshot.get("stats", {})
        delta = {}
        for key in COUNTING_STATS:
            if key == "pit_IP":
                # Innings are stored as X.1 / X.2; subtract in outs so that
                # 6.1 - 5.2 is 0.2 (two outs), never a decimal remainder.
                value = stat_engine.innings_delta(
                    current_stats.get(key, 0), previous_stats.get(key, 0)
                )
            else:
                value = current_stats.get(key, 0) - previous_stats.get(key, 0)
            if value != 0:
                delta[key] = value
        event["delta"] = delta
        event["role"] = _event_role(delta)
        date_advanced = _date_tuple(snapshot.get("date")) > _date_tuple(previous.get("date"))
        any_up = any(value > 0 for value in delta.values())
        any_down = any(value < 0 for value in delta.values())

        if _looks_like_accumulator_reset(previous_stats, current_stats, date_advanced):
            event["kind"] = "SEASON_ROLLOVER"
            event["baseline_only"] = True
            event["career_milestones"] = career_engine.detect_snapshot_milestones(
                self.state["career_profile"],
                self.state["milestone_ledger"],
                previous,
                snapshot,
                season_rollover=True,
            )
            event["milestones"].extend(
                career_engine.milestone_tuples(event["career_milestones"])
            )
            return event

        event["milestones"] = stat_engine.new_milestones(previous_stats, current_stats)
        if any_down and not any_up:
            event["kind"] = "CORRECTION"
        elif any_up and date_advanced:
            event["kind"] = "NEW_GAME"
        elif any_up:
            event["kind"] = "SEASON_UPDATE"
        elif date_advanced and not delta:
            event["kind"] = "REST_DAY"
        else:
            event["kind"] = "SEASON_UPDATE"
        event["career_milestones"] = career_engine.detect_snapshot_milestones(
            self.state["career_profile"],
            self.state["milestone_ledger"],
            previous,
            snapshot,
        )
        event["milestones"].extend(
            career_engine.milestone_tuples(event["career_milestones"])
        )
        return event

    def commit(self, event: dict) -> None:
        snapshot = event["snapshot"]
        previous = self.state.get("last_snapshot")
        self._seal_before(story_engine.date_key(snapshot))
        if self.state.get("world_id") is None:
            payload = f"{snapshot['player']['id']}:{snapshot.get('profile_fingerprint', '')}"
            self.state["world_id"] = hashlib.sha256(payload.encode()).hexdigest()[:16]
        if self.state.get("player") is None:
            self.state["player"] = snapshot["player"]
        profile = self.state["career_profile"]
        if not profile.get("tracking_started_at"):
            profile["tracking_started_at"] = _now()

        kind = event["kind"]
        if kind == "SEASON_ROLLOVER" and previous:
            career_engine.archive_completed_season(profile, previous)
        existing_milestone_ids = {
            str(row.get("id"))
            for row in self.state["milestone_ledger"]
            if isinstance(row, dict)
        }
        for row in event.get("career_milestones", []):
            if row.get("id") not in existing_milestone_ids:
                self.state["milestone_ledger"].append(row)
                existing_milestone_ids.add(row.get("id"))
        recordable = {
            "NEW_GAME",
            "SEASON_UPDATE",
            "WORLD_INIT",
            "CORRECTION",
            "SEASON_ROLLOVER",
            "REST_DAY",
        }
        if kind in recordable:
            source_hash = snapshot.get("content_hash")
            last_hash = self.state["history"][-1].get("source_hash") if self.state["history"] else None
            if source_hash != last_hash:
                game_lines = [] if event.get("baseline_only") else stat_engine.describe_game(event["delta"])
                self.state["history"].append(
                    {
                        "kind": kind,
                        "date": snapshot["date"],
                        "delta": event["delta"],
                        "milestones": event.get("milestones", []),
                        "career_milestones": event.get("career_milestones", []),
                        "game_lines": game_lines,
                        "role": event.get("role", "no_appearance"),
                        "baseline_only": bool(event.get("baseline_only")),
                        "source_hash": source_hash,
                        "committed_at": _now(),
                    }
                )
        self._remember_day_snapshot(snapshot, event)
        self.state["last_snapshot"] = snapshot
        self.save()

    def remember(self, kind: str, text: str) -> None:
        bucket = self.state["community_memory"].get(kind)
        if bucket is not None and text and text not in bucket:
            bucket.append(text)
            self.state["community_memory"][kind] = bucket[-60:]

    def recent_events(self, count: int = 5) -> list[dict]:
        return self.state["history"][-count:]

    def remember_daily_archive(self, row: dict) -> None:
        archive = [
            item
            for item in self.state["daily_archive"]
            if item.get("id") != row.get("id")
        ]
        archive.append(row)
        self.state["daily_archive"] = archive

    def recent_daily_archive(self, count: int = 30) -> list[dict]:
        return self.state["daily_archive"][-count:]

    def _seal_before(self, current_date: str) -> None:
        for game_date, session in self.state.get("story_sessions", {}).items():
            if game_date < current_date and isinstance(session, dict):
                story_engine.seal(session)
        for game_date, context in self.state.get("day_contexts", {}).items():
            if game_date < current_date and isinstance(context, dict):
                value_engine.seal(context)
        for game_date, row in self.state.get("day_snapshots", {}).items():
            if game_date < current_date and isinstance(row, dict):
                row["status"] = "sealed"
                row.setdefault("sealed_at", _now())

    def _remember_day_snapshot(self, snapshot: dict, event: dict | None = None) -> dict:
        game_date = story_engine.date_key(snapshot)
        existing = self.state["day_snapshots"].get(game_date)
        if isinstance(existing, dict) and existing.get("status") == "sealed":
            return existing
        row = {
            "schema_version": 1,
            "game_date": game_date,
            "status": "open",
            "verified_snapshot": story_engine.snapshot_fact(snapshot),
            "event": {
                "kind": (event or {}).get("kind"),
                "role": (event or {}).get("role", "no_appearance"),
                "delta": copy.deepcopy((event or {}).get("delta") or {}),
                "milestones": copy.deepcopy((event or {}).get("milestones") or []),
                "baseline_only": bool((event or {}).get("baseline_only")),
            }
            if event
            else copy.deepcopy((existing or {}).get("event")),
            "created_at": (existing or {}).get("created_at") or _now(),
            "updated_at": _now(),
            "sealed_at": None,
        }
        self.state["day_snapshots"][game_date] = row
        return row

    def story_view(self, snapshot: dict) -> dict:
        game_date = story_engine.date_key(snapshot)
        universe_id = str(self.state.get("world_id") or "")
        protagonist_id = (snapshot.get("player") or {}).get("id")
        memory_view = memory_windows.build_view(
            self.state,
            as_of_date=game_date,
            universe_id=universe_id,
            protagonist_id=protagonist_id,
        )
        current = self.state.get("story_sessions", {}).get(game_date)
        if not isinstance(current, dict):
            current = story_engine.new_session(snapshot)
        history = []
        for key, session in self.state.get("story_sessions", {}).items():
            if not isinstance(session, dict):
                continue
            turns = session.get("turns") or []
            history.append(
                {
                    "game_date": key,
                    "status": "sealed" if key < game_date else session.get("status", "open"),
                    "turn_count": len(turns),
                    "headline": ((turns[-1].get("scene") or {}).get("title") if turns else "기록된 개입 없음"),
                    "updated_at": session.get("updated_at"),
                }
            )
        history.sort(key=lambda row: row["game_date"], reverse=True)
        conversation = [
            self._public_turn(row)
            for row in self.state.get("conversation_turns", [])
            if isinstance(row, dict) and row.get("game_date") == game_date
        ]
        props = [
            row
            for row in self.state.get("narrative_props", {}).values()
            if isinstance(row, dict)
        ]
        threads = [
            {
                "thread_id": row.get("thread_id"),
                "thread_type": row.get("thread_type"),
                "label": row.get("label"),
                "state": row.get("state"),
                "opened_on": row.get("opened_on"),
                "last_event_on": row.get("last_event_on"),
                "participants": list(row.get("participants") or []),
            }
            for row in self.state.get("narrative_threads", {}).values()
            if isinstance(row, dict)
        ]
        return {
            "game_date": game_date,
            "catalog": story_engine.catalog(),
            "current": story_engine.public_session(current),
            "history": history,
            "conversation": conversation,
            "conversation_total": len(self.state.get("conversation_turns", [])),
            "props": [self._public_prop(row) for row in props],
            "threads": threads,
            "relationships": copy.deepcopy(self.state.get("relationship_edges", {})),
            "interactions": star_interactions.summaries(
                self.state,
                universe_id=universe_id,
                protagonist_id=protagonist_id,
                game_date=game_date,
            ),
            "personal_context": personal_context.view(
                self.state,
                universe_id=universe_id,
                protagonist_id=protagonist_id,
                game_date=game_date,
            ),
            "counterparts": world_identity.counterparts(
                self.state,
                universe_id=universe_id,
                protagonist_id=protagonist_id,
                game_date=game_date,
            ),
            "memory_views": memory_view,
            "worldbook_catalog": {
                "personal_context": personal_context.catalog(),
                "counterparts": world_identity.catalog(),
            },
            "storage_policy": "append_only_per_day",
            "date_advances_game": False,
            "renderer_neutral": True,
        }

    @staticmethod
    def _public_turn(row: dict) -> dict:
        persona = row.get("reply_persona")
        try:
            import personas as personas_module

            persona_label = personas_module.label(persona, korean=True) if persona and persona != "narrator" else None
        except Exception:  # pragma: no cover - label is cosmetic
            persona_label = None
        return {
            "turn_id": row.get("turn_id"),
            "game_date": row.get("game_date"),
            "sequence": row.get("sequence"),
            "user_text": row.get("user_text"),
            "reply_text": row.get("reply_text"),
            "reply_blocks": copy.deepcopy(row.get("reply_blocks") or []),
            "reply_persona": persona,
            "reply_persona_label": persona_label,
            "understanding": copy.deepcopy(row.get("understanding") or {}),
            "fact_ids": list(row.get("fact_ids") or []),
            "proposed_events": copy.deepcopy(row.get("proposed_events") or []),
            "choices": copy.deepcopy(row.get("choices") or []),
            "renderer": row.get("renderer"),
            "mode": row.get("mode"),
            "committed_event_id": row.get("committed_event_id"),
            "committed_proposal_id": row.get("committed_proposal_id"),
            "committed_proposals": narrative_state.proposal_commits(row),
            "prop_change": copy.deepcopy(row.get("prop_change")) if row.get("prop_change") else None,
            "created_at": row.get("created_at"),
        }

    @staticmethod
    def _public_prop(row: dict) -> dict:
        rules = row.get("recurrence_rules") or {}
        return {
            "prop_id": row.get("prop_id"),
            "name": row.get("name"),
            "type": row.get("prop_type"),
            "state": row.get("state"),
            "visibility": row.get("visibility"),
            "emotional_roles": list(row.get("emotional_roles") or []),
            "participants": list(row.get("participants") or []),
            "origin_date": row.get("origin_date"),
            "last_used_date": row.get("last_used_date"),
            "callbacks": len(row.get("callbacks") or []),
            "payoff_state": row.get("payoff_state"),
            "pending_triggers": [trigger for trigger in rules.get("trigger_events") or [] if not trigger.get("fired")],
            "lifetime": memory_windows.public_policy(row.get("retrieval_lifetime")),
        }

    def append_story_event(
        self,
        payload: dict,
        snapshot: dict,
        *,
        source: str = "button",
        llm_text: str | None = None,
        model: str | None = None,
        spotlight: dict | None = None,
        realized: dict | None = None,
        save: bool = True,
    ) -> dict:
        game_date = story_engine.date_key(snapshot)
        self._seal_before(game_date)
        sessions = self.state["story_sessions"]
        session = sessions.get(game_date)
        if not isinstance(session, dict):
            session = story_engine.new_session(snapshot)
            sessions[game_date] = session
        selection = story_engine.normalize(payload, chat=source == "llm")
        sequence = int(session.get("sequence") or 0) + 1
        event = story_engine.build_event(
            snapshot,
            sequence,
            selection,
            source=source,
            llm_text=llm_text,
            model=model,
            spotlight=spotlight,
            realized=realized,
            universe_id=str(self.state.get("world_id") or ""),
            editorial_memory=self.state.get("community_memory"),
        )
        lifetime_games = selection.get("issue_lifetime_games")
        if lifetime_games is not None:
            memory_windows.attach_lifetime(
                self.state,
                event,
                lifetime_games,
                record_kind="story_event",
                record_id=event["id"],
                label=(event.get("scene") or {}).get("title") or "세계선 이슈",
                game_date=game_date,
                universe_id=str(self.state.get("world_id") or ""),
                protagonist_id=(snapshot.get("player") or {}).get("id"),
            )
        story_engine.append_event(session, event)
        editorial_engine.remember(self.state["community_memory"], event["reactions"])
        self._remember_day_snapshot(snapshot)
        if save:
            self.save()
        return event

    def current_session(self, snapshot: dict) -> dict | None:
        session = self.state.get("story_sessions", {}).get(story_engine.date_key(snapshot))
        return session if isinstance(session, dict) else None

    def day_context_view(self, snapshot: dict) -> dict:
        game_date = story_engine.date_key(snapshot)
        context = self.state.get("day_contexts", {}).get(game_date)
        return value_engine.build_view(context, snapshot)

    def upsert_day_context(self, payload: dict, snapshot: dict) -> dict:
        game_date = story_engine.date_key(snapshot)
        self._seal_before(game_date)
        contexts = self.state["day_contexts"]
        context = value_engine.merge_context(contexts.get(game_date), payload, snapshot)
        contexts[game_date] = context
        self._remember_day_snapshot(snapshot)
        self.save()
        return value_engine.build_view(context, snapshot)

    @staticmethod
    def _row_date(row: dict) -> str | None:
        try:
            return story_engine.date_key(row.get("date") or {})
        except (TypeError, ValueError):
            return None

    def history_index(self, snapshot: dict) -> list[dict]:
        current_date = story_engine.date_key(snapshot)
        dates = set(self.state.get("day_snapshots", {}))
        dates.update(self.state.get("story_sessions", {}))
        dates.update(self.state.get("day_contexts", {}))
        dates.update(
            row.get("game_date")
            for row in self.state.get("daily_archive", [])
            if isinstance(row, dict) and row.get("game_date")
        )
        dates.update(
            date
            for date in (self._row_date(row) for row in self.state.get("history", []))
            if date
        )
        rows = []
        for game_date in sorted(dates, reverse=True):
            session = self.state.get("story_sessions", {}).get(game_date) or {}
            context = self.state.get("day_contexts", {}).get(game_date) or {}
            archives = [
                row for row in self.state.get("daily_archive", []) if row.get("game_date") == game_date
            ]
            events = [row for row in self.state.get("history", []) if self._row_date(row) == game_date]
            turns = session.get("turns") or []
            event_headline = None
            if events:
                event_lines = events[-1].get("game_lines") or []
                event_headline = event_lines[0] if event_lines else None
            headline = (
                ((turns[-1].get("scene") or {}).get("title") if turns else None)
                or (archives[-1].get("headline") if archives else None)
                or event_headline
                or "날짜별 세계선 기록"
            )
            rows.append(
                {
                    "game_date": game_date,
                    "status": "sealed" if game_date < current_date else "open",
                    "headline": headline,
                    "story_turns": len(turns),
                    "feed_archives": len(archives),
                    "events": len(events),
                    "standings": len(context.get("standings") or []),
                    "top5_stats": len(context.get("leaderboards") or []),
                    "has_value_snapshot": bool(context.get("saber_inputs") or context.get("salary")),
                }
            )
        return rows

    def history_capsule(self, game_date: str, snapshot: dict) -> dict:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(game_date or "")):
            raise ValueError("역사 보관 날짜 형식이 올바르지 않습니다.")
        current_date = story_engine.date_key(snapshot)
        session = self.state.get("story_sessions", {}).get(game_date)
        context = self.state.get("day_contexts", {}).get(game_date)
        day = self.state.get("day_snapshots", {}).get(game_date)
        archives = [
            copy.deepcopy(row)
            for row in self.state.get("daily_archive", [])
            if row.get("game_date") == game_date
        ]
        events = [
            copy.deepcopy(row)
            for row in self.state.get("history", [])
            if self._row_date(row) == game_date
        ]
        if not any((session, context, day, archives, events)) and game_date != current_date:
            raise FileNotFoundError("해당 날짜의 역사 캡슐을 찾지 못했습니다.")
        frozen = (
            (day or {}).get("verified_snapshot")
            or (session or {}).get("verified_snapshot")
            or (context or {}).get("verified_snapshot")
            or (story_engine.snapshot_fact(snapshot) if game_date == current_date else None)
        )
        exact_timeline = []
        try:
            timeline = self.career_view(snapshot).get("timeline") or []
            exact_timeline = [
                copy.deepcopy(row) for row in timeline if row.get("occurred_on") == game_date
            ]
        except Exception:
            exact_timeline = []
        value_view = None
        if frozen:
            value_view = value_engine.build_view(context, frozen)
        historical_player = ((frozen or {}).get("player") or {}).get("id")
        if historical_player in (None, ""):
            historical_player = (snapshot.get("player") or {}).get("id")
        historical_context = personal_context.view(
            self.state,
            universe_id=str(self.state.get("world_id") or ""),
            protagonist_id=historical_player,
            game_date=game_date,
        )
        historical_counterparts = world_identity.counterparts(
            self.state,
            universe_id=str(self.state.get("world_id") or ""),
            protagonist_id=historical_player,
            game_date=game_date,
        )
        historical_memory = memory_windows.build_view(
            self.state,
            as_of_date=game_date,
            universe_id=str(self.state.get("world_id") or ""),
            protagonist_id=historical_player,
        )
        return {
            "schema_version": 1,
            "game_date": game_date,
            "status": "sealed" if game_date < current_date else "open",
            "editable": game_date == current_date,
            "verified_snapshot": copy.deepcopy(frozen),
            "verified_events": events,
            "milestones": exact_timeline,
            "story": story_engine.public_session(session),
            "reaction_archives": archives,
            "league_and_value": value_view,
            "personal_context": historical_context,
            "counterparts": historical_counterparts,
            "memory_views": historical_memory,
            "story_desk": story_desk.view(self.state, {"player": {"id": historical_player},
                "date": dict(zip(("year", "month", "day"), map(int, game_date.split("-"))))},
                {"world_id": str(self.state.get("world_id") or "")}, day=game_date, page_size=None, include_catalog=False),
            "chronicle": [{key: copy.deepcopy(value) for key, value in row.items() if key != "coverage"}
                          for row in chronicle.checkpoints(self.state, {"world_id": str(self.state.get("world_id") or ""),
                            "player_id": str(historical_player), "game_date": current_date}, "day", game_date)],
            "retrieved_at": _now(),
        }

    def career_view(self, snapshot: dict | None = None) -> dict:
        return career_engine.build_view(
            self.state, snapshot if snapshot is not None else self.state.get("last_snapshot")
        )

    def sync_save_history(self, snapshot: dict) -> dict:
        """Import verified prior seasons before classification and aggregation."""
        result = career_engine.sync_verified_history(self.state["career_profile"], snapshot)
        generated_exists = any(
            row.get("provenance") in career_engine._GENERATED_IMPORT_PROVENANCE
            for row in self.state.get("milestone_ledger", [])
            if isinstance(row, dict)
        )
        if result.get("changed") or not generated_exists:
            rebuilt = career_engine.rebuild_imported_milestones(
                self.state["career_profile"], self.state["milestone_ledger"], snapshot
            )
            milestones_changed = rebuilt != self.state["milestone_ledger"]
            self.state["milestone_ledger"] = rebuilt
            if result.get("changed") or milestones_changed:
                self.save()
        return result

    def sync_snapshot_parser_baseline(self, snapshot: dict) -> dict:
        """Upgrade a same-date legacy snapshot without inventing a correction event."""
        previous = self.state.get("last_snapshot")
        if not isinstance(previous, dict):
            return {"changed": False}
        previous_history = previous.get("career_history") or {}
        current_history = snapshot.get("career_history") or {}
        if previous_history or not str(current_history.get("status") or "").startswith("verified"):
            return {"changed": False}
        if _date_tuple(previous.get("date")) != _date_tuple(snapshot.get("date")):
            return {"changed": False}
        if (previous.get("player") or {}).get("id") != (snapshot.get("player") or {}).get("id"):
            return {"changed": False}

        # These fields were already published by the legacy parser and are not
        # affected by the newly selected complete season-summary source.
        stable_keys = tuple(key for key in COUNTING_STATS if key != "pit_H")
        previous_stats = previous.get("stats") or {}
        current_stats = snapshot.get("stats") or {}
        if any(previous_stats.get(key, 0) != current_stats.get(key, 0) for key in stable_keys):
            return {"changed": False}

        migration_id = f"season-summary:{snapshot.get('content_hash') or 'unknown'}"
        migrations = self.state.setdefault("parser_migrations", [])
        if not any(row.get("id") == migration_id for row in migrations if isinstance(row, dict)):
            migrations.append(
                {
                    "id": migration_id,
                    "from": "legacy-live-season-log",
                    "to": current_history.get("format"),
                    "game_date": story_engine.date_key(snapshot),
                    "old_content_hash": previous.get("content_hash"),
                    "new_content_hash": snapshot.get("content_hash"),
                    "corrected_fields": [
                        key for key in ("pit_H",) if previous_stats.get(key) != current_stats.get(key)
                    ],
                    "migrated_at": _now(),
                }
            )
        self.state["last_snapshot"] = copy.deepcopy(snapshot)
        game_date = story_engine.date_key(snapshot)
        day = self.state.get("day_snapshots", {}).get(game_date)
        if isinstance(day, dict) and day.get("status") != "sealed":
            day["verified_snapshot"] = story_engine.snapshot_fact(snapshot)
            day["updated_at"] = _now()
        self.save()
        return {
            "changed": True,
            "corrected_fields": [
                key for key in ("pit_H",) if previous_stats.get(key) != current_stats.get(key)
            ],
        }

    def upsert_career_season(self, payload: dict, snapshot: dict) -> dict:
        profile = self.state["career_profile"]
        item_id = str(payload.get("id") or "")
        existing = next(
            (row for row in profile.get("seasons", []) if row.get("id") == item_id),
            None,
        )
        current_year = int((snapshot.get("date") or {}).get("year") or 0) or None
        row = career_engine.normalize_season(
            payload,
            current_year=current_year,
            existing=existing,
        )
        career_engine.upsert_season(profile, row)
        self.state["milestone_ledger"] = career_engine.rebuild_imported_milestones(
            profile, self.state["milestone_ledger"], snapshot
        )
        self.save()
        return row

    def upsert_career_honor(self, payload: dict, snapshot: dict) -> dict:
        profile = self.state["career_profile"]
        item_id = str(payload.get("id") or "")
        existing = next(
            (row for row in profile.get("honors", []) if row.get("id") == item_id),
            None,
        )
        row = career_engine.normalize_honor(payload, existing=existing)
        career_engine.upsert_honor(profile, row)
        self.save()
        return row

    def remove_career_item(self, entity: str, item_id: str, snapshot: dict) -> bool:
        if not item_id:
            raise ValueError("삭제할 기록 ID가 없습니다.")
        profile = self.state["career_profile"]
        removed = career_engine.remove_profile_item(profile, entity, item_id)
        if not removed:
            raise ValueError("삭제할 기록을 찾지 못했습니다.")
        if entity == "season":
            self.state["milestone_ledger"] = career_engine.rebuild_imported_milestones(
                profile, self.state["milestone_ledger"], snapshot
            )
        self.save()
        return True
