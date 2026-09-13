#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finite issue lifetimes and deterministic layered memory projections.

The permanent world ledger remains the source of truth.  This module never
deletes an event or replaces history with a summary.  A lifetime only controls
automatic retrieval, and it advances exactly once for a newly committed,
verified game identity.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone


MIN_LIFETIME_GAMES = 1
MAX_LIFETIME_GAMES = 20
POLICY_VERSION = 1
VIEW_VERSION = 1

_LIFETIME_TEXT = re.compile(r"(?<!\d)(\d{1,2})\s*경기\s*(?:동안|간|까지|짜리)?")
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REMOTE_VISIBILITY = {"public", "social", "local", "national", "international"}
_ANCHOR_PROP_TYPES = {"promise", "bet", "tension", "failure_memory"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable(*parts: object, length: int = 20) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _date_from(value: object) -> str | None:
    if isinstance(value, str) and _DATE_TEXT.fullmatch(value):
        return value
    if isinstance(value, dict):
        try:
            return f"{int(value.get('year')):04d}-{int(value.get('month')):02d}-{int(value.get('day')):02d}"
        except (TypeError, ValueError):
            return None
    return None


def normalize_lifetime_games(value: object = None, *, text: object = None) -> int | None:
    """Return a validated 1..20 game budget, or ``None`` when not requested."""
    candidate = value
    if candidate in (None, ""):
        match = _LIFETIME_TEXT.search(str(text or ""))
        candidate = match.group(1) if match else None
    if candidate in (None, ""):
        return None
    if isinstance(candidate, bool):
        raise ValueError("이슈 수명은 1~20경기의 정수로 입력해 주세요.")
    try:
        games = int(str(candidate).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("이슈 수명은 1~20경기의 정수로 입력해 주세요.") from exc
    if games < MIN_LIFETIME_GAMES or games > MAX_LIFETIME_GAMES:
        raise ValueError("이슈 수명은 1~20경기 사이여야 합니다.")
    return games


def verified_game_id(
    universe_id: object,
    protagonist_id: object,
    game_date: object,
) -> str | None:
    date_text = _date_from(game_date)
    if not universe_id or protagonist_id in (None, "") or not date_text:
        return None
    # The game date is the correction/retry coalescing boundary.  Multiple
    # snapshots of one in-game date must never spend multiple lifetime units.
    return _stable("verified-game", universe_id, protagonist_id, date_text)


def verified_games(
    state: dict,
    *,
    as_of_date: str | None = None,
    universe_id: object = None,
    protagonist_id: object = None,
) -> list[dict]:
    """Project stable eligible games from already committed ledger evidence."""
    world = str(universe_id or state.get("world_id") or "")
    player = str(
        protagonist_id
        if protagonist_id not in (None, "")
        else ((state.get("player") or {}).get("id") or "")
    )
    rows: dict[str, dict] = {}
    for position, bundle in enumerate(state.get("reaction_instances") or []):
        if not isinstance(bundle, dict) or bundle.get("kind") != "save_delta":
            continue
        if bundle.get("retrospective_import") or bundle.get("eligible_game") is False:
            continue
        date_text = _date_from(bundle.get("game_date"))
        bundle_world = str(bundle.get("universe_id") or world)
        bundle_player = str(bundle.get("protagonist_id") or player)
        if not date_text or (as_of_date and date_text > as_of_date):
            continue
        if world and bundle_world != world:
            continue
        if player and bundle_player != player:
            continue
        game_id = verified_game_id(bundle_world, bundle_player, date_text)
        if not game_id:
            continue
        row = rows.setdefault(
            game_id,
            {
                "game_id": game_id,
                "game_date": date_text,
                "universe_id": bundle_world,
                "protagonist_id": bundle_player,
                "instance_ids": [],
                "event_ids": [],
                "source": "verified_save_delta",
                "position": position,
            },
        )
        if bundle.get("instance_id") and bundle["instance_id"] not in row["instance_ids"]:
            row["instance_ids"].append(bundle["instance_id"])
        for event_id in bundle.get("event_ids") or []:
            if event_id not in row["event_ids"]:
                row["event_ids"].append(event_id)

    # Ledgers created before the save-delta narrative bridge still have valid
    # NEW_GAME rows.  They are used for window boundaries, never retroactively
    # charged against an issue.
    for position, history in enumerate(state.get("history") or []):
        if not isinstance(history, dict) or history.get("kind") != "NEW_GAME":
            continue
        if history.get("baseline_only") or history.get("retrospective_import"):
            continue
        date_text = _date_from(history.get("date"))
        if not date_text or (as_of_date and date_text > as_of_date):
            continue
        game_id = verified_game_id(world, player, date_text)
        if not game_id:
            continue
        rows.setdefault(
            game_id,
            {
                "game_id": game_id,
                "game_date": date_text,
                "universe_id": world,
                "protagonist_id": player,
                "instance_ids": [],
                "event_ids": [],
                "source": "legacy_verified_history",
                "position": 1_000_000 + position,
            },
        )
    return sorted(rows.values(), key=lambda row: (row["game_date"], row["position"], row["game_id"]))


def latest_verified_game_id(
    state: dict,
    *,
    as_of_date: str | None = None,
    universe_id: object = None,
    protagonist_id: object = None,
) -> str | None:
    games = verified_games(
        state,
        as_of_date=as_of_date,
        universe_id=universe_id,
        protagonist_id=protagonist_id,
    )
    return games[-1]["game_id"] if games else None


def attach_lifetime(
    state: dict,
    record: dict,
    games: object,
    *,
    record_kind: str,
    record_id: object,
    label: object,
    game_date: str,
    universe_id: object,
    protagonist_id: object,
) -> dict | None:
    """Attach a finite retrieval policy without changing the underlying record."""
    budget = normalize_lifetime_games(games)
    if budget is None:
        return None
    identifier = str(record_id or "")
    if not identifier:
        raise ValueError("수명을 연결할 사건 식별자가 없습니다.")
    existing = record.get("retrieval_lifetime")
    if isinstance(existing, dict):
        current = public_policy(existing)
        if current and current["budget_games"] == budget:
            return existing
        raise ValueError("이미 수명이 정해진 이슈입니다. 기존 기록을 덮어쓰지 않습니다.")
    policy = {
        "schema_version": POLICY_VERSION,
        "unit": "eligible_committed_game",
        "budget_games": budget,
        "remaining_games": budget,
        "activation_after_game_id": latest_verified_game_id(
            state,
            as_of_date=game_date,
            universe_id=universe_id,
            protagonist_id=protagonist_id,
        ),
        "consumed_game_ids": [],
        "status": "active",
        "created_on": game_date,
        "created_at": _now(),
        "expired_on": None,
        "expired_by_game_id": None,
    }
    record["retrieval_lifetime"] = policy
    _audit(
        state,
        "created",
        record_kind=record_kind,
        record_id=identifier,
        label=str(label or "이슈"),
        game_date=game_date,
        policy=policy,
    )
    return policy


def public_policy(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        budget = int(value.get("budget_games"))
    except (TypeError, ValueError):
        return None
    if not MIN_LIFETIME_GAMES <= budget <= MAX_LIFETIME_GAMES:
        return None
    consumed = list(dict.fromkeys(str(row) for row in value.get("consumed_game_ids") or [] if row))[:budget]
    remaining = max(0, budget - len(consumed))
    status = "expired" if remaining == 0 or value.get("status") == "expired" else "active"
    return {
        "schema_version": POLICY_VERSION,
        "unit": "eligible_committed_game",
        "budget_games": budget,
        "remaining_games": remaining,
        "consumed_games": len(consumed),
        "status": status,
        "created_on": _date_from(value.get("created_on")),
        "expired_on": _date_from(value.get("expired_on")),
        "activation_after_game_id": value.get("activation_after_game_id"),
        "expired_by_game_id": value.get("expired_by_game_id"),
    }


def _policy_as_of(
    state: dict,
    *,
    record_kind: str,
    record_id: str,
    value: object,
    as_of_date: str,
) -> dict | None:
    """Project a policy at a historical date without leaking later games."""
    view = public_policy(value)
    if not view or (view.get("created_on") and view["created_on"] > as_of_date):
        return None
    audit_rows = [
        row
        for row in state.get("narrative_lifetime_events") or []
        if isinstance(row, dict)
        and row.get("record_kind") == record_kind
        and str(row.get("record_id") or "") == record_id
    ]
    if not audit_rows:
        # Compatibility fallback for a hand-authored or pre-audit policy.
        return view
    consumed_ids = {
        str(row.get("game_id"))
        for row in audit_rows
        if row.get("action") in {"consumed", "expired"}
        and row.get("game_id")
        and (_date_from(row.get("game_date")) or "9999-99-99") <= as_of_date
    }
    remaining = max(0, view["budget_games"] - len(consumed_ids))
    expired_rows = [
        row
        for row in audit_rows
        if row.get("action") == "expired"
        and (_date_from(row.get("game_date")) or "9999-99-99") <= as_of_date
    ]
    view.update(
        {
            "remaining_games": remaining,
            "consumed_games": len(consumed_ids),
            "status": "expired" if remaining == 0 else "active",
            "expired_on": min(
                (_date_from(row.get("game_date")) for row in expired_rows),
                default=None,
            ),
        }
    )
    return view


def is_automatic_retrieval_active(record: object) -> bool:
    """Legacy rows without a lifetime keep their historical behavior."""
    if not isinstance(record, dict) or not isinstance(record.get("retrieval_lifetime"), dict):
        return True
    policy = public_policy(record.get("retrieval_lifetime"))
    return bool(policy and policy["status"] == "active" and policy["remaining_games"] > 0)


def _lifetime_records(state: dict):
    for prop_id, prop in (state.get("narrative_props") or {}).items():
        if isinstance(prop, dict) and isinstance(prop.get("retrieval_lifetime"), dict):
            yield "narrative_prop", str(prop_id), str(prop.get("name") or "서사 소재"), prop
    for game_date, session in (state.get("story_sessions") or {}).items():
        if not isinstance(session, dict):
            continue
        for turn in session.get("turns") or []:
            if isinstance(turn, dict) and isinstance(turn.get("retrieval_lifetime"), dict):
                label = str((turn.get("scene") or {}).get("title") or "세계선 이슈")
                yield "story_event", str(turn.get("id") or f"{game_date}:{turn.get('sequence')}"), label, turn


def _audit(
    state: dict,
    action: str,
    *,
    record_kind: str,
    record_id: str,
    label: str,
    game_date: str,
    policy: dict,
    game_id: str | None = None,
) -> None:
    rows = state.get("narrative_lifetime_events")
    if not isinstance(rows, list):
        rows = []
        state["narrative_lifetime_events"] = rows
    audit_id = _stable("lifetime", action, record_kind, record_id, game_id or game_date)
    if any(isinstance(row, dict) and row.get("audit_id") == audit_id for row in rows):
        return
    view = public_policy(policy) or {}
    rows.append(
        {
            "audit_id": audit_id,
            "action": action,
            "record_kind": record_kind,
            "record_id": record_id,
            "label": label,
            "game_date": game_date,
            "game_id": game_id,
            "remaining_games": view.get("remaining_games"),
            "budget_games": view.get("budget_games"),
            "at": _now(),
        }
    )


def consume_verified_game(state: dict, bundle: object) -> list[dict]:
    """Spend one unit on every active issue for one newly committed game.

    The caller invokes this only inside the successful save-delta commit gate.
    The function is nevertheless idempotent for retries and same-date
    corrections.
    """
    if not isinstance(bundle, dict) or bundle.get("kind") != "save_delta":
        return []
    if bundle.get("retrospective_import") or bundle.get("eligible_game") is False:
        return []
    world = str(bundle.get("universe_id") or state.get("world_id") or "")
    player = str(bundle.get("protagonist_id") or ((state.get("player") or {}).get("id") or ""))
    game_date = _date_from(bundle.get("game_date"))
    game_id = verified_game_id(world, player, game_date)
    if not game_id or not game_date:
        return []
    changes = []
    for record_kind, record_id, label, record in _lifetime_records(state):
        if str(record.get("universe_id") or world) != world:
            continue
        if str(record.get("protagonist_id") or player) != player:
            continue
        policy = record.get("retrieval_lifetime")
        view = public_policy(policy)
        if not view or view["status"] != "active":
            continue
        if view.get("created_on") and view["created_on"] > game_date:
            continue
        if policy.get("activation_after_game_id") == game_id:
            continue
        consumed = policy.get("consumed_game_ids")
        if not isinstance(consumed, list):
            consumed = []
            policy["consumed_game_ids"] = consumed
        if game_id in consumed:
            continue
        consumed.append(game_id)
        remaining = max(0, int(policy["budget_games"]) - len(set(consumed)))
        policy["remaining_games"] = remaining
        policy["last_consumed_on"] = game_date
        policy["last_consumed_game_id"] = game_id
        action = "consumed"
        if remaining == 0:
            policy["status"] = "expired"
            policy["expired_on"] = game_date
            policy["expired_by_game_id"] = game_id
            action = "expired"
        _audit(
            state,
            action,
            record_kind=record_kind,
            record_id=record_id,
            label=label,
            game_date=game_date,
            game_id=game_id,
            policy=policy,
        )
        changes.append(
            {
                "record_kind": record_kind,
                "record_id": record_id,
                "label": label,
                "game_id": game_id,
                "game_date": game_date,
                "remaining_games": remaining,
                "status": policy["status"],
            }
        )
    return changes


def _visible(row: dict, *, remote: bool) -> bool:
    if not remote:
        return True
    visibility = str(row.get("visibility") or "private")
    return visibility in _REMOTE_VISIBILITY


def _entry(
    memory_id: str,
    kind: str,
    label: object,
    game_date: object,
    *,
    source_ref: str,
    detail: object = "",
    salience: float = 0.5,
    pinned: bool = False,
    exact: bool = False,
    visibility: str = "private",
    lifetime: object = None,
    evidence_class: str = "derived_analysis",
) -> dict | None:
    date_text = _date_from(game_date)
    if not memory_id or not date_text or not str(label or "").strip():
        return None
    return {
        "memory_id": memory_id,
        "kind": kind,
        "label": str(label).strip(),
        "detail": str(detail or "").strip()[:360],
        "game_date": date_text,
        "source_ref": source_ref,
        "salience": max(0.0, min(1.0, float(salience or 0.0))),
        "pinned": bool(pinned),
        "exact": bool(exact),
        "visibility": visibility,
        "lifetime": public_policy(lifetime),
        "evidence_class": evidence_class,
    }


def _collect_memories(
    state: dict,
    *,
    as_of_date: str,
    universe_id: str,
    protagonist_id: str,
) -> list[dict]:
    rows: dict[str, dict] = {}

    def add(row: dict | None) -> None:
        if row and row["game_date"] <= as_of_date:
            current = rows.get(row["memory_id"])
            if current is None or (row["exact"], row["salience"], len(row["detail"])) > (
                current["exact"], current["salience"], len(current["detail"])
            ):
                rows[row["memory_id"]] = row

    for game_date, session in (state.get("story_sessions") or {}).items():
        if not isinstance(session, dict) or str(game_date) > as_of_date:
            continue
        for turn in session.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            if turn.get("renderer") == "story_desk":
                # Desk recall owns its independent external-transmission consent.
                # The mirrored event exists only for historical display.
                continue
            scene = turn.get("scene") or {}
            attention = scene.get("attention") or {}
            score = attention.get("score")
            salience = min(1.0, float(score) / 100.0) if isinstance(score, (int, float)) else 0.6
            add(
                _entry(
                    str(turn.get("id") or ""),
                    "story",
                    scene.get("title") or "세계선 장면",
                    turn.get("game_date") or game_date,
                    source_ref=f"story:{turn.get('id')}",
                    detail=scene.get("summary") or scene.get("response"),
                    salience=salience,
                    visibility=str((turn.get("input") or {}).get("visibility") or "private"),
                    lifetime=turn.get("retrieval_lifetime"),
                    evidence_class=(
                        "generated_fiction"
                        if turn.get("provenance") == "llm_generated_fiction"
                        else "fictional_intervention"
                    ),
                )
            )

    for turn in state.get("conversation_turns") or []:
        if not isinstance(turn, dict):
            continue
        committed = bool(turn.get("committed_event_id") or turn.get("committed_proposals"))
        add(
            _entry(
                f"conversation:{turn.get('turn_id')}",
                "conversation",
                str(turn.get("user_text") or "대화")[:100],
                turn.get("game_date"),
                source_ref=f"conversation:{turn.get('turn_id')}",
                detail=turn.get("reply_text"),
                salience=0.58 if committed else 0.35,
                exact=committed,
                visibility=str((turn.get("understanding") or {}).get("visibility") or "private"),
                evidence_class="fictional_intervention",
            )
        )

    for event in state.get("world_events") or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("universe_id") or universe_id) != universe_id:
            continue
        if str(event.get("protagonist_id") or protagonist_id) != protagonist_id:
            continue
        payload = event.get("payload") or {}
        add(
            _entry(
                str(event.get("event_id") or ""),
                "world_event",
                payload.get("label") or event.get("event_type"),
                event.get("game_date"),
                source_ref=f"event:{event.get('event_id')}",
                detail=payload.get("note") or payload.get("statement") or "",
                salience=float(event.get("salience") or 0.5),
                exact=event.get("evidence_class") in {"save_verified", "user_confirmed"},
                visibility=str(event.get("visibility") or "private"),
                evidence_class=str(event.get("evidence_class") or "derived_analysis"),
            )
        )

    games_by_date = {row["game_date"]: row for row in verified_games(state, as_of_date=as_of_date, universe_id=universe_id, protagonist_id=protagonist_id)}
    for history in state.get("history") or []:
        if not isinstance(history, dict):
            continue
        date_text = _date_from(history.get("date"))
        if not date_text or date_text > as_of_date:
            continue
        labels = list(history.get("game_lines") or []) + [str(row[1]) for row in history.get("milestones") or [] if isinstance(row, (list, tuple)) and len(row) > 1]
        label = labels[0] if labels else {"NEW_GAME": "검증된 경기 기록", "REST_DAY": "휴식일", "CORRECTION": "기록 정정"}.get(history.get("kind"), str(history.get("kind") or "기록 변화"))
        game = games_by_date.get(date_text)
        memory_id = f"game:{(game or {}).get('game_id') or history.get('source_hash') or _stable(date_text, label)}"
        add(
            _entry(
                memory_id,
                "verified_game",
                label,
                date_text,
                source_ref=memory_id,
                detail=" · ".join(labels[1:4]),
                salience=0.82 if history.get("milestones") else 0.65,
                exact=True,
                visibility="public",
                evidence_class="save_verified",
            )
        )

    for archive in state.get("daily_archive") or []:
        if not isinstance(archive, dict):
            continue
        add(
            _entry(
                f"archive:{archive.get('id')}",
                "publication",
                archive.get("headline") or "기사·커뮤니티 반응",
                archive.get("game_date"),
                source_ref=f"archive:{archive.get('id')}",
                detail=f"기사 {archive.get('article_count', 0)} · 게시판 {archive.get('board_count', 0)}",
                salience=0.55,
                exact=True,
                visibility="public",
                evidence_class="generated_fiction",
            )
        )

    for prop_id, prop in (state.get("narrative_props") or {}).items():
        if not isinstance(prop, dict):
            continue
        if str(prop.get("universe_id") or universe_id) != universe_id or str(prop.get("protagonist_id") or protagonist_id) != protagonist_id:
            continue
        add(
            _entry(
                f"prop:{prop_id}",
                "narrative_prop",
                prop.get("name") or "서사 소재",
                prop.get("origin_date"),
                source_ref=f"prop:{prop_id}",
                detail=f"{prop.get('prop_type') or 'motif'} · {prop.get('state') or 'seed'}",
                salience=0.78 if prop.get("prop_type") in _ANCHOR_PROP_TYPES else 0.56,
                pinned=prop.get("prop_type") in _ANCHOR_PROP_TYPES,
                exact=prop.get("truth_status") in {"verified", "user_confirmed"},
                visibility=str(prop.get("visibility") or "private"),
                lifetime=prop.get("retrieval_lifetime"),
                evidence_class=(
                    "user_confirmed"
                    if prop.get("truth_status") in {"verified", "user_confirmed"}
                    else "fictional_intervention"
                ),
            )
        )
    for index, event in enumerate(state.get("narrative_prop_events") or []):
        if not isinstance(event, dict):
            continue
        prop = (state.get("narrative_props") or {}).get(str(event.get("prop_id"))) or {}
        add(
            _entry(
                f"prop-event:{event.get('prop_id')}:{index}",
                "prop_beat",
                f"{prop.get('name') or '소재'} · {event.get('kind') or '변화'}",
                event.get("game_date"),
                source_ref=f"prop:{event.get('prop_id')}",
                detail=event.get("note"),
                salience=0.62,
                pinned=prop.get("prop_type") in _ANCHOR_PROP_TYPES and event.get("kind") in {"created", "retired"},
                visibility=str(prop.get("visibility") or "private"),
                lifetime=prop.get("retrieval_lifetime"),
                evidence_class="fictional_intervention",
            )
        )

    profile = state.get("career_profile") or {}
    for honor in profile.get("honors") or []:
        if not isinstance(honor, dict):
            continue
        date_text = _date_from(honor.get("occurred_on"))
        if not date_text:
            year = honor.get("season_year")
            date_text = f"{int(year):04d}-12-31" if str(year or "").isdigit() else None
        add(
            _entry(
                f"honor:{honor.get('id') or _stable(honor)}",
                "honor",
                honor.get("title") or honor.get("kind") or "수상·기념 기록",
                date_text,
                source_ref=f"honor:{honor.get('id')}",
                detail=honor.get("note") or honor.get("team") or "",
                salience=0.98,
                pinned=True,
                exact=True,
                visibility="public",
                evidence_class="save_verified",
            )
        )
    for milestone in state.get("milestone_ledger") or []:
        if not isinstance(milestone, dict):
            continue
        date_text = _date_from(milestone.get("occurred_on") or milestone.get("game_date") or milestone.get("date"))
        add(
            _entry(
                f"milestone:{milestone.get('id') or _stable(milestone)}",
                "milestone",
                milestone.get("title") or milestone.get("label") or milestone.get("description") or "마일스톤",
                date_text,
                source_ref=f"milestone:{milestone.get('id')}",
                detail=milestone.get("note") or milestone.get("scope") or "",
                salience=0.96,
                pinned=True,
                exact=True,
                visibility="public",
                evidence_class="save_verified",
            )
        )

    pinned_ids = {
        str(pin.get("memory_id") or pin.get("event_id") or pin.get("source_ref"))
        for pin in state.get("memory_pins") or []
        if isinstance(pin, dict) and (_date_from(pin.get("game_date") or pin.get("pinned_on")) or "0000-00-00") <= as_of_date
    }
    for row in rows.values():
        if row["memory_id"] in pinned_ids or row["source_ref"] in pinned_ids:
            row["pinned"] = True
    return sorted(rows.values(), key=lambda row: (row["game_date"], row["salience"], row["memory_id"]), reverse=True)


def _issue_views(state: dict, *, as_of_date: str, remote: bool = False) -> list[dict]:
    rows = []
    for record_kind, record_id, label, record in _lifetime_records(state):
        policy = _policy_as_of(
            state,
            record_kind=record_kind,
            record_id=record_id,
            value=record.get("retrieval_lifetime"),
            as_of_date=as_of_date,
        )
        if not policy:
            continue
        visibility = str(record.get("visibility") or (record.get("input") or {}).get("visibility") or "private")
        row = {
            "record_kind": record_kind,
            "record_id": record_id,
            "source_ref": f"{'story' if record_kind == 'story_event' else 'prop'}:{record_id}",
            "label": label,
            "visibility": visibility,
            **policy,
        }
        if _visible(row, remote=remote):
            rows.append(row)
    return sorted(rows, key=lambda row: (row["status"] != "active", row.get("created_on") or "", row["record_id"]), reverse=False)


def build_view(
    state: dict,
    *,
    as_of_date: str,
    universe_id: object,
    protagonist_id: object,
    remote: bool = False,
) -> dict:
    """Build recent-3, games-4..10 and season views without mutation."""
    if not _DATE_TEXT.fullmatch(str(as_of_date or "")):
        raise ValueError("기억 기준 날짜 형식이 올바르지 않습니다.")
    world = str(universe_id or state.get("world_id") or "")
    player = str(protagonist_id or ((state.get("player") or {}).get("id") or ""))
    games = verified_games(state, as_of_date=as_of_date, universe_id=world, protagonist_id=player)
    memories = [row for row in _collect_memories(state, as_of_date=as_of_date, universe_id=world, protagonist_id=player) if _visible(row, remote=remote)]
    game_dates = [row["game_date"] for row in games]

    def age_for(date_text: str) -> int:
        return sum(1 for game_date in game_dates if game_date > date_text)

    season = as_of_date[:4]
    definitions = (
        ("short_recent_3", "최근 3경기", lambda row: age_for(row["game_date"]) <= 2),
        ("medium_recent_4_10", "4~10경기 전", lambda row: 3 <= age_for(row["game_date"]) <= 9),
        ("long_season", "이번 시즌", lambda row: row["game_date"].startswith(season)),
    )
    windows = []
    for window_id, label, predicate in definitions:
        selected = [row for row in memories if predicate(row)]
        limit = 30 if window_id == "long_season" else 18
        windows.append(
            {
                "id": window_id,
                "label": label,
                "total": len(selected),
                "items": copy.deepcopy(selected[:limit]),
                "truncated": len(selected) > limit,
            }
        )
    anchors = [
        row
        for row in memories
        if row.get("pinned")
        or (row.get("exact") and row.get("kind") in {"honor", "milestone"})
        or row.get("salience", 0) >= 0.9
    ]
    return {
        "schema_version": VIEW_VERSION,
        "as_of_date": as_of_date,
        "universe_id": world,
        "protagonist_id": player,
        "eligible_game_count": len(games),
        "windows": windows,
        "anchors": copy.deepcopy(anchors[:30]),
        "issues": _issue_views(state, as_of_date=as_of_date, remote=remote),
        "storage_policy": "derived_projection_over_permanent_ledger",
        "expiry_policy": "automatic_retrieval_only; source history is never deleted",
    }


def recall(view: dict, query: object, *, limit: int = 4) -> list[dict]:
    words = {token.lower() for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", str(query or ""))}
    candidates = []
    for window in view.get("windows") or []:
        for row in window.get("items") or []:
            haystack = f"{row.get('label', '')} {row.get('detail', '')}".lower()
            overlap = sum(1 for word in words if word in haystack)
            if overlap:
                candidates.append((overlap, row.get("salience", 0), row))
    unique = {}
    for overlap, salience, row in sorted(candidates, key=lambda value: (value[0], value[1], value[2].get("game_date", "")), reverse=True):
        unique.setdefault(row["memory_id"], row)
    return [copy.deepcopy(row) for row in list(unique.values())[: max(0, limit)]]


def _prompt_record(record_type: str, row: dict) -> dict:
    if record_type == "issue":
        return {
            "record_type": "issue",
            "source_ref": row.get("source_ref"),
            "label": row.get("label"),
            "status": row.get("status"),
            "remaining_games": row.get("remaining_games"),
            "budget_games": row.get("budget_games"),
        }
    return {
        "record_type": "memory",
        "memory_id": row.get("memory_id"),
        "source_ref": row.get("source_ref"),
        "game_date": row.get("game_date"),
        "label": row.get("label"),
        "evidence_class": row.get("evidence_class"),
    }


def _packet_hash(records: list[dict]) -> str:
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prompt_packet(view: dict, *, query: object = "", limit: int = 8) -> dict:
    """Return bounded prompt text plus stable references to exactly what it used."""
    lines = []
    records = []
    references = []
    active = [row for row in view.get("issues") or [] if row.get("status") == "active"]
    for row in active[:4]:
        lines.append(f"진행 이슈: {row['label']} (자동 재등장 {row['remaining_games']}경기 남음, 출처 {row['source_ref']})")
        records.append(_prompt_record("issue", row))
        references.append({"record_type": "issue", "source_ref": row.get("source_ref")})
    recalled = recall(view, query, limit=max(0, limit - len(lines)))
    if not recalled:
        recent = next((row for row in view.get("windows") or [] if row.get("id") == "short_recent_3"), {})
        # An expired finite issue remains directly searchable, but it must not
        # return through the automatic recent-memory fallback.
        recalled = [
            row
            for row in recent.get("items") or []
            if not row.get("lifetime") or row["lifetime"].get("status") == "active"
        ][: max(0, limit - len(lines))]
    for row in recalled:
        lines.append(f"{row['game_date']} 기억: {row['label']} (출처 {row['source_ref']})")
        records.append(_prompt_record("memory", row))
        references.append({"record_type": "memory", "memory_id": row.get("memory_id")})
    return {
        "text": "\n".join(lines[:limit]),
        "references": references[:limit],
        "fingerprint": _packet_hash(records[:limit]),
    }


def prompt_section(view: dict, *, query: object = "", limit: int = 8) -> str:
    """Return bounded approved memory data; callers decide local/remote scope."""
    return prompt_packet(view, query=query, limit=limit)["text"]


def reference_fingerprint(
    state: dict,
    *,
    as_of_date: str,
    universe_id: object,
    protagonist_id: object,
    references: object,
    remote: bool = False,
) -> str:
    """Re-hash only the prompt records originally selected.

    An unrelated same-day append does not stale a slow model response. A
    changed or missing referenced record does, while save/player/date changes
    remain covered by the service origin guard.
    """
    world = str(universe_id or state.get("world_id") or "")
    player = str(protagonist_id or ((state.get("player") or {}).get("id") or ""))
    memories = {
        row["memory_id"]: row
        for row in _collect_memories(
            state,
            as_of_date=as_of_date,
            universe_id=world,
            protagonist_id=player,
        )
        if _visible(row, remote=remote)
    }
    issues = {
        row["source_ref"]: row
        for row in _issue_views(state, as_of_date=as_of_date, remote=remote)
    }
    records = []
    for reference in references if isinstance(references, list) else []:
        if not isinstance(reference, dict):
            records.append({"record_type": "invalid_reference"})
            continue
        if reference.get("record_type") == "issue":
            row = issues.get(reference.get("source_ref"))
            records.append(_prompt_record("issue", row) if row else {"record_type": "missing_issue", "source_ref": reference.get("source_ref")})
        else:
            row = memories.get(reference.get("memory_id"))
            records.append(_prompt_record("memory", row) if row else {"record_type": "missing_memory", "memory_id": reference.get("memory_id")})
    return _packet_hash(records)
