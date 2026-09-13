#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-LLM user-authored saga engine: narrative props and beats (master plan 8.11).

Chat commands such as ``이걸 앞으로 내부 농담으로 만들자`` map to deterministic
acts (create, set visibility, attach relationship, schedule callback, change
emotional role, escalate, resolve, retire). Every change is a state
transition on a stored ``narrative_prop`` plus an append-only prop event;
escalation beyond the current visibility requires explicit confirmation.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

import narrative_contracts as nc
import memory_windows
import narrative_state

_QUOTED = re.compile(r"[\"“‘'「『]([^\"”’'」』]{1,30})[\"”’'」』]")
_BEFORE_KEYWORD = re.compile(r"([가-힣A-Za-z0-9]{2,20})\s*(?:얘기|이야기|농담|별명|소재|사건|약속|내기|의식|루틴|건)")
_TYPE_HINTS = [
    ("food", re.compile(r"햄버거|버거|라멘|치킨|피자|김밥|초밥|커피|음식|식사|도시락|간식|빵")),
    ("nickname", re.compile(r"별명|호칭|이름으로\s*부르")),
    ("promise", re.compile(r"약속")),
    ("bet", re.compile(r"내기|걸었|건다")),
    ("ritual", re.compile(r"루틴|의식|징크스|매번")),
    ("gift", re.compile(r"선물|기념품")),
    ("object", re.compile(r"글러브|배트|모자|신발|스파이크|장갑|물건")),
    ("tension", re.compile(r"긴장|갈등|식단|다툼")),
    ("joke", re.compile(r"농담|드립|장난|웃긴")),
    ("failure_memory", re.compile(r"실패|실책|패배|악몽")),
]
_VISIBILITY_HINTS = [
    ("private", re.compile(r"우리\s*둘|둘만|혼자|비밀|아무한테도")),
    ("clubhouse", re.compile(r"팀\s*안|라커룸|선수들끼리|우리끼리|팀\s*내부|클럽하우스")),
    ("club", re.compile(r"구단\s*안|구단까지|스태프까지")),
    ("local", re.compile(r"팬들까지|팬한테|지역|응원석")),
    ("national", re.compile(r"전국|언론까지|기사로|공개적으로")),
    ("international", re.compile(r"해외|전\s*세계|SNS", re.I)),
]
_TRIGGER_HINTS = [
    ("SP.GAME.BAT.HOME_RUN", re.compile(r"홈런")),
    ("SP.GAME.POST.WIN", re.compile(r"이기|승리|이긴")),
    ("SP.GAME.POST.LOSS", re.compile(r"지면|패배|진\s*날")),
    ("SP.GAME.PITCH", re.compile(r"등판|선발")),
    ("SP.STANDINGS.TITLE", re.compile(r"우승")),
    ("SP.MILESTONE", re.compile(r"기록|마일스톤")),
    ("SP.RELATION.MEAL", re.compile(r"회식|식사")),
    ("SP.GAME.POST", re.compile(r"다음\s*경기|경기\s*뒤|경기\s*후")),
]
_ROLE_HINTS = [
    ("symbolic", re.compile(r"진지|무겁|상징|의미")),
    ("comforting", re.compile(r"위로|따뜻|화해|다독")),
    ("provocative", re.compile(r"도발|자극|긴장|신경전")),
    ("embarrassing", re.compile(r"창피|민망|부끄")),
    ("comic", re.compile(r"웃기|가볍|농담|유쾌")),
]

