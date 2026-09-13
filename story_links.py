"""Read-only, provenance-labelled links between saved editorial surfaces."""
from __future__ import annotations

import copy
import json
import re
from datetime import date

import story_desk as desk

CHANNELS = {"story": "서사", "article": "기사", "community": "커뮤니티·SNS", "record": "검증 기록", "context": "상대팀·인물"}
PUBLIC = {"public", "social", "national", "international", "community"}


def valid_day(value):
    try:
        text = str(value)
        return text if date.fromisoformat(text).isoformat() == text else None
    except (TypeError, ValueError):
        return None


def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(filter(None, (text_of(row) for row in value)))
    if isinstance(value, dict):
        return "\n\n".join(text_of(value[key]) for key in
            ("title", "subtitle", "body", "paragraphs", "text", "summary", "response", "comments", "replies")
            if value.get(key))
    return ""


def source(ident, day, channel, title, text, *, visibility="public", remote=True, basis="world_fiction", importance=10):
    value = {"id": str(ident), "date": day, "channel": channel, "title": str(title or CHANNELS.get(channel, channel)),
             "text": str(text), "visibility": visibility, "remote_allowed": remote is True,
             "basis": basis, "importance": importance}
    value["hash"] = desk.digest(value)
    return value


def permitted(row, *, remote=False, audience="private"):
    return (not remote or row.get("remote_allowed") is True) and (audience != "public" or row.get("visibility") in PUBLIC)


def collect(state, context, archives=()):
    """Archive payloads must already be confined to the owning world's files."""
    if str(state.get("world_id")) != context["world_id"]:
        return []
    result = []
    for row in desk.turns(state, context):
        result.append(source("desk:" + row["id"], row["game_date"], row.get("channel", "story"), row["label"],
            "사용자 입력:\n" + row["text"] + ("\n\n작성된 내용:\n" + row["reply"] if row.get("model") else ""),
            visibility=row["visibility"], remote=row.get("remote_allowed") is True, importance=40))
    for day, session in state.get("story_sessions", {}).items():
        if not valid_day(day) or day > context["game_date"]:
            continue
        owner = ((session.get("verified_snapshot") or {}).get("player") or {}).get("id")
        if owner is not None and str(owner) != context["player_id"]:
            continue
        for index, row in enumerate(session.get("turns", [])):
            if row.get("renderer") == "story_desk":
                continue
            visibility = (row.get("input") or {}).get("visibility", "private")
            result.append(source(f"scene:{day}:{row.get('id', index)}", day, "story",
                (row.get("scene") or {}).get("title", "저장된 장면"), text_of(row.get("scene") or {}),
                visibility=visibility, remote=visibility in PUBLIC, importance=35))
            for field, channel in (("media", "article"), ("boards", "community"), ("social", "community"), ("foreign", "community")):
                for number, reaction in enumerate((row.get("reactions") or {}).get(field, [])):
                    result.append(source(f"scene-reaction:{day}:{row.get('id', index)}:{field}:{number}", day, channel,
                        (reaction.get("title") if isinstance(reaction, dict) else None) or "장면 후속 반응", text_of(reaction)))
    for row in state.get("conversation_turns", []):
        day = valid_day(row.get("game_date"))
        if not day or day > context["game_date"] or str(row.get("universe_id")) != context["world_id"] or str(row.get("protagonist_id")) != context["player_id"]:
            continue
        # Legacy chat has no durable visibility/cloud-consent contract.
        # Keep it private/local and exclude unconfirmed event proposals.
        result.append(source("conversation:" + str(row["turn_id"]), day, "story", "이전 대화",
            "사용자 입력:\n" + str(row.get("user_text") or "") + "\n대화 응답 (제안은 사실 아님):\n" + str(row.get("reply_text") or ""),
            visibility="private", remote=False, basis="legacy_conversation", importance=30))
    for day, row in state.get("day_snapshots", {}).items():
        if not valid_day(day) or day > context["game_date"]:
            continue
        snapshot = row.get("verified_snapshot") or {}
        if str((snapshot.get("player") or {}).get("id")) != context["player_id"]:
            continue
        value = {"date": day, "stats": snapshot.get("stats", {}), "event": row.get("event"),
                 "warning": "시즌 누계와 해당 구간 변화는 다르다. 누계를 하루 성적으로 쓰지 않는다."}
        result.append(source("record:" + day, day, "record", "세이브 성적·확인된 변화", json.dumps(value, ensure_ascii=False),
                             basis="save_verified", importance=100))
    for index, row in enumerate(state.get("milestone_ledger", [])):
        day = valid_day(row.get("occurred_on") or row.get("game_date"))
        if day and day <= context["game_date"]:
            result.append(source(f"milestone:{row.get('id', index)}", day, "record", row.get("title", "마일스톤"),
                json.dumps(row, ensure_ascii=False), basis=str(row.get("provenance", "recorded_milestone")), importance=110))
    # The newest archived edition for a date supersedes only the projection,
    # never the immutable files holding earlier feed versions.
    latest = {}
    for payload in archives:
        day = valid_day(payload.get("game_date"))
        if day and day <= context["game_date"]:
            latest[day] = payload
    for day, payload in sorted(latest.items()):
        feed = payload.get("feed") or {}
        for field, channel in (("media", "article"), ("boards", "community"), ("social", "community")):
            for index, row in enumerate(feed.get(field, [])):
                if not isinstance(row, dict):
                    continue
                ident = row.get("id") or row.get("article_id") or row.get("post_id") or str(index)
                result.append(source(f"feed:{day}:{field}:{ident}", day, channel,
                    row.get("title") or row.get("platform") or row.get("outlet") or CHANNELS[channel], text_of(row), importance=25))
    for row in (state.get("chronicle") or {}).get("cinematics", []):
        if desk.owned(row, context):
            result.append(source("cinematic:" + row["id"], row["game_date"], "story", "장문 서사", row["text"],
                                 visibility=row.get("visibility", "private"), remote=row.get("remote_allowed", False), importance=35))
    return sorted((r for r in result if r["text"]), key=lambda r: (r["date"], r["id"]))


