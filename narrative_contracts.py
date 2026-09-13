#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger v5 narrative contracts (master plan sections 3.2, 4.2, 5.1, 6.2, 7, 8.5.1, 8.11).

Pure data contracts: evidence classes, protagonist relations, the common
event envelope, fact records, the story-thread state machine, relationship
edges with event-sourced explanations, narrative props with their beat
machine, and structured narrative blocks with a plain-text projection and a
safe legacy paragraphizer. No I/O, no randomness, no game access.
"""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from datetime import datetime, timezone

CONTRACT_VERSION = "5.0.0"

# --------------------------------------------------------------------------
# Evidence classes (3.2)
# --------------------------------------------------------------------------

EVIDENCE_CLASSES = (
    "save_verified",
    "user_confirmed",
    "external_verified",
    "derived_analysis",
    "fictional_intervention",
    "generated_fiction",
    "unknown",
)

# Existing provenance labels keep their original strings in storage; this map
# resolves them to the v5 evidence class for planning and display rules.
LEGACY_PROVENANCE_MAP = {
    "save_verified": "save_verified",
    "save_verified_completed_season": "save_verified",
    "save_verified_historical_season": "save_verified",
    "save_verified_historical_import": "save_verified",
    "save_verified_historical_milestone": "save_verified",
    "save_derived": "derived_analysis",
    "derived_save_delta": "derived_analysis",
    "stored_same_day_save_delta": "derived_analysis",
    "derived_reference_comparison": "derived_analysis",
    "derived_career_grade": "derived_analysis",
    "derived_or_imported": "derived_analysis",
    "manual_confirmed": "user_confirmed",
    "manual_profile_import": "user_confirmed",
    "fictional_intervention": "fictional_intervention",
    "llm_generated_fiction": "generated_fiction",
    "fictional_ambient_simulation": "generated_fiction",
    "fictional_press_simulation": "generated_fiction",
    "visual_hint": "unknown",
}


def evidence_class_of(provenance: object) -> str:
    value = str(provenance or "")
    if value in EVIDENCE_CLASSES:
        return value
    return LEGACY_PROVENANCE_MAP.get(value, "unknown")


# --------------------------------------------------------------------------
# Protagonist binding (4.2)
# --------------------------------------------------------------------------

PROTAGONIST_RELATIONS = (
    "subject",
    "direct_actor",
    "directly_affected",
    "teammate_context",
    "opponent_context",
    "record_comparator",
    "decision_maker",
    "private_relationship",
)

VISIBILITY_LEVELS = ("private", "clubhouse", "club", "local", "national", "international")

# The legacy story UI exposes four values; they map onto the six-level scale.
LEGACY_VISIBILITY_MAP = {
    "private": "private",
    "clubhouse": "clubhouse",
    "public": "national",
    "social": "international",
}


def visibility_of(value: object) -> str:
    text = str(value or "")
    if text in VISIBILITY_LEVELS:
        return text
    return LEGACY_VISIBILITY_MAP.get(text, "clubhouse")


def visibility_rank(value: object) -> int:
    return VISIBILITY_LEVELS.index(visibility_of(value))


RISK_TAGS = (
    "injury",
    "rumor",
    "privacy",
    "conflict",
    "contract",
    "romance",
    "family",
    "mental_health",
    "medical",
    "discipline",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clamp(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _date_text(value: object) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("game_date must be YYYY-MM-DD")
    return text


def stable_id(*parts: object, length: int = 16) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# Event envelope (5.1)
# --------------------------------------------------------------------------

_EVENT_TYPE = re.compile(r"^SP\.[A-Z_]+(?:\.[A-Z0-9_]+)*$")


def new_event(
    *,
    event_type: str,
    game_date: str,
    universe_id: str,
    protagonist_id: object,
    actor: str,
    visibility: str,
    evidence_class: str,
    protagonist_relation: str = "subject",
    target: str | None = None,
    participants=(),
    source_facts=(),
    salience: float = 0.5,
    novelty: float = 0.5,
    emotional_valence: float = 0.0,
    emotional_arousal: float = 0.3,
    risk_tags=(),
    thread_links=(),
    reaction_plan: dict | None = None,
    payload: dict | None = None,
    event_id: str | None = None,
    observed_at: str | None = None,
) -> dict:
    if not _EVENT_TYPE.match(str(event_type or "")):
        raise ValueError(f"event_type must be a hierarchical SP.* id, got {event_type!r}")
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"unknown evidence_class {evidence_class!r}")
    if protagonist_relation not in PROTAGONIST_RELATIONS:
        raise ValueError(f"protagonist_relation {protagonist_relation!r} is not allowed (unrelated is never emitted)")
    if not universe_id or protagonist_id in (None, ""):
        raise ValueError("universe_id and protagonist_id are required")
    unknown_risks = [tag for tag in risk_tags if tag not in RISK_TAGS]
    if unknown_risks:
        raise ValueError(f"unknown risk tags: {unknown_risks}")
    return {
        "contract_version": CONTRACT_VERSION,
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": str(event_type),
        "game_date": _date_text(game_date),
        "observed_at": observed_at or _now(),
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "protagonist_relation": protagonist_relation,
        "participants": [str(row) for row in participants],
        "actor": str(actor),
        "target": str(target) if target else None,
        "visibility": visibility_of(visibility),
        "evidence_class": evidence_class,
        "source_facts": [str(row) for row in source_facts],
        "salience": _clamp(salience, 0.0, 1.0, 0.5),
        "novelty": _clamp(novelty, 0.0, 1.0, 0.5),
        "emotional_valence": _clamp(emotional_valence, -1.0, 1.0, 0.0),
        "emotional_arousal": _clamp(emotional_arousal, 0.0, 1.0, 0.3),
        "risk_tags": [str(tag) for tag in risk_tags],
        "thread_links": [dict(row) for row in thread_links],
        "reaction_plan": dict(reaction_plan or {}),
        "payload": copy.deepcopy(payload or {}),
    }


def validate_event(event: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]
    for key in ("event_id", "event_type", "game_date", "universe_id", "protagonist_id", "actor", "visibility", "evidence_class"):
        if not event.get(key):
            problems.append(f"missing {key}")
    if event.get("protagonist_relation") not in PROTAGONIST_RELATIONS:
        problems.append("protagonist_relation is invalid or unrelated")
    if event.get("evidence_class") not in EVIDENCE_CLASSES:
        problems.append("evidence_class is invalid")
    if event.get("visibility") not in VISIBILITY_LEVELS:
        problems.append("visibility is invalid")
    if not _EVENT_TYPE.match(str(event.get("event_type") or "")):
        problems.append("event_type is not an SP.* id")
    return problems


# --------------------------------------------------------------------------
# Facts (3.2, 13.2 fact_registry)
# --------------------------------------------------------------------------


def new_fact(
    *,
    kind: str,
    label: str,
    value: object,
    evidence_class: str,
    game_date: str,
    scope: str = "season",
    source: str | None = None,
    fact_id: str | None = None,
    status: str | None = None,
    formula: str | None = None,
) -> dict:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"unknown evidence_class {evidence_class!r}")
    if evidence_class == "derived_analysis" and not formula:
        raise ValueError("derived_analysis facts must expose their formula/version")
    date_text = _date_text(game_date)
    return {
        "fact_id": fact_id or stable_id(kind, label, date_text, value, scope),
        "kind": str(kind),
        "label": str(label),
        "value": value,
        "scope": str(scope),
        "status": status,
        "evidence_class": evidence_class,
        "source": source,
        "formula": formula,
        "game_date": date_text,
        "recorded_at": _now(),
        "corrections": [],
    }


# --------------------------------------------------------------------------
# Story threads (7)
# --------------------------------------------------------------------------

THREAD_TYPES = (
    "performance_pursuit",
    "role_competition",
    "manager_trust",
    "teammate_mentorship",
    "rivalry",
    "injury_and_return",
    "contract_negotiation",
    "award_race",
    "milestone_chase",
    "pennant_race",
    "public_reputation",
    "private_life",
    "aging_and_reinvention",
    "retirement_and_legacy",
)

THREAD_STATES = (
    "seeded",
    "noticed",
    "developing",
    "pressure",
    "decision",
    "consequence",
    "dormant",
    "resurfaced",
    "resolved",
    "remembered",
    "contradicted",
    "corrected",
)

THREAD_TRANSITIONS = {
    "seeded": {"noticed", "dormant", "contradicted"},
    "noticed": {"developing", "dormant", "contradicted"},
    "developing": {"pressure", "dormant", "contradicted"},
    "pressure": {"decision", "dormant", "contradicted"},
    "decision": {"consequence", "contradicted"},
    "consequence": {"resolved", "developing", "dormant", "contradicted"},
    "dormant": {"resurfaced", "remembered"},
    "resurfaced": {"developing", "pressure", "resolved"},
    "resolved": {"remembered", "resurfaced"},
    "remembered": {"resurfaced"},
    "contradicted": {"corrected"},
    "corrected": {"developing", "resolved", "remembered"},
}


def new_thread(
    *,
    thread_type: str,
    universe_id: str,
    protagonist_id: object,
    label: str,
    game_date: str,
    participants=(),
    stakes: str = "",
    opened_by_event: str | None = None,
    thread_id: str | None = None,
    open_questions=(),
) -> dict:
    if thread_type not in THREAD_TYPES:
        raise ValueError(f"unknown thread_type {thread_type!r}")
    date_text = _date_text(game_date)
    return {
        "thread_id": thread_id or stable_id(universe_id, protagonist_id, thread_type, label, date_text),
        "thread_type": thread_type,
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "label": str(label),
        "participants": [str(row) for row in participants],
        "stakes": str(stakes),
        "open_questions": [str(row) for row in open_questions],
        "state": "seeded",
        "opened_on": date_text,
        "opened_by_event": opened_by_event,
        "last_event_on": date_text,
        "state_history": [{"state": "seeded", "game_date": date_text, "event_id": opened_by_event, "at": _now()}],
        "pinned": False,
    }


def transition_thread(
    thread: dict,
    new_state: str,
    *,
    game_date: str,
    evidence_event_id: str | None = None,
    authorized_intervention: bool = False,
    note: str = "",
) -> dict:
    """Move a thread and return the append-only thread event row."""
    current = str(thread.get("state") or "seeded")
    if new_state not in THREAD_STATES:
        raise ValueError(f"unknown thread state {new_state!r}")
    if new_state not in THREAD_TRANSITIONS.get(current, set()):
        raise ValueError(f"thread cannot move from {current} to {new_state}")
    if not evidence_event_id and not authorized_intervention:
        raise ValueError("a thread transition needs an evidence event or an authorized fictional intervention")
    date_text = _date_text(game_date)
    row = {
        "thread_id": thread.get("thread_id"),
        "from_state": current,
        "to_state": new_state,
        "game_date": date_text,
        "event_id": evidence_event_id,
        "authorized_intervention": bool(authorized_intervention),
        "note": str(note),
        "at": _now(),
    }
    thread["state"] = new_state
    thread["last_event_on"] = date_text
    thread.setdefault("state_history", []).append(
        {"state": new_state, "game_date": date_text, "event_id": evidence_event_id, "at": row["at"]}
    )
    return row


# --------------------------------------------------------------------------
# Relationship graph (6.2)
# --------------------------------------------------------------------------

EDGE_DIMENSIONS = (
    "trust",
    "warmth",
    "respect",
    "rivalry",
    "dependence",
    "tension",
    "familiarity",
    "public_alignment",
    "private_alignment",
    "unresolved_debt",
)


def edge_key(source_id: object, target_id: object) -> str:
    return f"{source_id}->{target_id}"


def new_edge(source_id: object, target_id: object, *, decay_profile: str = "slow") -> dict:
    return {
        "source_id": str(source_id),
        "target_id": str(target_id),
        **{dimension: 0.0 for dimension in EDGE_DIMENSIONS},
        "last_contact_date": None,
        "shared_memory_ids": [],
        "boundary_flags": [],
        "volatility": 0.2,
        "decay_profile": decay_profile,
        "created_at": _now(),
    }


def apply_edge_delta(
    edge: dict,
    deltas: dict,
    *,
    game_date: str,
    event_id: str | None,
    explanation: str,
    memory_id: str | None = None,
) -> dict:
    """Mutate the edge and return an append-only relationship event row."""
    unknown = [key for key in deltas if key not in EDGE_DIMENSIONS]
    if unknown:
        raise ValueError(f"unknown relationship dimensions: {unknown}")
    before = {key: float(edge.get(key) or 0.0) for key in deltas}
    for key, delta in deltas.items():
        edge[key] = round(_clamp(before[key] + float(delta), -1.0, 1.0, before[key]), 4)
    edge["last_contact_date"] = _date_text(game_date)
    if memory_id and memory_id not in edge.setdefault("shared_memory_ids", []):
        edge["shared_memory_ids"].append(memory_id)
    return {
        "edge": edge_key(edge.get("source_id"), edge.get("target_id")),
        "game_date": edge["last_contact_date"],
        "event_id": event_id,
        "deltas": {key: float(value) for key, value in deltas.items()},
        "before": before,
        "after": {key: edge[key] for key in deltas},
        "explanation": str(explanation),
        "at": _now(),
    }


# --------------------------------------------------------------------------
# Narrative props and beat machine (8.11)
# --------------------------------------------------------------------------

PROP_TYPES = (
    "food",
    "gift",
    "object",
    "joke",
    "nickname",
    "promise",
    "bet",
    "ritual",
    "hobby",
    "tension",
    "symbol",
    "slip",
    "tradition",
    "gesture",
    "failure_memory",
)

PROP_STATES = (
    "seed",
    "establish",
    "callback",
    "variation",
    "escalation",
    "reversal",
    "payoff",
    "dormant",
    "rediscovered",
    "retired",
)

PROP_TRANSITIONS = {
    "seed": {"establish", "callback", "dormant", "retired"},
    "establish": {"callback", "variation", "dormant", "retired"},
    "callback": {"callback", "variation", "escalation", "payoff", "dormant", "retired"},
    "variation": {"callback", "escalation", "reversal", "payoff", "dormant", "retired"},
    "escalation": {"reversal", "payoff", "callback", "dormant", "retired"},
    "reversal": {"payoff", "callback", "dormant", "retired"},
    "payoff": {"callback", "dormant", "retired"},
    "dormant": {"rediscovered", "retired"},
    "rediscovered": {"callback", "variation", "payoff", "retired"},
    "retired": set(),
}

EMOTIONAL_ROLES = ("comic", "comforting", "embarrassing", "provocative", "symbolic")
TRUTH_STATUS = ("verified", "user_confirmed", "fictional")


def new_prop(
    *,
    universe_id: str,
    protagonist_id: object,
    name: str,
    prop_type: str,
    game_date: str,
    origin_event_id: str | None = None,
    aliases=(),
    participants=(),
    emotional_roles=("comic",),
    visibility: str = "private",
    truth_status: str = "fictional",
    escalation_ceiling: str = "clubhouse",
    prop_id: str | None = None,
) -> dict:
    if prop_type not in PROP_TYPES:
        raise ValueError(f"unknown prop_type {prop_type!r}")
    roles = [role for role in emotional_roles if role in EMOTIONAL_ROLES]
    if not roles:
        raise ValueError("at least one emotional role is required")
    if truth_status not in TRUTH_STATUS:
        raise ValueError(f"unknown truth_status {truth_status!r}")
    date_text = _date_text(game_date)
    return {
        "prop_id": prop_id or stable_id(universe_id, protagonist_id, "prop", name, date_text),
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "name": str(name),
        "aliases": [str(row) for row in aliases],
        "prop_type": prop_type,
        "origin_event_id": origin_event_id,
        "origin_date": date_text,
        "participants": [str(row) for row in participants],
        "emotional_roles": roles,
        "visibility": visibility_of(visibility),
        "truth_status": truth_status,
        "recurrence_rules": {"min_days_between_callbacks": 1, "trigger_events": []},
        "escalation_ceiling": visibility_of(escalation_ceiling),
        "relationship_effects": [],
        "callbacks": [],
        "payoff_state": "pending",
        "state": "seed",
        "last_used_date": date_text,
        "created_at": _now(),
    }


def transition_prop(prop: dict, new_state: str, *, game_date: str, event_id: str | None = None, note: str = "") -> dict:
    current = str(prop.get("state") or "seed")
    if new_state not in PROP_STATES:
        raise ValueError(f"unknown prop state {new_state!r}")
    if new_state not in PROP_TRANSITIONS.get(current, set()):
        raise ValueError(f"prop cannot move from {current} to {new_state}")
    date_text = _date_text(game_date)
    row = {
        "prop_id": prop.get("prop_id"),
        "from_state": current,
        "to_state": new_state,
        "game_date": date_text,
        "event_id": event_id,
        "note": str(note),
        "at": _now(),
    }
    prop["state"] = new_state
    prop["last_used_date"] = date_text
    if new_state in ("callback", "variation", "escalation", "reversal", "payoff", "rediscovered"):
        prop.setdefault("callbacks", []).append({"game_date": date_text, "beat": new_state, "event_id": event_id})
    if new_state == "payoff":
        prop["payoff_state"] = "delivered"
    if new_state == "retired":
        prop["payoff_state"] = prop.get("payoff_state") if prop.get("payoff_state") == "delivered" else "retired_without_payoff"
    return row


# --------------------------------------------------------------------------
# Structured narrative blocks (8.5.1, 16.4)
# --------------------------------------------------------------------------

BLOCK_TYPES = (
    "eyebrow",
    "headline",
    "dek",
    "section_heading",
    "paragraph",
    "quote",
    "translation",
    "fact_callout",
    "source_note",
    "choice",
    "list",
)

PARAGRAPH_SOFT_MAX = 420
PARAGRAPH_HARD_MAX = 650
LAYOUT_REPAIR_VERSION = "1.0.0"


def block(kind: str, text: object = "", **extra) -> dict:
    if kind not in BLOCK_TYPES:
        raise ValueError(f"unknown block type {kind!r}")
    row = {"type": kind, "text": str(text or "")}
    row.update(extra)
    return row


_INLINE_NUMBERED = re.compile(r"(?:^|\s)1\.\s+\S.*?\s2\.\s+\S")


def validate_blocks(blocks: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(blocks, list) or not blocks:
        return ["blocks must be a non-empty list"]
    for index, row in enumerate(blocks):
        if not isinstance(row, dict) or row.get("type") not in BLOCK_TYPES:
            problems.append(f"block {index}: invalid type")
            continue
        text = str(row.get("text") or "")
        if row["type"] == "paragraph":
            if len(text) > PARAGRAPH_HARD_MAX:
                problems.append(f"block {index}: paragraph exceeds {PARAGRAPH_HARD_MAX} characters")
            if _INLINE_NUMBERED.search(text):
                problems.append(f"block {index}: numbered sections stored inside one paragraph")
        if row["type"] == "quote" and not row.get("speaker"):
            problems.append(f"block {index}: quote without speaker")
        if row["type"] == "fact_callout" and not row.get("fact_ids"):
            problems.append(f"block {index}: fact_callout without fact_ids")
    return problems


def block_warnings(blocks: list[dict]) -> list[str]:
    warnings = []
    for index, row in enumerate(blocks):
        if row.get("type") == "paragraph" and len(str(row.get("text") or "")) > PARAGRAPH_SOFT_MAX:
            warnings.append(f"block {index}: paragraph longer than {PARAGRAPH_SOFT_MAX} characters")
    return warnings


def project_blocks(blocks: list[dict]) -> str:
    """Plain Markdown projection consumed by the existing safeMarkdown renderer."""
    lines: list[str] = []
    for row in blocks or []:
        kind = row.get("type")
        text = str(row.get("text") or "").strip()
        if kind == "eyebrow":
            lines.append(f"*{text}*")
        elif kind == "headline":
            lines.append(f"## {text}")
        elif kind == "dek":
            lines.append(f"*{text}*")
        elif kind == "section_heading":
            lines.append(f"### {text}")
        elif kind == "paragraph":
            lines.append(text)
        elif kind == "quote":
            speaker = row.get("speaker")
            lines.append(f"> {text}" + (f" — {speaker}" if speaker else ""))
        elif kind == "translation":
            label = row.get("label") or "한국어 번역"
            lines.append(f"[{label}] {text}")
        elif kind == "fact_callout":
            label = row.get("label") or "검증 사실"
            lines.append(f"**{label}** {text}")
        elif kind == "source_note":
            lines.append(f"_{text}_")
        elif kind == "choice":
            lines.append(f"- {text}")
        elif kind == "list":
            for item in row.get("items") or ([text] if text else []):
                lines.append(f"- {item}")
        if lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


_NUMBERED_SECTION = re.compile(r"(?:^|(?<=\s))(\d{1,2})\.\s+(?=[^\d\s])")
_SENTENCE_END = re.compile(r"[.。!?！？](?=\s|$)")


def _split_numbered(text: str) -> list[tuple[int, str]] | None:
    matches = list(_NUMBERED_SECTION.finditer(text))
    if len(matches) < 2:
        return None
    numbers = [int(match.group(1)) for match in matches]
    if numbers != list(range(1, len(numbers) + 1)):
        return None
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        # Text before "1." is a lead paragraph, not a numbered item.
        prefix = [(0, text[: matches[0].start()])]
    else:
        prefix = []
    parts = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parts.append((numbers[index], text[match.end() : end]))
    return prefix + parts


def _heading_and_body(number: int, segment: str) -> tuple[str, str]:
    stripped = segment.strip()
    terminator = _SENTENCE_END.search(stripped)
    if terminator and terminator.end() <= 80:
        return f"{number}. {stripped[: terminator.end()].strip()}", stripped[terminator.end() :].strip()
    for marker in (" — ", " - ", ": ", "："):
        position = stripped.find(marker)
        if 0 < position <= 80:
            return f"{number}. {stripped[:position].strip()}", stripped[position + len(marker) :].strip()
    return str(number), stripped


def paragraphize_legacy(text: object) -> dict:
    """Split a raw legacy response into blocks without changing its words.

    Returns ``{"blocks": [...], "original_text": ..., "layout_repair_version": ...}``.
    Blank lines are paragraph boundaries. A run of ``1. … 2. … 3. …`` inside
    one paragraph becomes section headings plus paragraphs. Decimal
    statistics (``.730``, ``22.80``), dates, inning notation (``6.1``), and
    numbers followed directly by digits are never treated as boundaries.
    """
    original = str(text or "")
    blocks: list[dict] = []
    for raw in re.split(r"\n\s*\n", original.replace("\r\n", "\n")):
        paragraph = raw.strip()
        if not paragraph:
            continue
        numbered = _split_numbered(paragraph)
        if numbered is None:
            blocks.append(block("paragraph", paragraph))
            continue
        for number, segment in numbered:
            if number == 0:
                blocks.append(block("paragraph", segment.strip()))
                continue
            heading, body = _heading_and_body(number, segment)
            blocks.append(block("section_heading", heading))
            if body:
                blocks.append(block("paragraph", body))
    if not blocks and original.strip():
        blocks.append(block("paragraph", original.strip()))
    return {
        "blocks": blocks,
        "original_text": original,
        "layout_repair_version": LAYOUT_REPAIR_VERSION,
    }


def korean_length(text: object) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))
