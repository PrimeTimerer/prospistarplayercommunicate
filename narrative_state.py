#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger v5 narrative-state mutations (conversation turns, threads, edges, memory).

All functions mutate a ledger ``state`` dict in place and return the row they
added or changed. They never save; the caller (``Ledger``) owns persistence.
Every mutation is append-only or event-sourced so explanations remain
inspectable (master plan 6.2, 7.2, 8.10, 13.2).
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone

import narrative_contracts as nc
import memory_windows

MAX_SIGNATURES = 200


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable(*parts, length: int = 16) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:length]


def _list(state: dict, key: str) -> list:
    value = state.get(key)
    if not isinstance(value, list):
        value = []
        state[key] = value
    return value


def _dict(state: dict, key: str) -> dict:
    value = state.get(key)
    if not isinstance(value, dict):
        value = {}
        state[key] = value
    return value


# --------------------------------------------------------------------------
# Conversation turns
# --------------------------------------------------------------------------


def append_conversation_turn(state: dict, *, game_date: str, user_text: str, response: dict, universe_id: str, protagonist_id: object) -> dict:
    turns = _list(state, "conversation_turns")
    sequence = sum(1 for row in turns if row.get("game_date") == game_date) + 1
    turn = {
        "turn_id": _stable(universe_id, protagonist_id, game_date, sequence, user_text, response.get("response_id")),
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "game_date": game_date,
        "sequence": sequence,
        "user_text": user_text,
        "understanding": copy.deepcopy(response.get("understanding") or {}),
        "reply_text": str((response.get("reply") or {}).get("text") or ""),
        "reply_blocks": copy.deepcopy((response.get("reply") or {}).get("blocks") or []),
        "reply_persona": (response.get("reply") or {}).get("persona"),
        "proposed_events": copy.deepcopy(response.get("proposed_events") or []),
        "choices": copy.deepcopy(response.get("choices") or []),
        "renderer": response.get("renderer"),
        "provenance": copy.deepcopy(response.get("provenance") or {}),
        "audit": copy.deepcopy(response.get("audit") or {}),
        "fact_ids": list(response.get("fact_ids") or []),
        "mode": response.get("mode"),
        "committed_event_id": None,
        "committed_proposal_id": None,
        "committed_proposals": {},
        "conversation_state": copy.deepcopy(response.get("conversation_state") or {}),
        "prop_change": copy.deepcopy(response.get("prop_change")) if response.get("prop_change") else None,
        "created_at": _now(),
    }
    turns.append(turn)
    return turn


def find_turn(state: dict, turn_id: str) -> dict | None:
    for row in _list(state, "conversation_turns"):
        if row.get("turn_id") == turn_id:
            return row
    return None


def conversation_state(state: dict, game_date: str) -> dict:
    rows = [row for row in _list(state, "conversation_turns") if row.get("game_date") == game_date]
    if not rows:
        return {}
    last = rows[-1]
    value = dict(last.get("conversation_state") or {})
    value["last_turn_id"] = last.get("turn_id")
    threads = [row for row in _dict(state, "narrative_threads").values() if isinstance(row, dict) and row.get("state") not in ("resolved", "remembered")]
    value["active_threads"] = [{"thread_id": row.get("thread_id"), "label": row.get("label"), "participants": row.get("participants")} for row in threads[:8]]
    props = [
        row
        for row in _dict(state, "narrative_props").values()
        if isinstance(row, dict)
        and row.get("state") != "retired"
        and memory_windows.is_automatic_retrieval_active(row)
    ]
    if props:
        value["last_prop_id"] = sorted(props, key=lambda row: str(row.get("last_used_date") or ""))[-1].get("prop_id")
    return value


def proposal_commits(turn: dict) -> dict:
    """Read the additive map and the legacy single pair without rewriting it."""
    commits = dict(turn.get("committed_proposals") or {})
    if turn.get("committed_event_id") and turn.get("committed_proposal_id"):
        commits.setdefault(turn["committed_proposal_id"], turn["committed_event_id"])
    return commits


def proposal_is_committed(turn: dict, proposal_id: str) -> bool:
    commits = proposal_commits(turn)
    # An unidentified legacy commit cannot safely be replayed.
    return bool(commits.get(proposal_id) or (turn.get("committed_event_id") and not commits))


def mark_proposal_committed(state: dict, turn_id: str, proposal_id: str, event_id: str) -> dict | None:
    turn = find_turn(state, turn_id)
    if turn is None:
        return None
    commits = proposal_commits(turn)
    commits[proposal_id] = event_id
    turn["committed_proposals"] = commits
    if not turn.get("committed_event_id"):
        turn["committed_event_id"] = event_id
        turn["committed_proposal_id"] = proposal_id
    pending = any(row.get("proposal_id") not in commits for row in turn.get("proposed_events") or [])
    turn["mode"] = "일부 기록 확정" if pending else "오늘의 기록으로 확정"
    return turn


# --------------------------------------------------------------------------
# Anti-collapse memory
# --------------------------------------------------------------------------