VISIBILITY_LABELS = {"private": "우리 둘만", "clubhouse": "팀 내부", "club": "구단 내부", "local": "지역 팬", "national": "전국 공개", "international": "해외까지"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_prop_name(text: str) -> str | None:
    value = str(text or "")
    match = _QUOTED.search(value)
    if match:
        return match.group(1).strip()
    match = _BEFORE_KEYWORD.search(value)
    if match:
        name = match.group(1).strip()
        for particle in ("이라는", "라는", "은", "는", "이", "가", "을", "를", "의"):
            if name.endswith(particle) and len(name) > len(particle) + 1:
                name = name[: -len(particle)]
        if name not in ("이걸", "그걸", "저걸", "이번", "그거", "이거", "앞으로", "다음", "오늘", "이제", "이것", "그것"):
            return name
    return None


def guess_type(text: str, name: str | None) -> str:
    haystack = f"{name or ''} {text or ''}"
    for prop_type, pattern in _TYPE_HINTS:
        if pattern.search(haystack):
            return prop_type
    return "joke"


def parse_visibility(text: str) -> str | None:
    for level, pattern in _VISIBILITY_HINTS:
        if pattern.search(str(text or "")):
            return level
    return None


def parse_trigger(text: str) -> str:
    for trigger, pattern in _TRIGGER_HINTS:
        if pattern.search(str(text or "")):
            return trigger
    return "next_scene"


def parse_roles(text: str) -> list[str]:
    roles = [role for role, pattern in _ROLE_HINTS if pattern.search(str(text or ""))]
    if re.search(r"웃기지\s*말고|농담\s*말고", str(text or "")):
        roles = [role for role in roles if role != "comic"] or ["symbolic"]
    return roles


def resolve_prop(state: dict, text: str, *, last_prop_id: str | None = None) -> dict | None:
    props = {key: row for key, row in (state.get("narrative_props") or {}).items() if isinstance(row, dict)}
    haystack = str(text or "")
    best = None
    for row in props.values():
        names = [str(row.get("name") or "")] + [str(alias) for alias in row.get("aliases") or []]
        if any(name and name in haystack for name in names):
            if best is None or str(row.get("last_used_date") or "") > str(best.get("last_used_date") or ""):
                best = row
    if best is not None:
        return best
    if last_prop_id and last_prop_id in props:
        return props[last_prop_id]
    if re.search(r"이걸|그걸|이\s*(?:소재|농담|별명)|그\s*(?:소재|농담|별명)|이번에는|이제\s*그만", haystack):
        active = [row for row in props.values() if row.get("state") != "retired"]
        if active:
            return sorted(active, key=lambda row: str(row.get("last_used_date") or ""))[-1]
    return None


def _prop_event(state: dict, prop: dict, kind: str, *, game_date: str, note: str = "", details: dict | None = None) -> dict:
    row = {"prop_id": prop.get("prop_id"), "kind": kind, "state": prop.get("state"), "game_date": game_date, "note": note, "details": copy.deepcopy(details or {}), "at": _now()}
    events = state.get("narrative_prop_events")
    if not isinstance(events, list):
        events = []
        state["narrative_prop_events"] = events
    events.append(row)
    return row


def handle_prop_act(
    state: dict,
    *,
    act: str,
    text: str,
    understanding: dict,
    game_date: str,
    universe_id: str,
    protagonist_id: object,
    last_prop_id: str | None = None,
    confirm: bool = False,
    lifetime_games: object = None,
) -> dict:
    """Interpret one prop-control act. Returns the interpretation and the change made.

    Consequential escalations (widening visibility beyond the clubhouse or
    the ``prop_escalate`` act) are returned as proposals until ``confirm`` is
    true; every other change is applied immediately and is append-only.
    """
    props = state.setdefault("narrative_props", {})
    name = parse_prop_name(text)
    prop = resolve_prop(state, text, last_prop_id=last_prop_id) if act != "prop_create" or not name else None
    if act == "prop_create":
        if prop is None and not name:
            return {"act": act, "applied": False, "requires_clarification": True, "message": "어떤 소재를 남길지 이름을 알려 줘. 예: ‘햄버거 얘기를 내부 농담으로 만들자’."}
        if prop is not None and name and name != prop.get("name"):
            prop = None
        if prop is None:
            prop = nc.new_prop(universe_id=universe_id, protagonist_id=protagonist_id, name=name, prop_type=guess_type(text, name), game_date=game_date, participants=[understanding.get("target")] if understanding.get("target") and understanding.get("target") != "self" else [], emotional_roles=parse_roles(text) or ["comic"], visibility=parse_visibility(text) or "clubhouse", escalation_ceiling=parse_visibility(text) or "clubhouse")
            props[prop["prop_id"]] = prop
            requested_lifetime = memory_windows.normalize_lifetime_games(
                lifetime_games,
                text=text,
            )
            if requested_lifetime is not None:
                memory_windows.attach_lifetime(
                    state,
                    prop,
                    requested_lifetime,
                    record_kind="narrative_prop",
                    record_id=prop["prop_id"],
                    label=prop["name"],
                    game_date=game_date,
                    universe_id=universe_id,
                    protagonist_id=protagonist_id,
                )
            _prop_event(state, prop, "created", game_date=game_date, note=text)
            nc.transition_prop(prop, "establish", game_date=game_date, note="user established the motif")
            _prop_event(state, prop, "transition", game_date=game_date, note="seed -> establish")
            lifetime_label = f" 자동 재등장은 {requested_lifetime}경기로 제한했다." if requested_lifetime else ""
            return {"act": act, "applied": True, "prop": prop, "message": f"‘{prop['name']}’을(를) {VISIBILITY_LABELS.get(prop['visibility'], prop['visibility'])} 소재로 등록했다.{lifetime_label}", "change": {"state": prop["state"], "visibility": prop["visibility"], "type": prop["prop_type"], "lifetime_games": requested_lifetime}}
        return {"act": act, "applied": False, "prop": prop, "message": f"‘{prop['name']}’은(는) 이미 등록돼 있다.", "change": {}}
    if prop is None:
        return {"act": act, "applied": False, "requires_clarification": True, "message": "어느 소재를 말하는지 찾지 못했다. 이름을 함께 말해 줘."}
    if act == "prop_set_visibility":
        level = parse_visibility(text)
        if not level:
            return {"act": act, "applied": False, "prop": prop, "requires_clarification": True, "message": "어디까지 공개할지 알려 줘: 우리 둘만, 팀 내부, 구단, 지역 팬, 전국."}
        widening = nc.visibility_rank(level) > nc.visibility_rank(prop.get("visibility"))
        if widening and nc.visibility_rank(level) > nc.visibility_rank("clubhouse"):
            # Widening beyond the clubhouse is an escalation beat (8.11): it
            # needs explicit confirmation and then moves the beat machine.
            if not confirm:
                return {"act": act, "applied": False, "prop": prop, "requires_confirmation": True, "proposal": {"proposal_id": f"prop:{prop['prop_id']}:escalate:{level}", "kind": "prop_escalation", "prop_id": prop["prop_id"], "visibility": level, "label": f"‘{prop['name']}’을(를) {VISIBILITY_LABELS.get(level, level)}까지 공개"}, "message": f"‘{prop['name']}’을(를) {VISIBILITY_LABELS.get(level, level)}까지 넓히는 건 되돌리기 어렵다. 확정할까?"}
            return escalate(state, prop, level, game_date=game_date, note=text)
        before = prop.get("visibility")
        prop["visibility"] = level
        prop["escalation_ceiling"] = level if nc.visibility_rank(level) > nc.visibility_rank(prop.get("escalation_ceiling")) else prop.get("escalation_ceiling")
        prop["last_used_date"] = game_date
        _prop_event(state, prop, "visibility", game_date=game_date, note=text, details={"from": before, "to": level})
        return {"act": act, "applied": True, "prop": prop, "message": f"‘{prop['name']}’의 범위를 {VISIBILITY_LABELS.get(level, level)}(으)로 정했다.", "change": {"visibility": level}}
    if act == "prop_attach_relationship":
        target = understanding.get("target") or (prop.get("participants") or [None])[0]
        if not target or target == "self":
            return {"act": act, "applied": False, "prop": prop, "requires_clarification": True, "message": "누구와의 관계에 걸지 알려 줘."}
        roles = parse_roles(text) or ["provocative"]
        effect = {"target": target, "dimension": "tension", "delta": 0.2, "game_date": game_date}
        prop.setdefault("relationship_effects", []).append(effect)
        if target not in prop.get("participants", []):
            prop.setdefault("participants", []).append(target)
        for role in roles:
            if role not in prop.get("emotional_roles", []):
                prop.setdefault("emotional_roles", []).append(role)
        prop["last_used_date"] = game_date
        narrative_state.apply_relationship(state, target=target, deltas={"tension": 0.2, "familiarity": 0.05}, game_date=game_date, event_id=None, explanation=f"‘{prop['name']}’ 소재가 {target}과의 긴장 요소로 연결됨", memory_id=prop["prop_id"])
        if prop.get("state") in ("establish", "callback", "variation"):
            nc.transition_prop(prop, "variation" if prop.get("state") != "variation" else "callback", game_date=game_date, note="relationship attached")
        _prop_event(state, prop, "relationship", game_date=game_date, note=text, details=effect)
        return {"act": act, "applied": True, "prop": prop, "message": f"‘{prop['name']}’이(가) {target}과의 긴장 요소가 됐다.", "change": {"relationship": effect, "state": prop["state"]}}
    if act == "prop_schedule_callback":
        if re.search(r"다음|나중에|때\s", text or "") and not re.search(r"오늘", text or ""):
            trigger = parse_trigger(text)
            rules = prop.setdefault("recurrence_rules", {"min_days_between_callbacks": 1, "trigger_events": []})
            rules.setdefault("trigger_events", []).append({"event_type": trigger, "scheduled_on": game_date, "fired": False})
            prop["last_used_date"] = game_date
            _prop_event(state, prop, "callback_scheduled", game_date=game_date, note=text, details={"trigger": trigger})
            return {"act": act, "applied": True, "prop": prop, "message": f"‘{prop['name']}’을(를) 다음 계기({trigger})에 다시 꺼내도록 예약했다.", "change": {"trigger": trigger}}
        return callback_now(state, prop, game_date=game_date, note=text)
    if act == "prop_change_role":
        roles = parse_roles(text) or ["symbolic"]
        before = list(prop.get("emotional_roles") or [])
        prop["emotional_roles"] = roles
        prop["last_used_date"] = game_date
        if prop.get("state") in ("establish", "callback", "escalation", "rediscovered"):
            try:
                nc.transition_prop(prop, "variation" if prop.get("state") != "escalation" else "reversal", game_date=game_date, note="emotional role changed")
            except ValueError:
                pass
        _prop_event(state, prop, "role", game_date=game_date, note=text, details={"from": before, "to": roles})
        return {"act": act, "applied": True, "prop": prop, "message": f"‘{prop['name']}’의 분위기를 {', '.join(roles)}(으)로 바꿨다.", "change": {"emotional_roles": roles, "state": prop["state"]}}
    if act == "prop_escalate":
        level = parse_visibility(text) or "local"
        if not confirm:
            return {"act": act, "applied": False, "prop": prop, "requires_confirmation": True, "proposal": {"proposal_id": f"prop:{prop['prop_id']}:escalate:{level}", "kind": "prop_escalation", "prop_id": prop["prop_id"], "visibility": level, "label": f"‘{prop['name']}’ 확대 ({VISIBILITY_LABELS.get(level, level)})"}, "message": f"‘{prop['name']}’을(를) {VISIBILITY_LABELS.get(level, level)}까지 퍼뜨리는 건 되돌릴 수 없다. 확정할까?"}
        return escalate(state, prop, level, game_date=game_date, note=text)
    if act == "prop_retire":
        return retire(state, prop, game_date=game_date, note=text)
    return {"act": act, "applied": False, "prop": prop, "message": "알 수 없는 소재 명령이다."}


def callback_now(state: dict, prop: dict, *, game_date: str, note: str = "", event_id: str | None = None) -> dict:
    current = prop.get("state")
    target = "rediscovered" if current == "dormant" else "callback"
    if current == "retired":
        return {"act": "prop_callback", "applied": False, "prop": prop, "message": f"‘{prop['name']}’은(는) 이미 정리된 소재다. 다시 살리려면 새로 등록해야 한다."}
    try:
        nc.transition_prop(prop, target, game_date=game_date, event_id=event_id, note=note)
    except ValueError:
        if current == "seed":
            nc.transition_prop(prop, "establish", game_date=game_date, note=note)
            nc.transition_prop(prop, "callback", game_date=game_date, event_id=event_id, note=note)
        else:
            return {"act": "prop_callback", "applied": False, "prop": prop, "message": f"‘{prop['name']}’은(는) 지금 상태({prop.get('state')})에서 다시 꺼낼 수 없다."}
    _prop_event(state, prop, "callback", game_date=game_date, note=note, details={"event_id": event_id})
    return {"act": "prop_callback", "applied": True, "prop": prop, "message": f"‘{prop['name']}’을(를) 오늘 다시 꺼냈다.", "change": {"state": prop["state"]}, "scene_event_type": "SP.USER.PROP.CALLBACK"}


def escalate(state: dict, prop: dict, level: str, *, game_date: str, note: str = "", event_id: str | None = None) -> dict:
    before = prop.get("visibility")
    try:
        nc.transition_prop(prop, "escalation", game_date=game_date, event_id=event_id, note=note)
    except ValueError:
        if prop.get("state") == "seed":
            nc.transition_prop(prop, "establish", game_date=game_date, note=note)
            nc.transition_prop(prop, "callback", game_date=game_date, note=note)
            nc.transition_prop(prop, "escalation", game_date=game_date, event_id=event_id, note=note)
        elif prop.get("state") == "establish":
            nc.transition_prop(prop, "callback", game_date=game_date, note=note)
            nc.transition_prop(prop, "escalation", game_date=game_date, event_id=event_id, note=note)
        else:
            return {"act": "prop_escalate", "applied": False, "prop": prop, "message": f"‘{prop['name']}’은(는) 지금 상태({prop.get('state')})에서 확대할 수 없다."}
    prop["visibility"] = level if nc.visibility_rank(level) > nc.visibility_rank(before) else before
    prop["escalation_ceiling"] = prop["visibility"]
    _prop_event(state, prop, "escalation", game_date=game_date, note=note, details={"from": before, "to": prop["visibility"]})
    return {"act": "prop_escalate", "applied": True, "prop": prop, "message": f"‘{prop['name']}’이(가) {VISIBILITY_LABELS.get(prop['visibility'], prop['visibility'])}까지 퍼졌다.", "change": {"state": prop["state"], "visibility": prop["visibility"]}, "scene_event_type": "SP.USER.PROP.ESCALATION"}


def retire(state: dict, prop: dict, *, game_date: str, note: str = "") -> dict:
    if prop.get("state") == "retired":
        return {"act": "prop_retire", "applied": False, "prop": prop, "message": f"‘{prop['name']}’은(는) 이미 정리됐다."}
    delivered = False
    if prop.get("state") in ("callback", "variation", "escalation", "reversal", "rediscovered"):
        nc.transition_prop(prop, "payoff", game_date=game_date, note="closed as a good memory")
        delivered = True
    nc.transition_prop(prop, "retired", game_date=game_date, note=note)
    _prop_event(state, prop, "retired", game_date=game_date, note=note, details={"payoff_delivered": delivered})
    return {"act": "prop_retire", "applied": True, "prop": prop, "message": f"‘{prop['name']}’은(는) 좋은 기억으로 정리됐다.", "change": {"state": "retired", "payoff_state": prop.get("payoff_state")}, "scene_event_type": "SP.USER.PROP.PAYOFF" if delivered else "SP.USER.PROP.RETIRED"}


def _trigger_matches(trigger: dict, event_type: str | None, *, game_date: str | None = None, source_hash: str | None = None) -> bool:
    if trigger.get("fired") or not event_type:
        return False
    if game_date and str(trigger.get("scheduled_on") or "") > game_date:
        return False
    if source_hash and trigger.get("after_snapshot_hash") == source_hash:
        return False
    wanted = str(trigger.get("event_type") or "")
    return bool(wanted and (event_type == wanted or event_type.startswith(wanted + ".") or wanted == "next_scene"))


def due_callbacks(state: dict, event_type: str | None, *, game_date: str | None = None, source_hash: str | None = None) -> list[dict]:
    """Props whose scheduled trigger matches a committed event type."""
    rows = []
    for prop in (state.get("narrative_props") or {}).values():
        if not isinstance(prop, dict) or prop.get("state") == "retired" or not memory_windows.is_automatic_retrieval_active(prop):
            continue
        for trigger in (prop.get("recurrence_rules") or {}).get("trigger_events") or []:
            if _trigger_matches(trigger, event_type, game_date=game_date, source_hash=source_hash):
                rows.append(prop)
                break
    return rows


def fire_callback(state: dict, prop: dict, event_type: str, *, game_date: str, event_id: str | None, source_hash: str | None = None) -> dict:
    for trigger in (prop.get("recurrence_rules") or {}).get("trigger_events") or []:
        if _trigger_matches(trigger, event_type, game_date=game_date, source_hash=source_hash):
            result = callback_now(state, prop, game_date=game_date, note=f"scheduled callback fired by {event_type}", event_id=event_id)
            if result.get("applied"):
                trigger["fired"] = True
                trigger["fired_on"] = game_date
                trigger["fired_by"] = event_id
            return result
    return {"applied": False, "prop": prop}


def summary(prop: dict) -> dict:
    return {
        "prop_id": prop.get("prop_id"),
        "name": prop.get("name"),
        "type": prop.get("prop_type"),
        "state": prop.get("state"),
        "visibility": prop.get("visibility"),
        "visibility_label": VISIBILITY_LABELS.get(str(prop.get("visibility")), str(prop.get("visibility"))),
        "emotional_roles": list(prop.get("emotional_roles") or []),
        "participants": list(prop.get("participants") or []),
        "origin_date": prop.get("origin_date"),
        "last_used_date": prop.get("last_used_date"),
        "callbacks": len(prop.get("callbacks") or []),
        "payoff_state": prop.get("payoff_state"),
        "pending_triggers": [row for row in (prop.get("recurrence_rules") or {}).get("trigger_events") or [] if not row.get("fired")],
        "lifetime": memory_windows.public_policy(prop.get("retrieval_lifetime")),
    }
