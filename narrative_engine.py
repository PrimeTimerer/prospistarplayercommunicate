#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic (no-LLM) conversational core (master plan 8, 9.1, 17.1-17.3).

Pipeline for one user turn::

    understand -> build context (facts, relations, threads, props, memory)
      -> retrieve typed templates -> score/choose deterministically
      -> realize (Korean particles, fact slots) -> structured blocks
      -> propose events (confirmation required) -> reaction bundle
      -> realism audit -> NarrativeResponse

Everything reads local state only. The LLM is never required; an LLM
adapter may later rerank or paraphrase an approved plan behind the same
contract, but it cannot change truth, rights, or narrative state.
"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone

import dialogue_engine
import dominant_event
import event_ontology
import narrative_contracts as nc
import accepted_facts
import memory_windows
import personas
import personal_context
import realism_gate
import reply_graph
import template_store as ts

ENGINE_VERSION = "1.0.0"
RENDERER = "deterministic"

CHOICE_LABELS = {
    "request_private_meeting": "비공개 면담을 잡는다",
    "wait_one_day": "하루 가라앉히고 다시 본다",
    "give_public_quote": "경기 후 공개 발언을 한다",
    "ask_coach_advice": "코치에게 먼저 묻는다",
    "team_dinner": "팀 식사 자리를 만든다",
    "follow_up_private": "따로 다시 이야기한다",
    "keep_private": "이 이야기는 여기에만 남긴다",
    "write_diary": "오늘의 기록으로만 남긴다",
    "call_family": "가족에게 전화한다",
    "visit_family": "집에 다녀온다",
    "ask_trainer": "트레이너에게 몸 상태를 말한다",
    "recovery_day": "훈련 대신 회복을 택한다",
    "extra_work": "추가 훈련을 한다",
    "extra_work_together": "함께 추가 훈련을 한다",
    "study_video": "영상실에서 다시 본다",
    "study_video_together": "함께 영상을 본다",
    "hold_position": "지금 입장을 유지한다",
    "declare_goal": "목표를 공개적으로 밝힌다",
    "praise_publicly": "공개적으로 칭찬한다",
    "apologize_privately": "따로 사과한다",
    "apologize_publicly": "공개적으로 사과한다",
    "console_teammate": "동료를 위로한다",
    "criticize_self_publicly": "공개적으로 책임을 말한다",
    "fan_message_later": "팬들에게 메시지를 남긴다",
    "signing_session": "사인회 시간을 늘린다",
    "ask_agent": "에이전트와 상의한다",
    "ask_club_pr": "구단 홍보팀에 맡긴다",
    "ask_next_milestone": "다음 기준선까지의 거리를 본다",
    "ask_records": "기록 대조표를 본다",
    "ask_stats": "시즌 성적을 본다",
    "ask_history": "지난 기록을 찾아본다",
    "open_record_room": "기록실을 연다",
    "update_standings": "순위표를 입력한다",
    "continue_thread": "열린 이야기를 이어간다",
    "pin_memory": "기억 보관함에 고정한다",
    "clarify_memory": "언제, 누구와의 일인지 알려준다",
    "callback_prop": "그 소재를 오늘 다시 꺼낸다",
    "retire_prop": "그 소재를 좋은 기억으로 정리한다",
    "set_prop_visibility": "소재의 공개 범위를 정한다",
    "schedule_callback": "다음 계기에 다시 꺼내도록 예약한다",
    "escalate_prop": "소재를 더 넓게 퍼뜨린다",
    "change_prop_role": "소재의 분위기를 바꾼다",
    "resolve_prop": "소재의 긴장을 정리한다",
    "confirm_escalation": "확대를 확정한다",
    "keep_current_visibility": "지금 범위를 유지한다",
}

# Acts whose committed event is publicly consequential (needs explicit confirmation).
CONSEQUENTIAL_VISIBILITY = ("national", "international", "local", "club")

STAT_LABELS = {
    "pit_K": "탈삼진", "pit_W": "승", "pit_IP": "이닝", "pit_H": "피안타", "pit_TBF": "상대 타자",
    "bat_HR": "홈런", "bat_AVG": "타율", "bat_H": "안타", "bat_RBI": "타점", "bat_SB": "도루", "bat_AB": "타수", "bat_R": "득점", "bat_SO": "삼진",
}

_TONE_HINTS = (
    ("fiery", re.compile(r"강하게|세게|정면으로|물러서지|단호|분명하게\s*말")),
    ("cold", re.compile(r"냉정|차갑게|건조하게|담담")),
    ("calm", re.compile(r"차분|조용히|부드럽게|천천히")),
    ("warm", re.compile(r"따뜻|다정|고맙다고|감싸")),
    ("witty", re.compile(r"재치|농담|웃기게|유머")),
    ("honest", re.compile(r"솔직|있는\s*그대로|숨기지")),
)


def tone_hint(text: object) -> str | None:
    value = str(text or "")
    for tone, pattern in _TONE_HINTS:
        if pattern.search(value):
            return tone
    return None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable(*parts, length: int = 16) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:length]


def _date_text(game_date: str) -> str:
    try:
        year, month, day = game_date.split("-")
        return f"{int(year)}년 {int(month)}월 {int(day)}일"
    except (ValueError, AttributeError):
        return str(game_date)


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------


