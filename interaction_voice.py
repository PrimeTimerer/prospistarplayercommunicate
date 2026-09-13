"""Pure, finite authored-expression planning for confirmed private encounters.

Continuity is fictional, never a game friendship value. Only committed events
feed this planner; previews do not consume wording or alter an emotion. Plans
freeze their selected paths and invalidate when the named counterpart advances.
"""

from __future__ import annotations

import copy
import hashlib
import json

VERSION = 1
STORE = "star_interactions"
EVENTS = "star_interaction_events"
WINDOW = 48


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def related_events(state: dict, row: dict, game_date: str) -> list[dict]:
    """Join events to their owned encounter before consulting any memory."""
    related = []
    named = row.get("participant_source") == "user_label" and row.get("participant_key")
    for event in state.get(EVENTS) or []:
        if not isinstance(event, dict) or not event.get("event_id"):
            continue
        owner = (state.get(STORE) or {}).get(event.get("interaction_id"))
        if not isinstance(owner, dict):
            continue
        if any(owner.get(key) != row.get(key) for key in ("universe_id", "protagonist_id")):
            continue
        if any(event.get(key, row.get(key)) != row.get(key) for key in ("universe_id", "protagonist_id")):
            continue
        if not event.get("game_date") or event["game_date"] > game_date:
            continue
        if owner.get("visibility") != row.get("visibility"):
            continue
        same = (owner.get("participant_source") == "user_label" and owner.get("participant_key") == named
                if named else owner.get("interaction_id") == row.get("interaction_id"))
        if same:
            related.append(event)
    return related


def advance(before: dict, beat: str) -> dict:
    result = copy.deepcopy(before)
    if beat == "disagree":
        result["tension"] = min(3, result["tension"] + 1)
        result["repair_pending"] = False
    elif beat == "reconcile" and (result["tension"] or result["guarded"]):
        result["tension"] = max(0, result["tension"] - 1)
        result["guarded"] = False
        result["repair_pending"] = True
    elif beat == "refuse":
        result["guarded"] = True
    elif beat == "confide":
        result["vulnerable"] = True
        result["trust"] = min(3, result["trust"] + 1)
    elif beat in ("agree", "joke"):
        result["warmth"] = min(3, result["warmth"] + 1)
        if beat == "agree" and result["repair_pending"]:
            result["repair_pending"] = False
            result["tension"] = max(0, result["tension"] - 1)
        if beat == "agree" and not result["tension"]:
            result["vulnerable"] = False
            result["guarded"] = False
    elif beat == "fulfill":
        result["trust"] = min(3, result["trust"] + 1)
    # Time, a joke, a gift, a pause, or an unrelated agreement is not forgiveness.
    return result


def context(state: dict, row: dict, game_date: str) -> dict:
    events = related_events(state, row, game_date)
    emotion = {"tension": 0, "trust": 0, "warmth": 0, "vulnerable": False,
               "guarded": False, "repair_pending": False}
    # Legacy rows have no expression metadata. Replay only their explicit beats,
    # never old generated prose, so missing metadata requires no archive rewrite.
    for event in events:
        emotion = advance(emotion, event.get("beat", ""))
    last = events[-1] if events else {}
    previous = (state.get(STORE) or {}).get(last.get("interaction_id")) or {}
    return {"anchor": last.get("event_id"), "emotion": emotion,
            "source_event_ids": [event["event_id"] for event in events[-8:]],
            "previous_date": last.get("game_date"), "previous_topic": previous.get("topic"),
            "previous_interaction_id": last.get("interaction_id"),
            "recent": [event.get("expression") or {} for event in events[-WINDOW:]]}


def mood(emotion: dict) -> str:
    if emotion["tension"]:
        return "strained"
    if emotion["repair_pending"]:
        return "repairing"
    if emotion["guarded"]:
        return "guarded"
    if emotion["vulnerable"]:
        return "vulnerable"
    if emotion["warmth"] >= 2 or emotion["trust"] >= 2:
        return "warm"
    return "neutral"


def node(pack: dict, path: str):
    current = pack
    for key in path.split("/"):
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def plan(state: dict, row: dict, proposal: dict, pack: dict) -> dict:
    ctx = context(state, row, proposal["game_date"])
    beat = proposal["beat"]
    result = {"version": VERSION, "anchor": ctx["anchor"], "selections": {},
              "source_event_ids": ctx["source_event_ids"], "reused_groups": [],
              "emotion_before": ctx["emotion"], "emotion_after": advance(ctx["emotion"], beat),
              "previous_date": ctx["previous_date"], "previous_topic": ctx["previous_topic"]}
    result["mood"] = mood(result["emotion_after"])
    if beat == "share":
        # No private topic, feeling, or continuity marker enters public writing.
        return {"version": VERSION, "anchor": ctx["anchor"], "selections": {},
                "source_event_ids": [], "reused_groups": [], "mood": "public"}

    def choose(group: str, path: str) -> None:
        options = node(pack, path)
        candidates = [f"{path}/{index}" for index in range(len(options))]
        used = {value: i for i, trace in enumerate(ctx["recent"])
                for value in (trace.get("selections") or {}).values()}
        unused = [value for value in candidates if value not in used]
        eligible = unused or [value for value in candidates if used[value] == min(used[v] for v in candidates)]
        if not unused:
            result["reused_groups"].append(group)
        selected = min(eligible, key=lambda value: _digest(proposal["proposal_id"], group, value))
        result["selections"][group] = selected

    primary = (f"actions/{row['action_id']}/openings" if beat == "start" else
               f"{'beats' if row['participant_key'] else 'solo_beats'}/{beat}")
    choose("main", primary)
    if beat not in ("start", "close", "pause", "callback"):
        choose("setting", f"continuity/settings/{row['action_id']}")
    if result["mood"] != "neutral":
        family = "bridges" if row["participant_key"] else "solo_bridges"
        choose("bridge", f"continuity/{family}/{result['mood']}")
    if beat == "start" and ctx["anchor"] and ctx["previous_interaction_id"] != row["interaction_id"]:
        choose("recall", "continuity/returning")
    if row["participant_key"]:
        role = row["participant_role"]
        topic = row["topic"] + " " + (proposal.get("line") or "")
        if beat == "joke" and any(word in topic for word in ("햄버거", "버거")):
            choose("reply", f"continuity/hamburger/{role}")
        elif beat == "start":
            choose("reply", f"voices/{role}")
        else:
            choose("reply", f"continuity/replies/{role}/{beat}")
    return result


def validate(state: dict, row: dict, proposal: dict) -> None:
    expression = proposal.get("expression_plan")
    if expression is None:
        return  # Earlier stored proposals retain their original rendering path.
    if expression.get("version") != VERSION:
        raise ValueError("이 대화 제안의 표현 버전을 읽을 수 없습니다. 다시 제안해 주세요.")
    current = context(state, row, proposal["game_date"])
    if expression.get("anchor") != current["anchor"]:
        raise ValueError("같은 상대와 나눈 다른 대화가 갱신됐습니다. 현재 관계를 바탕으로 다시 제안해 주세요.")


def record(row: dict, proposal: dict, event: dict) -> None:
    expression = proposal.get("expression_plan")
    if not expression:
        return
    event["expression"] = copy.deepcopy(expression)
    if proposal["beat"] != "share":
        row["emotional_continuity"] = {"version": VERSION, "event_id": event["event_id"],
                                       "evidence_class": "fictional_intervention",
                                       **copy.deepcopy(expression["emotion_after"])}
