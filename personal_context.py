"""World-bound player background, preference, and hobby memory.

Context lives under the existing protagonist row in ``world_entities``.  Each
edit appends an immutable revision snapshot; retirement only changes automatic
retrieval and never deletes the prior versions.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

import narrative_contracts as nc
import world_identity

VERSION = "1.0.0"

BASIS = {
    "authored_background": {
        "kind": "background",
        "label": "세계선 배경",
        "evidence_class": "fictional_intervention",
    },
    "user_preference": {
        "kind": "preference",
        "label": "선수 취향",
        "evidence_class": "user_confirmed",
    },
    "fictional_experience": {
        "kind": "hobby",
        "label": "취미·서사상 경험",
        "evidence_class": "fictional_intervention",
    },
    "verified_game_observation": {
        "kind": "game_observation",
        "label": "검증된 게임 관찰",
        "evidence_class": "save_verified",
    },
}

PROFICIENCY = {
    "curious": "관심 단계",
    "beginner": "입문",
    "familiar": "익숙함",
    "skilled": "숙련",
    "expert": "매우 숙련",
}

PUBLIC_VISIBILITY = {"private": "private", "clubhouse": "clubhouse", "public": "national"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date(value: object) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("선수 맥락 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return text


def _text(value: object, *, label: str, limit: int, required: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label}은(는) 텍스트로 입력해 주세요.")
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) > limit or (required and not text):
        suffix = f"1~{limit}자" if required else f"{limit}자 이하"
        raise ValueError(f"{label}은(는) {suffix}로 입력해 주세요.")
    return text


def _store(entity: dict, *, create: bool) -> dict:
    value = entity.get("personal_context")
    if isinstance(value, dict):
        items = value.get("items")
        events = value.get("events")
        if isinstance(items, dict) and isinstance(events, list):
            return value
    if not create:
        return {"schema_version": 1, "items": {}, "events": []}
    value = {"schema_version": 1, "items": {}, "events": []}
    entity["personal_context"] = value
    return value


def _protagonist_row(state: dict, universe_id: str, protagonist_id: object) -> dict | None:
    ident = world_identity.protagonist_entity_id(universe_id, protagonist_id)
    row = (state.get("world_entities") or {}).get(ident)
    if not isinstance(row, dict):
        return None
    if str(row.get("universe_id")) != str(universe_id) or str(row.get("protagonist_id")) != str(protagonist_id):
        return None
    return row


def _state_snapshot(row: dict) -> dict:
    return {
        "context_id": row["context_id"],
        "basis": row["basis"],
        "kind": row["kind"],
        "label": row["label"],
        "detail": row["detail"],
        "proficiency": row.get("proficiency"),
        "visibility": row["visibility"],
        "remote_allowed": bool(row.get("remote_allowed")),
        "evidence_class": row["evidence_class"],
        "source_event_ids": list(row.get("source_event_ids") or []),
        "status": row["status"],
        "valid_from": row["valid_from"],
        "retired_on": row.get("retired_on"),
    }


def _append_revision(store: dict, row: dict, *, operation: str, game_date: str) -> None:
    revision = int(row.get("revision") or 1)
    record = {
        "context_id": row["context_id"],
        "revision": revision,
        "operation": operation,
        "effective_on": _date(game_date),
        "state": _state_snapshot(row),
        "at": _now(),
    }
    row.setdefault("revisions", []).append(copy.deepcopy(record))
    store.setdefault("events", []).append(record)


def _validate_origin(payload: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> None:
    origin = payload.get("world_origin")
    if origin is None:
        return
    expected = {
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "game_date": str(game_date),
    }
    if not isinstance(origin, dict) or any(str(origin.get(key) or "") != value for key, value in expected.items()):
        raise ValueError("선수 맥락을 편집하는 동안 선수·세계선·날짜가 바뀌었습니다. 다시 불러와 주세요.")


def _visibility(value: object) -> str:
    raw = str(value or "private")
    if raw not in PUBLIC_VISIBILITY:
        raise ValueError("선수 맥락 공개 범위를 다시 선택해 주세요.")
    return PUBLIC_VISIBILITY[raw]


def _new_id(store: dict, universe_id: str, protagonist_id: object, basis: str, label: str) -> str:
    serial = len(store.get("events") or []) + 1
    while True:
        ident = f"context:{nc.stable_id(universe_id, protagonist_id, basis, label, serial, length=20)}"
        if ident not in store.get("items", {}):
            return ident
        serial += 1


def upsert_user_context(
    state: dict,
    payload: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    player_label: str,
    game_date: str,
) -> dict:
    """Create or revise user-authored context; verified claims are excluded."""
    if not isinstance(payload, dict):
        raise ValueError("선수 맥락 입력 형식이 올바르지 않습니다.")
    _validate_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    if payload.get("source_event_ids"):
        raise ValueError("사용자 입력으로 검증 사실의 출처 연결을 만들 수 없습니다.")
    basis = str(payload.get("basis") or "authored_background")
    if basis not in BASIS or basis == "verified_game_observation":
        raise ValueError("사용자가 기록할 수 있는 배경·취향·취미 유형을 선택해 주세요.")
    label = _text(payload.get("label"), label="맥락 이름", limit=80, required=True)
    detail = _text(payload.get("detail"), label="맥락 설명", limit=800, required=True)
    visibility = _visibility(payload.get("visibility"))
    remote_allowed = payload.get("remote_allowed") is True
    if remote_allowed and visibility != "national":
        raise ValueError("외부 AI 전달 허용은 공개로 지정한 맥락에서만 켤 수 있습니다.")
    proficiency = str(payload.get("proficiency") or "") or None
    if basis == "fictional_experience":
        proficiency = proficiency or "familiar"
        if proficiency not in PROFICIENCY:
            raise ValueError("취미의 서사상 숙련 단계를 선택해 주세요.")
    elif proficiency:
        raise ValueError("숙련 단계는 취미·서사상 경험에만 기록할 수 있습니다.")

    entity = world_identity.ensure_protagonist_entity(
        state,
        universe_id=universe_id,
        protagonist_id=protagonist_id,
        label=player_label,
        game_date=game_date,
    )
    store = _store(entity, create=True)
    context_id = str(payload.get("context_id") or "")
    if context_id:
        row = store["items"].get(context_id)
        if not isinstance(row, dict):
            raise ValueError("수정할 선수 맥락을 찾지 못했습니다.")
        if row.get("status") != "active":
            raise ValueError("보관된 선수 맥락은 수정할 수 없습니다. 새 항목으로 기록해 주세요.")
        try:
            expected = int(payload.get("expected_revision"))
        except (TypeError, ValueError):
            raise ValueError("현재 맥락 리비전을 확인해 주세요.") from None
        if expected != int(row.get("revision") or 1):
            raise ValueError("다른 창에서 선수 맥락이 바뀌었습니다. 현재 내용을 다시 불러와 주세요.")
        if basis != row.get("basis"):
            raise ValueError("맥락 유형은 이력을 보존하기 위해 바꿀 수 없습니다. 새 항목으로 기록해 주세요.")
        row.update(
            label=label,
            detail=detail,
            proficiency=proficiency,
            visibility=visibility,
            remote_allowed=remote_allowed,
            revision=expected + 1,
            updated_at=_now(),
        )
        operation = "updated"
    else:
        context_id = _new_id(store, universe_id, protagonist_id, basis, label)
        row = {
            "schema_version": 1,
            "context_id": context_id,
            "universe_id": str(universe_id),
            "protagonist_id": str(protagonist_id),
            "basis": basis,
            "kind": BASIS[basis]["kind"],
            "label": label,
            "detail": detail,
            "proficiency": proficiency,
            "visibility": visibility,
            "remote_allowed": remote_allowed,
            "evidence_class": BASIS[basis]["evidence_class"],
            "source_event_ids": [],
            "status": "active",
            "valid_from": _date(game_date),
            "retired_on": None,
            "revision": 1,
            "revisions": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        store["items"][context_id] = row
        operation = "created"
    _append_revision(store, row, operation=operation, game_date=game_date)
    entity["updated_at"] = _now()
    return copy.deepcopy(row)


def retire_user_context(
    state: dict,
    payload: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
) -> dict:
    _validate_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    entity = _protagonist_row(state, universe_id, protagonist_id)
    store = _store(entity or {}, create=False)
    row = store["items"].get(str(payload.get("context_id") or ""))
    if not isinstance(row, dict):
        raise ValueError("보관할 선수 맥락을 찾지 못했습니다.")
    try:
        expected = int(payload.get("expected_revision"))
    except (TypeError, ValueError):
        raise ValueError("현재 맥락 리비전을 확인해 주세요.") from None
    if expected != int(row.get("revision") or 1):
        raise ValueError("다른 창에서 선수 맥락이 바뀌었습니다. 현재 내용을 다시 불러와 주세요.")
    if row.get("status") == "retired":
        return copy.deepcopy(row)
    row.update(status="retired", retired_on=_date(game_date), revision=expected + 1, updated_at=_now())
    _append_revision(store, row, operation="retired", game_date=game_date)
    if entity:
        entity["updated_at"] = _now()
    return copy.deepcopy(row)


def record_verified_observation(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    player_label: str,
    game_date: str,
    label: str,
    detail: str,
    source_fact_ids: list[str],
) -> dict:
    """Record a game observation only when every source fact is save-verified."""
    fact_registry = state.get("fact_registry") or {}
    fact_ids = list(dict.fromkeys(str(value) for value in source_fact_ids if str(value)))
    if not fact_ids or any(
        not isinstance(fact_registry.get(fact_id), dict)
        or nc.evidence_class_of(fact_registry[fact_id].get("evidence_class")) != "save_verified"
        for fact_id in fact_ids
    ):
        raise ValueError("검증된 게임 관찰은 save_verified 사실 출처가 필요합니다.")
    entity = world_identity.ensure_protagonist_entity(
        state,
        universe_id=universe_id,
        protagonist_id=protagonist_id,
        label=player_label,
        game_date=game_date,
    )
    store = _store(entity, create=True)
    context_id = _new_id(store, universe_id, protagonist_id, "verified_game_observation", label)
    row = {
        "schema_version": 1,
        "context_id": context_id,
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "basis": "verified_game_observation",
        "kind": "game_observation",
        "label": _text(label, label="관찰 이름", limit=80, required=True),
        "detail": _text(detail, label="관찰 설명", limit=800, required=True),
        "proficiency": None,
        "visibility": "private",
        "remote_allowed": False,
        "evidence_class": "save_verified",
        "source_event_ids": fact_ids,
        "status": "active",
        "valid_from": _date(game_date),
        "retired_on": None,
        "revision": 1,
        "revisions": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    store["items"][context_id] = row
    _append_revision(store, row, operation="verified_observation", game_date=game_date)
    entity["updated_at"] = _now()
    return copy.deepcopy(row)


def _as_of(row: dict, game_date: str) -> dict | None:
    eligible = [
        revision
        for revision in (row.get("revisions") or [])
        if isinstance(revision, dict)
        and str(revision.get("effective_on") or "") <= game_date
        and isinstance(revision.get("state"), dict)
    ]
    if eligible:
        chosen = max(eligible, key=lambda revision: int(revision.get("revision") or 0))
        result = copy.deepcopy(chosen["state"])
        result["revision"] = int(chosen.get("revision") or 1)
        result["history_count"] = len(row.get("revisions") or [])
        return result
    if str(row.get("valid_from") or "9999-99-99") <= game_date:
        result = _state_snapshot(row)
        result["revision"] = int(row.get("revision") or 1)
        result["history_count"] = len(row.get("revisions") or [])
        return result
    return None


def view(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
    include_retired: bool = True,
) -> dict:
    """Return the context state that was valid on exactly ``game_date``."""
    date = _date(game_date)
    entity = _protagonist_row(state, universe_id, protagonist_id)
    store = _store(entity or {}, create=False)
    rows = []
    for raw in store["items"].values():
        if not isinstance(raw, dict):
            continue
        if raw.get("universe_id") != str(universe_id) or raw.get("protagonist_id") != str(protagonist_id):
            continue
        row = _as_of(raw, date)
        if row is None or (not include_retired and row.get("status") != "active"):
            continue
        row["basis_label"] = BASIS.get(row.get("basis"), {}).get("label", row.get("basis"))
        row["proficiency_label"] = PROFICIENCY.get(row.get("proficiency"))
        row["visibility_label"] = {"private": "비공개", "clubhouse": "팀 내부", "national": "공개"}.get(row.get("visibility"), row.get("visibility"))
        rows.append(row)
    rows.sort(key=lambda row: (row.get("status") != "active", row.get("valid_from") or "", row.get("context_id") or ""))
    active = [copy.deepcopy(row) for row in rows if row.get("status") == "active"]
    return {
        "version": VERSION,
        "game_date": date,
        "items": rows,
        "active_items": active,
        "active_count": len(active),
        "retired_count": sum(1 for row in rows if row.get("status") == "retired"),
        "catalog": catalog(),
        "storage_policy": "nested_append_only_revisions",
        "game_ability_writes": False,
    }


def scene_references(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
    query: str,
    audience: str = "private",
    limit: int = 2,
) -> list[dict]:
    rows = view(
        state,
        universe_id=universe_id,
        protagonist_id=protagonist_id,
        game_date=game_date,
        include_retired=False,
    )["active_items"]
    audience = nc.visibility_of(audience)
    if audience == "private":
        allowed_visibility = {"private", "clubhouse", "national"}
    elif audience in {"clubhouse", "club"}:
        allowed_visibility = {"clubhouse", "national"}
    else:
        allowed_visibility = {"national"}
    rows = [row for row in rows if row.get("visibility") in allowed_visibility]
    normalized_query = world_identity.normalize_alias(query)
    selected: list[dict] = []
    for row in rows:
        haystacks = [row.get("label"), row.get("detail")]
        tokens = {
            token
            for text in haystacks
            for token in re.split(r"[^0-9A-Za-z가-힣ぁ-んァ-ン一-龥]+", world_identity.normalize_alias(text))
            if len(token) >= 2
        }
        if any(token in normalized_query for token in tokens):
            selected.append(row)
        if len(selected) >= max(0, min(3, int(limit))):
            break
    if not selected and rows:
        backgrounds = [row for row in rows if row.get("kind") == "background"]
        selected.append(backgrounds[0] if backgrounds else rows[0])
    return [
        {
            "context_id": row["context_id"],
            "revision": row["revision"],
            "kind": row["kind"],
            "label": row["label"],
            "detail": row["detail"],
            "proficiency": row.get("proficiency"),
            "visibility": row["visibility"],
            "evidence_class": row["evidence_class"],
            "source_event_ids": list(row.get("source_event_ids") or []),
        }
        for row in selected[:limit]
    ]


def validate_references(
    state: dict,
    references: list[dict] | None,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
    audience: str,
) -> None:
    """Reject a proposal whose referenced context changed after preview."""
    if not references:
        return
    audience_level = nc.visibility_of(audience)
    if audience_level == "private":
        allowed_visibility = {"private", "clubhouse", "national"}
    elif audience_level in {"clubhouse", "club"}:
        allowed_visibility = {"clubhouse", "national"}
    else:
        allowed_visibility = {"national"}
    current = {
        row["context_id"]: row
        for row in view(
            state,
            universe_id=universe_id,
            protagonist_id=protagonist_id,
            game_date=game_date,
            include_retired=False,
        )["active_items"]
        if row.get("visibility") in allowed_visibility
    }
    for reference in references:
        context_id = str(reference.get("context_id") or "")
        row = current.get(context_id)
        if (
            not isinstance(row, dict)
            or int(row.get("revision") or 0) != int(reference.get("revision") or 0)
            or row.get("kind") != reference.get("kind")
        ):
            raise ValueError("장면을 미리 본 뒤 선수 설정이 바뀌었습니다. 현재 설정으로 다시 제안해 주세요.")


def prompt_section(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
    remote: bool,
    audience: str = "private",
) -> str:
    rows = view(
        state,
        universe_id=universe_id,
        protagonist_id=protagonist_id,
        game_date=game_date,
        include_retired=False,
    )["active_items"]
    audience_level = nc.visibility_of(audience)
    if remote:
        rows = [row for row in rows if row.get("visibility") == "national" and row.get("remote_allowed") is True]
    elif audience_level in {"national", "international"}:
        rows = [row for row in rows if row.get("visibility") == "national"]
    elif audience_level in {"clubhouse", "club", "local"}:
        rows = [row for row in rows if row.get("visibility") in {"clubhouse", "national"}]
    if not rows:
        return ""
    lines = ["[세계선 선수 맥락 — 세이브 능력치가 아닌 설정/취향]"]
    for row in rows[:12]:
        proficiency = f" · 서사상 숙련 {PROFICIENCY[row['proficiency']]}" if row.get("proficiency") in PROFICIENCY else ""
        lines.append(f"- {BASIS.get(row.get('basis'), {}).get('label', row.get('kind'))}: {row['label']} — {row['detail']}{proficiency}")
    lines.append("이 항목은 선수의 게임 능력·성적·수상 사실을 바꾸지 않는다.")
    return "\n".join(lines)[:3200]


def catalog() -> dict:
    return {
        "version": VERSION,
        "basis": [
            {"id": key, "label": value["label"], "kind": value["kind"]}
            for key, value in BASIS.items()
            if key != "verified_game_observation"
        ],
        "proficiency": [{"id": key, "label": value} for key, value in PROFICIENCY.items()],
        "visibility": [
            {"id": "private", "label": "비공개"},
            {"id": "clubhouse", "label": "팀 내부"},
            {"id": "public", "label": "공개"},
        ],
        "remote_policy": "explicit_public_item_only",
        "deletion_policy": "retire_only",
    }