def _stats_summary(stats: dict) -> str | None:
    parts = []
    if stats.get("pit_IP"):
        parts.append(f"투수 {stats.get('pit_W', 0)}승 · {stats.get('pit_IP')}이닝 · {stats.get('pit_K', 0)}탈삼진 · 피안타 {stats.get('pit_H', 0)}")
    if stats.get("bat_AB"):
        avg = stats.get("bat_AVG")
        avg_text = f"타율 {avg:.3f}".replace("0.", ".") if isinstance(avg, (int, float)) and avg else "타율 —"
        parts.append(f"타자 {avg_text} · {stats.get('bat_HR', 0)}홈런 · {stats.get('bat_RBI', 0)}타점 · {stats.get('bat_SB', 0)}도루")
    return " / ".join(parts) if parts else None


def _honors_text(career: dict | None) -> str | None:
    rows = [row for row in ((career or {}).get("honor_summary") or []) if isinstance(row, dict)]
    if not rows:
        return None
    return ", ".join(f"{row.get('label') or row.get('kind')} {int(row.get('count') or 1)}회" for row in rows[:6])


def _records_text(career: dict | None) -> str | None:
    rows = []
    for scope, entries in ((career or {}).get("records") or {}).items():
        for row in entries or []:
            if row.get("status") in ("tied", "broken"):
                rows.append(f"{row.get('label')} {'동률' if row.get('status') == 'tied' else '초과'}({'시즌' if scope == 'season' else '통산'})")
    return ", ".join(rows[:6]) if rows else None


def _next_milestone_text(career: dict | None) -> str | None:
    rows = [row for row in ((career or {}).get("next_milestones") or []) if isinstance(row, dict)]
    if not rows:
        return None
    parts = []
    for row in rows[:3]:
        label = row.get("label") or row.get("stat_key")
        remaining = row.get("remaining")
        parts.append(f"{label}까지 {remaining}" if remaining is not None else str(label))
    return ", ".join(parts)


def _standings_text(day_context: dict | None) -> str | None:
    rows = [row for row in ((day_context or {}).get("standings") or []) if isinstance(row, dict)]
    if not rows:
        return None
    return ", ".join(f"{row.get('rank')}위 {row.get('team')} {row.get('wins')}승 {row.get('losses')}패" for row in rows[:6])


def _threads_text(threads: dict | None) -> str | None:
    rows = [row for row in (threads or {}).values() if isinstance(row, dict) and row.get("state") not in ("resolved", "remembered")]
    if not rows:
        return None
    return " / ".join(f"{row.get('label')} ({row.get('state')}, {row.get('last_event_on')})" for row in rows[:5])


def _relations_text(edges: dict | None) -> str | None:
    rows = [row for row in (edges or {}).values() if isinstance(row, dict)]
    if not rows:
        return None
    parts = []
    for row in rows[:6]:
        label = personas.label(row.get("target_id")) if row.get("target_id") in personas.PERSONAS else dialogue_engine.ENTITY_LEXICON.get(str(row.get("target_id")), {}).get("label", row.get("target_id"))
        parts.append(f"{label}: 신뢰 {row.get('trust', 0):+.1f} 존중 {row.get('respect', 0):+.1f} 긴장 {row.get('tension', 0):+.1f}")
    return " / ".join(parts)


def _props_text(props: dict | None) -> str | None:
    rows = [
        row
        for row in (props or {}).values()
        if isinstance(row, dict)
        and row.get("state") != "retired"
        and memory_windows.is_automatic_retrieval_active(row)
    ]
    if not rows:
        return None
    return " / ".join(f"{row.get('name')} ({nc_state_label(row.get('state'))}, {row.get('origin_date')}부터)" for row in rows[:5])


def nc_state_label(state: object) -> str:
    return {
        "seed": "씨앗", "establish": "자리 잡음", "callback": "다시 등장", "variation": "변주", "escalation": "확대",
        "reversal": "반전", "payoff": "매듭", "dormant": "잠잠", "rediscovered": "재발견", "retired": "정리됨",
    }.get(str(state), str(state))


def _memory_text(session: dict | None, conversation_turns: list | None, query: str) -> str | None:
    """Naive keyword recall over stored turns (FTS over the ledger is added by the service)."""
    stems = {token for token in dialogue_engine.stems(query) if len(token) >= 2}
    hits = []
    for turn in (session or {}).get("turns") or []:
        scene = turn.get("scene") or {}
        text = " ".join(str(scene.get(key) or "") for key in ("title", "summary", "response")) + " " + str((turn.get("input") or {}).get("user_text") or "")
        if stems & set(dialogue_engine.stems(text)):
            hits.append(f"{turn.get('game_date')}: {scene.get('title')}")
    for turn in conversation_turns or []:
        text = f"{turn.get('user_text', '')} {turn.get('reply_text', '')}"
        if stems & set(dialogue_engine.stems(text)):
            hits.append(f"{turn.get('game_date')}: {str(turn.get('user_text') or '')[:40]}")
    return " / ".join(hits[:4]) if hits else None