def header(row):
    return {key: copy.deepcopy(row.get(key)) for key in ("id", "date", "channel", "title", "visibility", "remote_allowed", "basis", "hash")}


def linked_context(rows, selection, day):
    remote = selection["provider"] == "gemini"
    audience = "public" if selection.get("channel", "story") in {"article", "community"} else "private"
    allowed = [row for row in rows if row["date"] <= day and permitted(row, remote=remote, audience=audience)]
    lookup = {row["id"]: row for row in allowed}
    ids = selection.get("source_ids", [])
    if any(ident not in lookup for ident in ids):
        raise ValueError("연결 자료가 바뀌었거나 공개·Google 전송 범위가 맞지 않습니다. 허용된 자료를 다시 선택해 주세요.")
    chosen = [lookup[ident] for ident in ids]
    candidates = [row for row in allowed if row["id"] not in ids]
    # Explicit links first, then relevant cross-surface context. Selection does
    # not mark a source as public or grant cloud transmission permission.
    candidates.sort(key=lambda r: (desk._score(r["title"] + r["text"], selection["text"]), r["date"], r["importance"]), reverse=True)
    chosen.extend(candidates[:max(0, 8-len(chosen))])
    packets = [{**header(row), "excerpt": row["text"][:1800], "excerpted": len(row["text"]) > 1800} for row in chosen]
    return packets


def split_sources(rows, size=2800):
    """Hash every bounded chunk; successful prefixes survive interrupted jobs."""
    units = []
    for row in rows:
        text = row["text"]
        for offset in range(0, max(1, len(text)), size):
            unit = {**header(row), "id": f"{row['id']}#p{offset // size}", "source_id": row["id"],
                    "text": text[offset:offset+size], "part": offset // size + 1,
                    "parts": max(1, (len(text) + size - 1) // size)}
            # Full-source hashes/part counts change when a suffix is appended;
            # neither may invalidate an unchanged, completed prefix chunk.
            unit["hash"] = desk.digest({k: v for k, v in unit.items() if k not in {"hash", "parts"}})
            units.append(unit)
    return units


def compact(text, limit=1800):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit * 2 // 3] + "\n[중간 생략·원문은 로컬 보관]\n" + text[-limit // 3:]


def named_voices(context_row, *, remote=False):
    if not context_row or (remote and context_row.get("remote_allowed") is not True):
        return {}
    return {"previous_team": context_row.get("previous_team"), "upcoming_team": context_row.get("upcoming_team"),
            "people": copy.deepcopy(context_row.get("people", [])), "basis": "user_supplied_world_reference",
            "warning": "소속·일정은 사용자 제공 참고. 생성 발언은 실제 인터뷰가 아닌 창작 반응이다."}