def remember_signature(state: dict, signature: dict | None) -> None:
    if not signature:
        return
    memory = _dict(state, "narrative_memory")
    rows = memory.get("signatures")
    if not isinstance(rows, list):
        rows = []
    rows.append(dict(signature, at=_now()))
    memory["signatures"] = rows[-MAX_SIGNATURES:]
    openings = memory.get("recent_openings")
    if not isinstance(openings, list):
        openings = []
    if signature.get("opening"):
        openings.append(signature["opening"])
    memory["recent_openings"] = openings[-60:]
    outlines = memory.get("recent_outlines")
    if not isinstance(outlines, list):
        outlines = []
    if signature.get("outline"):
        outlines.append(signature["outline"])
    memory["recent_outlines"] = outlines[-60:]


# --------------------------------------------------------------------------
# World events, facts, entities
# --------------------------------------------------------------------------


def record_world_event(state: dict, event: dict) -> dict:
    problems = nc.validate_event(event)
    if problems:
        raise ValueError("invalid world event: " + "; ".join(problems))
    _list(state, "world_events").append(copy.deepcopy(event))
    return event


def register_fact(state: dict, fact: dict) -> dict:
    facts = _dict(state, "fact_registry")
    facts.setdefault(str(fact["fact_id"]), copy.deepcopy(fact))
    return facts[str(fact["fact_id"])]


def register_entity(state: dict, entity_id: str, *, kind: str, label: str, universe_id: str) -> dict:
    entities = _dict(state, "world_entities")
    row = entities.get(entity_id)
    if row is None:
        row = {"entity_id": entity_id, "kind": kind, "label": label, "universe_id": str(universe_id), "aliases": [], "created_at": _now()}
        entities[entity_id] = row
    return row


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------


def ensure_thread(state: dict, *, thread_type: str, label: str, universe_id: str, protagonist_id: object, game_date: str, participants=(), opened_by_event: str | None = None, stakes: str = "", scope_key: str | None = None) -> tuple[dict, bool]:
    threads = _dict(state, "narrative_threads")
    for row in threads.values():
        if isinstance(row, dict) and row.get("scope_key") == scope_key and row.get("thread_type") == thread_type and row.get("label") == label and row.get("state") not in ("resolved", "remembered"):
            return row, False
    thread = nc.new_thread(thread_type=thread_type, universe_id=universe_id, protagonist_id=protagonist_id, label=label, game_date=game_date, participants=participants, stakes=stakes, opened_by_event=opened_by_event,
                           thread_id=nc.stable_id(universe_id, protagonist_id, "interaction", scope_key) if scope_key else None)
    if scope_key:
        thread["scope_key"] = scope_key
    threads[thread["thread_id"]] = thread
    _list(state, "thread_events").append({"thread_id": thread["thread_id"], "from_state": None, "to_state": "seeded", "game_date": game_date, "event_id": opened_by_event, "authorized_intervention": True, "note": "opened", "at": _now()})
    return thread, True


def advance_thread(state: dict, thread_id: str, new_state: str, *, game_date: str, event_id: str | None = None, authorized: bool = False, note: str = "") -> dict:
    thread = _dict(state, "narrative_threads").get(thread_id)
    if thread is None:
        raise KeyError(f"unknown thread {thread_id}")
    row = nc.transition_thread(thread, new_state, game_date=game_date, evidence_event_id=event_id, authorized_intervention=authorized, note=note)
    _list(state, "thread_events").append(row)
    return row


def touch_thread_for_event(state: dict, thread: dict, *, game_date: str, event_id: str) -> dict | None:
    """Move a thread one natural step forward when a related event is committed."""
    current = thread.get("state")
    next_state = {"seeded": "noticed", "noticed": "developing", "developing": "pressure", "dormant": "resurfaced", "resurfaced": "developing"}.get(str(current))
    if not next_state:
        thread["last_event_on"] = game_date
        return None
    return advance_thread(state, thread["thread_id"], next_state, game_date=game_date, event_id=event_id, authorized=True, note="advanced by committed intervention")


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------


def apply_relationship(state: dict, *, target: str, deltas: dict, game_date: str, event_id: str | None, explanation: str, memory_id: str | None = None) -> dict:
    edges = _dict(state, "relationship_edges")
    key = nc.edge_key("protagonist", target)
    edge = edges.get(key)
    if edge is None:
        edge = nc.new_edge("protagonist", target)
        edges[key] = edge
    row = nc.apply_edge_delta(edge, deltas, game_date=game_date, event_id=event_id, explanation=explanation, memory_id=memory_id)
    _list(state, "relationship_events").append(row)
    return row


ACT_RELATIONSHIP_DELTAS = {
    "vent": {"tension": 0.05, "familiarity": 0.05},
    "console": {"warmth": 0.15, "trust": 0.1, "familiarity": 0.1},
    "praise": {"respect": 0.15, "warmth": 0.1, "public_alignment": 0.1},
    "criticize_self": {"respect": 0.05, "public_alignment": 0.05},
    "declare": {"rivalry": 0.1, "public_alignment": 0.05},
    "apologize": {"trust": 0.15, "tension": -0.2, "unresolved_debt": -0.2},
    "request_private_meeting": {"trust": 0.1, "familiarity": 0.15, "tension": -0.05},
    "give_public_quote": {"public_alignment": 0.1},
}


def relationship_deltas_for(act: str | None, target: str | None) -> dict:
    if not act or not target or target == "self":
        return {}
    return dict(ACT_RELATIONSHIP_DELTAS.get(act, {}))