def build_context(
    *,
    understanding: dict,
    snapshot: dict,
    game_date: str,
    spotlight: dict | None,
    career: dict | None = None,
    day_context: dict | None = None,
    session: dict | None = None,
    ledger_state: dict | None = None,
    universe_id: str = "",
    tone: str | None = None,
    locale: str = "ko",
) -> dict:
    player = snapshot.get("player") or {}
    stats = snapshot.get("stats") or {}
    state = ledger_state or {}
    target = understanding.get("target")
    target_label = dialogue_engine.ENTITY_LEXICON.get(str(target), {}).get("label", "상대")
    layered_memory = memory_windows.build_view(
        state,
        as_of_date=game_date,
        universe_id=universe_id,
        protagonist_id=player.get("id"),
    )
    memory_refs = memory_windows.recall(
        layered_memory,
        understanding.get("normalized_text") or "",
        limit=3,
    )
    active_issue_text = memory_windows.prompt_section(
        layered_memory,
        query=understanding.get("normalized_text") or "",
        limit=5,
    )
    facts = {
        "stats": {key: value for key, value in stats.items() if value not in (None, "", 0, 0.0)},
        "honors_text": _honors_text(career),
        "records_text": _records_text(career),
        "next_milestone_text": _next_milestone_text(career),
        "standings_text": _standings_text(day_context),
        "threads_text": _threads_text(state.get("narrative_threads")),
        "relations_text": _relations_text(state.get("relationship_edges")),
        "props_text": _props_text(state.get("narrative_props")),
        "memory_text": _memory_text(session, state.get("conversation_turns"), understanding.get("normalized_text") or ""),
        "layered_memory_text": active_issue_text,
        "stats_summary_text": _stats_summary(stats),
    }
    facts = {key: value for key, value in facts.items() if value not in (None, "", {}, [])}
    fact_date = accepted_facts.requested_date(understanding.get("normalized_text") or "", game_date)
    reviewed_facts = accepted_facts.for_context(state, universe_id=universe_id, protagonist_id=player.get("id"), game_date=fact_date)
    context_refs = personal_context.scene_references(
        state,
        universe_id=universe_id,
        protagonist_id=player.get("id"),
        game_date=game_date,
        query=understanding.get("normalized_text") or "",
        audience=understanding.get("visibility_hint") or "private",
        limit=1,
    )
    name = str(player.get("name") or "선수")
    short = name.split()[-1] if " " in name else name
    edge_key = nc.edge_key("protagonist", target or "self")
    relation = (state.get("relationship_edges") or {}).get(edge_key) or {}
    memory = state.get("narrative_memory") or {}
    return {
        "universe_id": universe_id,
        "protagonist_id": str(player.get("id") or ""),
        "primary_act": understanding.get("primary_act"),
        "secondary_acts": understanding.get("secondary_acts") or [],
        "target": target,
        "visibility": understanding.get("visibility_hint") or "private",
        "tier_index": int((spotlight or {}).get("tier_index") or 0),
        "emotion": understanding.get("emotion") or {"valence": 0.0, "arousal": 0.2},
        "fact_requirements": understanding.get("fact_requirements") or [],
        "risk_tags": understanding.get("risk_tags") or [],
        "preferred_personas": [],
        "locale": locale,
        "tone": tone,
        "relation": relation,
        "active_thread_types": [row.get("thread_type") for row in (state.get("narrative_threads") or {}).values() if isinstance(row, dict) and row.get("state") not in ("resolved", "remembered")],
        "recent_template_ids": [row.get("template_id") for row in (memory.get("signatures") or [])[-40:]],
        "recent_signatures": list((memory.get("signatures") or [])[-40:]),
        "names": [name, short],
        "user_authored": True,
        "slots": {
            "player": name,
            "player_short": short,
            "team": str(player.get("team") or "팀"),
            "target_label": target_label,
            "date_text": _date_text(game_date),
            "game_date": game_date,
            "career_year": (snapshot.get("date") or {}).get("career_year"),
            "user_text": understanding.get("normalized_text") or None,
        },
        "facts": facts,
        "accepted_facts": reviewed_facts,
        "requested_fact_date": fact_date,
        "personal_context_refs": context_refs,
        "memory_window_refs": memory_refs,
        "memory_views": layered_memory,
    }


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _responders(understanding: dict) -> list[str]:
    act = understanding.get("primary_act")
    target = understanding.get("target")
    if act == "vent":
        confidants = ["inner_voice", "veteran_teammate", "catcher", "family_parent", "partner", "agent", "pitching_coach", "mentor", "trainer", "club_pr", "rookie_teammate", "local_radio"]
        return confidants
    if act in ("ask", "recall", "predict"):
        return ["record_desk", "narrator", "archive_desk", "national_desk", "inner_voice", "former_pitcher"]
    if act in ("give_public_quote", "declare", "criticize_self"):
        # The user states an intention; a confidant weighs it before the
        # event is confirmed. Press and fans react once it is committed.
        return ["inner_voice", "veteran_teammate", "catcher", "agent", "club_pr", "manager", "pitching_coach", "family_parent", "partner", "mentor", "rookie_teammate", "trainer", "narrator", "beat_reporter", "national_desk", "columnist", "record_desk", "former_pitcher", "former_hitter", "tv_analyst", "local_paper", "local_radio", "home_supporter", "skeptical_fan", "rival_fan", "longtime_fan", "youth_player", "opposing_star", "archive_desk", "front_office"]
    if act in ("console", "praise", "apologize", "request_private_meeting"):
        preferred = personas.responders_for(target)
        return preferred + ["narrator", "inner_voice", "veteran_teammate", "catcher", "beat_reporter", "national_desk", "columnist", "record_desk", "former_pitcher", "tv_analyst", "home_supporter", "skeptical_fan", "rival_fan", "longtime_fan", "youth_player", "local_paper", "local_radio", "agent", "family_parent", "partner", "pitching_coach", "trainer", "rookie_teammate", "opposing_star", "front_office", "club_pr", "archive_desk", "former_hitter", "mentor"]
    if act and act.startswith("prop_"):
        return ["inner_voice"]
    if act in ("worry", "celebrate", "thank"):
        return ["inner_voice", "trainer", "family_parent", "veteran_teammate", "home_supporter"]
    return ["inner_voice", "veteran_teammate", "family_parent", "narrator"]


INTENTION_ACTS = ("give_public_quote", "declare", "criticize_self")
CONFIDANTS = ("inner_voice", "veteran_teammate", "catcher", "agent", "club_pr", "manager", "pitching_coach", "family_parent", "partner", "mentor", "rookie_teammate", "trainer")


def _plan_reply(store: ts.TemplateStore, understanding: dict, context: dict, seed: str) -> tuple[dict | None, dict]:
    act = understanding.get("primary_act")
    target = understanding.get("target")
    visibility = context.get("visibility")
    preferred = _responders(understanding)
    context = dict(context, preferred_personas=preferred)
    candidates: list[dict] = []
    if act in INTENTION_ACTS:
        # A stated intention is answered privately by a confidant; the
        # event's own visibility governs reactions after confirmation.
        candidates = store.candidates(
            responds_to=act, target=target, visibility="*", personas_allowed=list(CONFIDANTS), locale="ko",
            kind="response", context=context, query=understanding.get("normalized_text"),
        )
    if act and not candidates:
        candidates = store.candidates(
            responds_to=act, target=target, visibility=visibility, personas_allowed=preferred, locale="ko",
            kind="prop" if act.startswith("prop_") else "response", context=context, query=understanding.get("normalized_text"),
        )
        if not candidates and target:
            candidates = store.candidates(responds_to=act, target=None, visibility=visibility, personas_allowed=preferred, locale="ko", kind="prop" if act.startswith("prop_") else "response", context=context)
        if not candidates:
            candidates = store.candidates(responds_to=act, target=None, visibility=None, personas_allowed=preferred, locale="ko", kind="prop" if act.startswith("prop_") else "response", context=context)
    if not candidates:
        candidates = store.candidates(responds_to="*", kind="fallback", locale="ko", context=context)
    candidates = store.diversity_filter(candidates, context.get("recent_signatures") or [])
    chosen = store.choose(candidates, context, seed=seed)
    return chosen, context


def _proposals(understanding: dict, context: dict, reply: dict | None, game_date: str) -> list[dict]:
    act = understanding.get("primary_act")
    rows: list[dict] = []
    seen = set()

    def add(act_name: str, target: str | None, visibility: str | None, source: str):
        spec = event_ontology.DIALOGUE_ACT_EVENTS.get(act_name)
        if not spec or not spec.get("creates_event"):
            return
        event_type = spec["event_type"]
        vis = nc.visibility_of(visibility or spec.get("visibility") or "private")
        key = (event_type, target, vis)
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "proposal_id": _stable(game_date, event_type, target, vis, source, len(rows)),
                "type": event_type,
                "label": event_ontology.label_of(event_type),
                "act": act_name,
                "target": target,
                "target_label": dialogue_engine.ENTITY_LEXICON.get(str(target), {}).get("label", "상대") if target else None,
                "visibility": vis,
                "requires_confirmation": True,
                "consequential": vis in CONSEQUENTIAL_VISIBILITY,
                "thread_type": _thread_type_for(event_type, target),
                "source": source,
            }
        )

    dual = understanding.get("dual_acts") or []
    if dual:
        for row in dual:
            add(row["act"], row.get("target"), row.get("visibility"), "clause")
    elif act in dialogue_engine.EVENT_ACTS:
        add(act, understanding.get("target"), understanding.get("visibility_hint"), "primary")
    template_events = ((reply or {}).get("effects") or {}).get("proposed_events") or []
    for event_type in (template_events if not rows else []):
        if event_type in {row["type"] for row in rows}:
            continue
        if not event_ontology.is_registered(event_type):
            continue
        vis = nc.visibility_of(understanding.get("visibility_hint") or "private")
        key = (event_type, understanding.get("target"), vis)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "proposal_id": _stable(game_date, event_type, understanding.get("target"), vis, "template", len(rows)),
                "type": event_type,
                "label": event_ontology.label_of(event_type),
                "act": act,
                "target": understanding.get("target"),
                "target_label": dialogue_engine.ENTITY_LEXICON.get(str(understanding.get("target")), {}).get("label", "상대") if understanding.get("target") else None,
                "visibility": vis,
                "requires_confirmation": True,
                "consequential": vis in CONSEQUENTIAL_VISIBILITY,
                "thread_type": _thread_type_for(event_type, understanding.get("target")),
                "source": "template",
            }
        )
    return rows


def _thread_type_for(event_type: str, target: str | None = None) -> str:
    domain = event_ontology.domain_of(event_type) or ""
    by_target = {
        "manager": "manager_trust",
        "coach": "manager_trust",
        "rival": "rivalry",
        "agent": "contract_negotiation",
        "front_office": "contract_negotiation",
        "family": "private_life",
        "partner": "private_life",
        "medical": "injury_and_return",
    }
    if domain in ("SP.RELATION", "SP.USER", "SP.PRIVATE") and target in by_target:
        return by_target[target]
    return {
        "SP.ROSTER": "role_competition",
        "SP.ROLE": "role_competition",
        "SP.RELATION": "teammate_mentorship",
        "SP.MEDIA": "public_reputation",
        "SP.MILESTONE": "milestone_chase",
        "SP.CONTRACT": "contract_negotiation",
        "SP.HEALTH": "injury_and_return",
        "SP.PRIVATE": "private_life",
        "SP.PUBLIC": "public_reputation",
        "SP.USER": "private_life",
        "SP.TRAINING": "performance_pursuit",
        "SP.GAME.POST": "performance_pursuit",
        "SP.GAME.PRE": "performance_pursuit",
        "SP.AWARD": "award_race",
        "SP.STANDINGS": "pennant_race",
        "SP.LEGACY": "retirement_and_legacy",
        "SP.AGING": "aging_and_reinvention",
        "SP.ROMANCE": "private_life",
    }.get(domain, "private_life")


def _choices(reply: dict | None, understanding: dict) -> list[dict]:
    ids = list(((reply or {}).get("effects") or {}).get("proposed_choices") or [])
    if not ids:
        level = understanding.get("fallback_level", 4)
        ids = ["request_private_meeting", "give_public_quote"] if level <= 4 else ["keep_private", "write_diary"]
    rows = []
    for choice_id in ids[:3]:
        rows.append({"id": choice_id, "label": CHOICE_LABELS.get(choice_id, choice_id)})
    return rows


def _reply_blocks(understanding: dict, reply: dict | None, context: dict, *, clarification: str | None = None, help_rows=()) -> list[dict]:
    blocks: list[dict] = []
    if reply is None:
        if clarification:
            blocks.append(nc.block("paragraph", clarification))
        for row in help_rows:
            blocks.append(nc.block("choice", row))
        return blocks
    persona = reply.get("persona")
    label = personas.label(persona) if persona and persona != "narrator" else None
    text = str(reply.get("text") or "")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if reply.get("headline"):
        blocks.append(nc.block("headline", reply["headline"]))
    if reply.get("dek"):
        blocks.append(nc.block("dek", reply["dek"]))
    for paragraph in paragraphs or [text]:
        if persona and persona != "narrator":
            blocks.append(nc.block("quote", paragraph, speaker=label))
        else:
            blocks.append(nc.block("paragraph", paragraph))
    if reply.get("translation_ko"):
        blocks.append(nc.block("translation", reply["translation_ko"], label="한국어 번역"))
    for reference in context.get("personal_context_refs") or []:
        blocks.append(
            nc.block(
                "paragraph",
                f"{reference['label']}에 관해 이 세계선에 남겨 둔 기억이 있다. {reference['detail']}",
                origin="world_context_projection",
                evidence_class=reference.get("evidence_class") or "fictional_intervention",
                memory_event_ids=[reference["context_id"]],
                context_revision=reference.get("revision"),
            )
        )
    for reference in context.get("memory_window_refs") or []:
        blocks.append(
            nc.block(
                "paragraph",
                f"{reference['game_date']}에 남은 ‘{reference['label']}’ 기억이 이번 말과 이어진다.",
                origin="layered_memory_projection",
                evidence_class=reference.get("evidence_class") or "derived_analysis",
                memory_event_ids=[reference["source_ref"]],
            )
        )
    fact_lines = _fact_callouts(understanding, context)
    if fact_lines:
        blocks.append(nc.block("fact_callout", " · ".join(fact_lines), label="검증 사실", fact_ids=[f"stats:{key}" for key in (context.get("fact_requirements") or [])] or ["snapshot"]))
    return blocks


def _fact_callouts(understanding: dict, context: dict) -> list[str]:
    if understanding.get("primary_act") not in ("ask", "recall"):
        return []
    facts = context.get("facts") or {}
    rows = []
    for key in understanding.get("fact_requirements") or []:
        if key.startswith("stats."):
            stat = key.split(".", 1)[1]
            value = (facts.get("stats") or {}).get(stat)
            if value in (None, "", 0):
                continue
            label = STAT_LABELS.get(stat, stat)
            if stat == "bat_AVG" and isinstance(value, float):
                rows.append(f"{label} {value:.3f}".replace(" 0.", " ."))
            elif stat in ("pit_W", "pit_IP"):
                rows.append(f"{value}{label}")
            else:
                rows.append(f"{label} {value}")
    return rows


def understand(text: object, state: dict | None = None) -> dict:
    return dialogue_engine.understand(text, state)


# --------------------------------------------------------------------------
# Turn generation
# --------------------------------------------------------------------------


def generate_turn(
    *,
    user_text: str,
    snapshot: dict,
    game_date: str,
    spotlight: dict | None,
    universe_id: str,
    ledger_state: dict | None = None,
    session: dict | None = None,
    career: dict | None = None,
    day_context: dict | None = None,
    conversation_state: dict | None = None,
    tone: str | None = None,
    store: ts.TemplateStore | None = None,
    sequence: int = 0,
) -> dict:
    """Produce a NarrativeResponse for one chat turn without committing anything."""
    store = store or ts.default_store()
    understanding = understand(user_text, conversation_state)
    tone = tone or tone_hint(user_text)
    context = build_context(
        understanding=understanding, snapshot=snapshot, game_date=game_date, spotlight=spotlight,
        career=career, day_context=day_context, session=session, ledger_state=ledger_state,
        universe_id=universe_id, tone=tone,
    )
    # The message is an intention, not a public statement: templates that
    # quote `{user_text}` wait for the confirmed event and its statement.
    context["slots"]["user_text"] = None
    seed = f"{universe_id}:{context['protagonist_id']}:{game_date}:{snapshot.get('content_hash')}:{sequence}:{understanding.get('normalized_text')}"
    clarification = None
    help_rows: list[str] = []
    chosen = None
    reply = None
    level = int(understanding.get("fallback_level") or 4)
    result_question = understanding.get("primary_act") in ("ask", "recall") and "game.result" in (understanding.get("fact_requirements") or [])
    if result_question:
        # Exact result questions use the evidence projection, not a randomly
        # selected season-summary reply with unrelated stats.
        level = min(level, 4)
    elif level >= 6:
        help_rows = dialogue_engine.help_suggestions()
    elif level == 5:
        clarification = dialogue_engine.clarification_question(understanding)
    else:
        chosen, context = _plan_reply(store, understanding, context, seed)
        if chosen:
            reply = store.realize(chosen, context)
            variant = chosen.get("variant") or {}
            if variant.get("headline"):
                reply["headline"] = ts.render_text(str(variant["headline"]), context)
            if variant.get("dek"):
                reply["dek"] = ts.render_text(str(variant["dek"]), context)
        else:
            clarification = dialogue_engine.clarification_question(understanding)
            level = 5
    blocks = accepted_facts.result_blocks(context["accepted_facts"], context["requested_fact_date"]) if result_question else _reply_blocks(understanding, reply, context, clarification=clarification, help_rows=help_rows)
    if result_question:
        stat_lines = _fact_callouts(understanding, context)
        if stat_lines:
            blocks.append(nc.block("fact_callout", " · ".join(stat_lines), label="세이브 검증 · 현재 시즌", evidence_class="save_verified", fact_ids=[f"snapshot:{snapshot.get('content_hash')}"]))
    if not blocks:
        blocks = [nc.block("paragraph", "들었다. 이 말은 남겨 둔다.")]
    text = nc.project_blocks(blocks)
    audit = realism_gate.audit_public_prose(
        " ".join(str(row.get("text") or "") for row in blocks),
        layer="immersive", personas=personas.all_labels(), protagonist_names=context.get("names") or [],
        facts=[{"kind": "record_tied"}] if context.get("facts", {}).get("records_text") else (),
    )
    proposals = _proposals(understanding, context, reply, game_date) if level <= 4 else []
    choices = _choices(reply, understanding)
    response = {
        "response_id": _stable(seed, "response"),
        "engine_version": ENGINE_VERSION,
        "renderer": RENDERER,
        "understanding": {
            "dialogue_acts": [row["act"] for row in understanding.get("acts") or []][:4],
            "primary_act": understanding.get("primary_act"),
            "entities": [row["id"] for row in understanding.get("entities") or []],
            "target": understanding.get("target"),
            "target_source": understanding.get("target_source"),
            "visibility": understanding.get("visibility_hint"),
            "emotion": understanding.get("emotion"),
            "risk_tags": understanding.get("risk_tags"),
            "fact_requirements": understanding.get("fact_requirements"),
            "confidence": understanding.get("confidence"),
            "fallback_level": level,
            "language": understanding.get("language"),
            "dual_acts": understanding.get("dual_acts"),
            "prop_acts": understanding.get("prop_acts"),
        },
        "reply": {"language": "ko", "text": text, "blocks": blocks, "persona": (reply or {}).get("persona")},
        "proposed_events": proposals,
        "choices": choices,
        "fact_ids": list(dict.fromkeys(([f"snapshot:{snapshot.get('content_hash')}"] if snapshot.get("content_hash") else []) + [fact_id for row in blocks for fact_id in row.get("fact_ids", [])])),
        "thread_updates": [],
        "provenance": {
            "template_ids": [reply["template_id"]] if reply else [],
            "pack_ids": [reply["pack_id"]] if reply else [],
            "license_classes": [reply["license_class"]] if reply else [],
            "renderer": RENDERER,
            "renderer_version": ENGINE_VERSION,
            "corpus_manifest": store.manifest_hash(),
            "seed": _stable(seed, length=12),
            "score": (reply or {}).get("score"),
            "signature": (reply or {}).get("signature"),
            "accepted_fact_bridge": accepted_facts.VERSION if result_question else None,
            "context_refs": [
                {"context_id": row.get("context_id"), "revision": row.get("revision"), "kind": row.get("kind")}
                for row in context.get("personal_context_refs") or []
            ] + [
                {"memory_id": row.get("memory_id"), "source_ref": row.get("source_ref"), "game_date": row.get("game_date"), "kind": row.get("kind")}
                for row in context.get("memory_window_refs") or []
            ],
        },
        "audit": audit.as_dict(),
        "conversation_state": {
            "last_target": understanding.get("target") or (conversation_state or {}).get("last_target"),
            "last_act": understanding.get("primary_act") or (conversation_state or {}).get("last_act"),
            "active_persona": (reply or {}).get("persona") or (conversation_state or {}).get("active_persona"),
        },
        "creates_event": bool(proposals),
        "mode": "사건 제안" if proposals else "대화만 함",
        "created_at": _now(),
    }
    return response


# --------------------------------------------------------------------------
# Committed event realisation (scene + reaction bundle)
# --------------------------------------------------------------------------


def realize_event(
    *,
    proposal: dict,
    snapshot: dict,
    game_date: str,
    spotlight: dict | None,
    universe_id: str,
    user_text: str | None,
    ledger_state: dict | None = None,
    career: dict | None = None,
    store: ts.TemplateStore | None = None,
    sequence: int = 1,
    prop: dict | None = None,
    statement: str | None = None,
    tone: str | None = None,
) -> dict:
    """Build the scene blocks and reaction bundle for a confirmed proposal.

    ``user_text`` is the chat message that produced the proposal (seed and
    diagnostics only). ``statement`` is the in-world line the protagonist
    actually says, when the user supplied one; only then is it quoted.
    """
    store = store or ts.default_store()
    event_type = str(proposal.get("type"))
    visibility = nc.visibility_of(proposal.get("visibility") or "private")
    understanding = {"primary_act": proposal.get("act"), "target": proposal.get("target"), "visibility_hint": visibility, "emotion": {"valence": 0.0, "arousal": 0.3}, "normalized_text": user_text or ""}
    context = build_context(understanding=understanding, snapshot=snapshot, game_date=game_date, spotlight=spotlight, career=career, ledger_state=ledger_state, universe_id=universe_id, tone=tone or tone_hint(user_text))
    context["visibility"] = visibility
    statement = str(statement or "").strip() or None
    context["slots"]["user_text"] = statement
    if prop:
        context["slots"]["prop_name"] = prop.get("name")
        context["slots"]["prop_origin_date"] = _date_text(str(prop.get("origin_date") or game_date))
        context["facts"]["prop_name"] = prop.get("name")
        context["facts"]["prop_origin_date"] = context["slots"]["prop_origin_date"]
    seed = f"{universe_id}:{context['protagonist_id']}:{game_date}:{snapshot.get('content_hash')}:{sequence}:{event_type}:{user_text or ''}:{statement or ''}"
    # Scene: narrator template for the event type, then its domain.
    scenes = [row for row in store.candidates(kind="scene", locale="ko", context=context, limit=200) if any(event_type == target or event_type.startswith(target) or target.startswith(event_type) for target in row["responds_to"])]
    if not scenes:
        domain = event_ontology.domain_of(event_type) or ""
        scenes = [row for row in store.candidates(kind="scene", locale="ko", context=context, limit=200) if any(target.startswith(domain) for target in row["responds_to"])]
    scenes = store.diversity_filter(scenes, context.get("recent_signatures") or [])
    blocks: list[dict] = [nc.block("eyebrow", event_ontology.label_of(event_type))]
    scene_text = None
    scene_template = None
    if scenes:
        chosen = store.choose(scenes, dict(context, preferred_personas=["narrator"]), seed=f"{seed}:scene")
        variant = chosen["variant"]
        realized = store.realize(chosen, context)
        scene_template = realized
        headline = ts.render_text(str(variant.get("headline") or event_ontology.label_of(event_type)), context)
        dek = ts.render_text(str(variant.get("dek") or ""), context) if variant.get("dek") else None
        blocks.append(nc.block("headline", headline))
        if dek:
            blocks.append(nc.block("dek", dek))
        for paragraph in [part.strip() for part in re.split(r"\n\s*\n", realized["text"]) if part.strip()]:
            blocks.append(nc.block("paragraph", paragraph))
        scene_text = realized["text"]
    else:
        headline = event_ontology.label_of(event_type)
        blocks.append(nc.block("headline", headline))
        blocks.append(nc.block("paragraph", f"{context['slots']['player']}의 하루에 {headline} 장면이 더해졌다."))
    if statement:
        blocks.append(nc.block("quote", statement, speaker=context["slots"]["player"]))
    fact_line = context["facts"].get("stats_summary_text")
    if fact_line:
        blocks.append(nc.block("fact_callout", fact_line, label="검증 사실", fact_ids=[f"snapshot:{snapshot.get('content_hash')}"]))
    # Reaction bundle scaled by the attention band and the visibility.
    tier = int((spotlight or {}).get("tier_index") or 0)
    thread_count = 0 if visibility in ("private", "clubhouse") else min(3, 1 + tier // 2)
    press_count = 0 if visibility in ("private", "clubhouse") else min(3, tier // 2 + (1 if tier >= 1 else 0))
    foreign_count = 0 if visibility not in ("national", "international") or tier < 3 else min(3, tier - 2)
    headline_fact = str(blocks[1]["text"]) if len(blocks) > 1 else headline
    guards = []
    boards = []
    for index in range(thread_count):
        thread = reply_graph.build_thread(store, seed=f"{seed}:thread:{index}", event_type=event_type, headline_fact=headline_fact, visibility=visibility, context=context, reply_count=3 + (index % 2), guards=guards, names=context.get("names"), protagonist_id=context["protagonist_id"])
        if thread:
            boards.append(thread)
    media = reply_graph.build_press(store, seed=seed, event_type=event_type, headline_fact=headline_fact, visibility=visibility, context=context, count=press_count, guards=guards, names=context.get("names"))
    foreign = reply_graph.build_foreign(store, seed=seed, event_type=event_type, headline_fact=headline_fact, visibility=visibility, context=context, count=foreign_count, guards=guards)
    for row in foreign:
        blocks.append(nc.block("quote", row["original"], speaker=row["channel"]))
        if row.get("translation_ko"):
            blocks.append(nc.block("translation", row["translation_ko"], label="한국어 번역"))
    audit_text = " ".join(str(row.get("text") or "") for row in blocks)
    audit = realism_gate.audit_public_prose(audit_text, personas=personas.all_labels(), protagonist_names=context.get("names") or [])
    signature = {
        "template_id": (scene_template or {}).get("template_id"),
        "persona": "narrator",
        "opening": ((scene_template or {}).get("signature") or {}).get("opening"),
        "outline": ((scene_template or {}).get("signature") or {}).get("outline"),
        "head": realism_gate.normalize_text(scene_text or headline)[:8],
        "skeleton": realism_gate.headline_skeleton(headline, context.get("names") or []),
        "stance_sequences": [row.get("stance_sequence") for row in boards],
    }
    return {
        "event_type": event_type,
        "label": event_ontology.label_of(event_type),
        "visibility": visibility,
        "headline": headline,
        "renderer": RENDERER,
        "blocks": blocks,
        "text": nc.project_blocks(blocks),
        "reactions": {"boards": boards, "media": media, "foreign": foreign, "budget": {"threads": thread_count, "press": press_count, "foreign": foreign_count}},
        "audit": audit.as_dict(),
        "signature": signature,
        "provenance": {
            "template_ids": [row for row in [(scene_template or {}).get("template_id")] if row] + [row.get("template_id") for row in media] + [post.get("template_id") for board in boards for post in board.get("posts", [])] + [row.get("template_id") for row in foreign],
            "renderer": RENDERER,
            "renderer_version": ENGINE_VERSION,
            "corpus_manifest": store.manifest_hash(),
        },
        "thread_type": proposal.get("thread_type") or _thread_type_for(event_type, proposal.get("target")),
    }


# --------------------------------------------------------------------------
# Verified-game reaction bundle (dominant event aware)
# --------------------------------------------------------------------------


def realize_game_reactions(
    *,
    event: dict,
    snapshot: dict,
    game_date: str,
    spotlight: dict | None,
    universe_id: str,
    ledger_state: dict | None = None,
    career: dict | None = None,
    extra_facts=(),
    store: ts.TemplateStore | None = None,
) -> dict:
    """Dominant-event-aware reactions for a verified game delta (5.4.1)."""
    store = store or ts.default_store()
    candidates = dominant_event.extract_game_candidates(event, extra_facts=extra_facts)
    selection = dominant_event.select_dominant(candidates)
    lead = selection.get("lead") or {}
    understanding = {"primary_act": None, "target": None, "visibility_hint": "national", "emotion": {"valence": 0.3 if lead.get("outcome") == "positive" else -0.3 if lead.get("outcome") == "negative" else 0.0, "arousal": 0.5}, "normalized_text": ""}
    context = build_context(understanding=understanding, snapshot=snapshot, game_date=game_date, spotlight=spotlight, career=career, ledger_state=ledger_state, universe_id=universe_id)
    context.update(dominant_event.outcome_context(selection))
    context["fact_ids"] = list(event.get("source_facts") or [f"snapshot:{snapshot.get('content_hash')}"])
    context["user_authored"] = False
    tier = int((spotlight or {}).get("tier_index") or 0)
    visibility = "international" if tier >= 4 else "national" if tier >= 2 else "local" if tier >= 1 else "club"
    context["visibility"] = visibility
    headline_fact = str(selection.get("headline_fact") or "새 경기 기록")
    seed = f"{universe_id}:{context['protagonist_id']}:{game_date}:{snapshot.get('content_hash')}:game"
    guards = selection.get("guards") or []
    boards = []
    for index in range(min(3, 1 + tier // 2)):
        thread = reply_graph.build_thread(store, seed=f"{seed}:thread:{index}", event_type=lead.get("event_type"), headline_fact=headline_fact, visibility=visibility, context=context, reply_count=3 + (index % 2), guards=guards, names=context.get("names"))
        if thread:
            boards.append(thread)
    media = reply_graph.build_press(store, seed=seed, event_type=lead.get("event_type"), headline_fact=headline_fact, visibility=visibility, context=context, count=min(3, 1 + tier // 2), guards=guards, names=context.get("names"))
    foreign = reply_graph.build_foreign(store, seed=seed, event_type=lead.get("event_type"), headline_fact=headline_fact, visibility=visibility, context=context, count=min(3, max(0, tier - 2)), guards=guards)
    for row in media + foreign:
        row["fact_ids"] = list(context["fact_ids"])
    mandatory_labels = [row["label"] for row in selection.get("mandatory") or []]
    record_line = None
    if lead.get("verified"):
        record_line = f"확인된 사실: {lead['label']}"
    return {
        "dominant": {"lead": lead, "secondary": [row["label"] for row in selection.get("secondary") or []], "mandatory": mandatory_labels, "guards": guards, "two_way": selection.get("two_way")},
        "headline_fact": headline_fact,
        "record_line": record_line,
        "reactions": {"boards": boards, "media": media, "foreign": foreign},
        "visibility": visibility,
    }
