"""Stable world-local identities for user-authored story counterparts.

The ledger already owns ``world_entities``.  This module adds a conservative
identity contract inside those existing rows instead of introducing a new
top-level schema.  Old readers therefore keep the nested data intact.

Read helpers are pure.  Mutating helpers are called only by the service while
it owns the per-world state lock.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from datetime import datetime, timezone

import narrative_contracts as nc

VERSION = "1.0.0"
COUNTERPART_KIND = "story_counterpart"
LEGACY_KIND = "user_named_story_participant"
PROTAGONIST_KIND = "world_protagonist"

ROLE_LABELS = {
    "teammate": "동료",
    "rookie": "후배 선수",
    "rival": "라이벌",
    "coach": "코치",
    "manager": "감독",
    "family": "가족",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date(value: object) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("세계선 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return text


def _text(value: object, *, label: str, limit: int, required: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label}은(는) 텍스트로 입력해 주세요.")
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) > limit or (required and not text):
        suffix = f"1~{limit}자" if required else f"{limit}자 이하"
        raise ValueError(f"{label}은(는) {suffix}로 입력해 주세요.")
    return text


def normalize_alias(value: object) -> str:
    """Return a comparison key without changing the displayed spelling."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


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
        raise ValueError("주변 인물을 편집하는 동안 선수·세계선·날짜가 바뀌었습니다. 다시 불러와 주세요.")


def _aliases(value: object, *, limit: int = 24) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        rows = re.split(r"[,\n]", value)
    elif isinstance(value, list):
        rows = value
    else:
        raise ValueError("별명은 쉼표로 구분한 텍스트 또는 목록이어야 합니다.")
    result: list[str] = []
    seen: set[str] = set()
    for raw in rows:
        alias = _text(raw, label="별명", limit=60)
        key = normalize_alias(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(alias)
        if len(result) > limit:
            raise ValueError(f"별명은 최대 {limit}개까지 기록할 수 있습니다.")
    return result


def _entities(state: dict) -> dict:
    value = state.get("world_entities")
    return value if isinstance(value, dict) else {}


def _owned_interaction_keys(state: dict, universe_id: str, protagonist_id: object) -> set[str]:
    return {
        str(row.get("participant_key"))
        for row in (state.get("star_interactions") or {}).values()
        if isinstance(row, dict)
        and row.get("universe_id") == str(universe_id)
        and row.get("protagonist_id") == str(protagonist_id)
        and row.get("participant_key")
    }


def _owned(
    state: dict,
    entity_id: str,
    row: dict,
    *,
    universe_id: str,
    protagonist_id: object,
) -> bool:
    if str(row.get("universe_id") or "") != str(universe_id):
        return False
    owner = row.get("protagonist_id")
    if owner not in (None, ""):
        return str(owner) == str(protagonist_id)
    # Legacy participant rows did not store protagonist_id.  They are visible
    # only when an interaction in this exact world/player already references
    # the immutable entity id.
    return entity_id in _owned_interaction_keys(state, universe_id, protagonist_id)


def _names(row: dict) -> list[str]:
    values = [row.get("label"), row.get("canonical_label")]
    values.extend(row.get("aliases") or [])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = normalize_alias(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _find(
    state: dict,
    entity_id: object,
    *,
    universe_id: str,
    protagonist_id: object,
) -> dict | None:
    ident = str(entity_id or "")
    row = _entities(state).get(ident)
    if not isinstance(row, dict):
        return None
    if row.get("kind") not in (COUNTERPART_KIND, LEGACY_KIND):
        return None
    return row if _owned(state, ident, row, universe_id=universe_id, protagonist_id=protagonist_id) else None


def counterparts(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    include_retired: bool = True,
    game_date: str | None = None,
) -> list[dict]:
    """Project world-local counterpart rows without mutating legacy data."""
    raw_rows: list[tuple[str, dict]] = []
    for entity_id, row in _entities(state).items():
        if not isinstance(row, dict) or row.get("kind") not in (COUNTERPART_KIND, LEGACY_KIND):
            continue
        if not _owned(state, str(entity_id), row, universe_id=universe_id, protagonist_id=protagonist_id):
            continue
        projected = row
        if game_date is not None:
            date = _date(game_date)
            history = [
                item
                for item in (row.get("identity_history") or [])
                if isinstance(item, dict)
                and str(item.get("effective_on") or "") <= date
                and isinstance(item.get("state"), dict)
            ]
            if history:
                chosen = max(history, key=lambda item: int(item.get("revision") or 0))
                projected = copy.deepcopy(row)
                projected.update(copy.deepcopy(chosen["state"]))
                projected["canonical_label"] = projected.get("label")
                projected["identity_revision"] = int(chosen.get("revision") or 1)
            elif row.get("identity_history") or (
                row.get("created_on") and str(row.get("created_on")) > date
            ):
                continue
        if not include_retired and projected.get("status", "active") != "active":
            continue
        raw_rows.append((str(entity_id), projected))

    name_owners: dict[str, set[str]] = {}
    for entity_id, row in raw_rows:
        if row.get("status", "active") != "active":
            continue
        for name in _names(row):
            name_owners.setdefault(normalize_alias(name), set()).add(entity_id)

    result = []
    for entity_id, row in raw_rows:
        names = _names(row)
        ambiguous = [name for name in names if len(name_owners.get(normalize_alias(name), set())) > 1]
        result.append(
            {
                "entity_id": entity_id,
                "label": str(row.get("canonical_label") or row.get("label") or "이름 없는 인물"),
                "aliases": [str(value) for value in (row.get("aliases") or []) if str(value).strip()],
                "roles": [str(value) for value in (row.get("roles") or []) if value in ROLE_LABELS],
                "role_labels": [ROLE_LABELS[value] for value in (row.get("roles") or []) if value in ROLE_LABELS],
                "note": str(row.get("note") or ""),
                "status": str(row.get("status") or "active"),
                "identity_revision": int(row.get("identity_revision") or 1),
                "identity_mode": str(row.get("identity_mode") or "legacy_role_name"),
                "created_on": row.get("created_on"),
                "last_seen_on": row.get("last_seen_on"),
                "event_count": len(row.get("event_links") or []),
                "ambiguous_names": ambiguous,
                "needs_explicit_selection": bool(ambiguous),
                "legacy": row.get("kind") == LEGACY_KIND,
            }
        )
    result.sort(key=lambda row: (row["status"] != "active", normalize_alias(row["label"]), row["entity_id"]))
    return result


def prepare_participant(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    role: str,
    supplied_name: object,
    entity_id: object = None,
) -> dict:
    """Resolve one participant for a pure interaction plan.

    The legacy role+name key is retained for existing callers.  Once two
    entities share a displayed name, a caller must choose an entity id; name
    text alone can never pick one of them silently.
    """
    if role not in ROLE_LABELS:
        raise ValueError("지원하는 주변 인물 역할을 선택해 주세요.")
    name = _text(supplied_name, label="상대 이름", limit=60)
    requested_id = str(entity_id or "").strip()
    if requested_id:
        row = _find(
            state,
            requested_id,
            universe_id=universe_id,
            protagonist_id=protagonist_id,
        )
        if row is None:
            raise ValueError("현재 선수·세계선에 속한 주변 인물을 찾지 못했습니다.")
        if row.get("status", "active") != "active":
            raise ValueError("보관된 주변 인물은 새 만남에 선택할 수 없습니다.")
        if role not in (row.get("roles") or []):
            raise ValueError("선택한 주변 인물의 역할이 현재 만남의 상대 역할과 다릅니다.")
        canonical = str(row.get("canonical_label") or row.get("label") or "").strip()
        if name and normalize_alias(name) not in {normalize_alias(value) for value in _names(row)}:
            raise ValueError("선택한 주변 인물과 입력한 이름이 다릅니다. 현재 목록에서 다시 선택해 주세요.")
        return {
            "participant_key": requested_id,
            "participant_label": canonical or name or ROLE_LABELS[role],
            "participant_source": "world_entity",
            "participant_identity_revision": int(row.get("identity_revision") or 1),
        }

    label = name or ROLE_LABELS[role]
    legacy_id = f"interaction-person:{nc.stable_id(universe_id, protagonist_id, role, normalize_alias(name) or role)}"
    key = normalize_alias(label)
    matches = [
        row
        for row in counterparts(
            state,
            universe_id=universe_id,
            protagonist_id=protagonist_id,
            include_retired=False,
        )
        if key and key in {normalize_alias(value) for value in [row["label"], *row["aliases"]]}
    ]
    if len(matches) > 1:
        raise ValueError("같은 이름을 쓰는 주변 인물이 둘 이상입니다. 목록에서 정확한 인물을 선택해 주세요.")
    if len(matches) == 1 and matches[0]["entity_id"] != legacy_id:
        raise ValueError("이미 등록된 주변 인물 이름입니다. 이름만 입력하지 말고 목록에서 그 인물을 선택해 주세요.")
    return {
        "participant_key": legacy_id,
        "participant_label": label,
        "participant_source": "user_label" if name else "fictional_role",
        "participant_identity_revision": int(matches[0]["identity_revision"]) if matches else 0,
    }


def _history_state(row: dict) -> dict:
    return {
        "label": str(row.get("canonical_label") or row.get("label") or ""),
        "aliases": list(row.get("aliases") or []),
        "roles": list(row.get("roles") or []),
        "note": str(row.get("note") or ""),
        "status": str(row.get("status") or "active"),
    }


def _append_identity_history(row: dict, *, operation: str, game_date: str) -> None:
    revision = int(row.get("identity_revision") or 1)
    row.setdefault("identity_history", []).append(
        {
            "revision": revision,
            "operation": operation,
            "effective_on": _date(game_date),
            "state": _history_state(row),
            "at": _now(),
        }
    )


def create_counterpart(
    state: dict,
    payload: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("주변 인물 입력 형식이 올바르지 않습니다.")
    _validate_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    label = _text(payload.get("canonical_name"), label="인물 이름", limit=60, required=True)
    role = str(payload.get("role") or "teammate")
    if role not in ROLE_LABELS:
        raise ValueError("지원하는 주변 인물 역할을 선택해 주세요.")
    aliases = [value for value in _aliases(payload.get("aliases")) if normalize_alias(value) != normalize_alias(label)]
    note = _text(payload.get("note"), label="인물 메모", limit=500)
    owned_count = len(counterparts(state, universe_id=universe_id, protagonist_id=protagonist_id))
    serial = owned_count + 1
    entities = state.setdefault("world_entities", {})
    while True:
        entity_id = f"counterpart:{nc.stable_id(universe_id, protagonist_id, 'counterpart', serial, normalize_alias(label), length=20)}"
        if entity_id not in entities:
            break
        serial += 1
    row = {
        "schema_version": 1,
        "entity_id": entity_id,
        "kind": COUNTERPART_KIND,
        "universe_id": str(universe_id),
        "protagonist_id": str(protagonist_id),
        "label": label,
        "canonical_label": label,
        "aliases": aliases,
        "roles": [role],
        "note": note,
        "status": "active",
        "identity_mode": "explicit_world_entity",
        "identity_revision": 1,
        "created_on": _date(game_date),
        "last_seen_on": None,
        "event_links": [],
        "identity_history": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _append_identity_history(row, operation="created", game_date=game_date)
    entities[entity_id] = row
    return copy.deepcopy(row)


def update_counterpart(
    state: dict,
    payload: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
) -> dict:
    _validate_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    entity_id = str(payload.get("entity_id") or "")
    row = _find(state, entity_id, universe_id=universe_id, protagonist_id=protagonist_id)
    if row is None:
        raise ValueError("수정할 주변 인물을 찾지 못했습니다.")
    if row.get("status", "active") != "active":
        raise ValueError("보관된 주변 인물은 수정할 수 없습니다.")
    try:
        expected = int(payload.get("expected_revision"))
    except (TypeError, ValueError):
        raise ValueError("현재 인물 리비전을 확인해 주세요.") from None
    if expected != int(row.get("identity_revision") or 1):
        raise ValueError("다른 창에서 인물 정보가 바뀌었습니다. 현재 내용을 다시 불러와 주세요.")
    old_label = str(row.get("canonical_label") or row.get("label") or "")
    new_label = _text(payload.get("canonical_name", old_label), label="인물 이름", limit=60, required=True)
    aliases = _aliases(payload.get("aliases"))
    merged = [*list(row.get("aliases") or [])]
    if normalize_alias(old_label) != normalize_alias(new_label):
        merged.append(old_label)
    merged.extend(aliases)
    row["aliases"] = [value for value in _aliases(merged) if normalize_alias(value) != normalize_alias(new_label)]
    role = str(payload.get("role") or "")
    if role:
        if role not in ROLE_LABELS:
            raise ValueError("지원하는 주변 인물 역할을 선택해 주세요.")
        if role not in row.setdefault("roles", []):
            row["roles"].append(role)
    if "note" in payload:
        row["note"] = _text(payload.get("note"), label="인물 메모", limit=500)
    row.update(
        label=new_label,
        canonical_label=new_label,
        identity_revision=expected + 1,
        updated_at=_now(),
    )
    _append_identity_history(row, operation="updated", game_date=game_date)
    return copy.deepcopy(row)


def retire_counterpart(
    state: dict,
    payload: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
) -> dict:
    _validate_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    entity_id = str(payload.get("entity_id") or "")
    row = _find(state, entity_id, universe_id=universe_id, protagonist_id=protagonist_id)
    if row is None:
        raise ValueError("보관할 주변 인물을 찾지 못했습니다.")
    try:
        expected = int(payload.get("expected_revision"))
    except (TypeError, ValueError):
        raise ValueError("현재 인물 리비전을 확인해 주세요.") from None
    if expected != int(row.get("identity_revision") or 1):
        raise ValueError("다른 창에서 인물 정보가 바뀌었습니다. 현재 내용을 다시 불러와 주세요.")
    if row.get("status", "active") == "retired":
        return copy.deepcopy(row)
    row.update(status="retired", retired_on=_date(game_date), identity_revision=expected + 1, updated_at=_now())
    _append_identity_history(row, operation="retired", game_date=game_date)
    return copy.deepcopy(row)


def ensure_interaction_entity(
    state: dict,
    *,
    entity_id: str,
    label: str,
    role: str,
    universe_id: str,
    protagonist_id: object,
    game_date: str,
    event_id: str,
    source: str,
) -> dict:
    """Attach a committed interaction to its stable identity."""
    entities = state.setdefault("world_entities", {})
    row = entities.get(entity_id)
    if not isinstance(row, dict):
        row = {
            "schema_version": 1,
            "entity_id": entity_id,
            "kind": LEGACY_KIND,
            "universe_id": str(universe_id),
            "protagonist_id": str(protagonist_id),
            "label": label,
            "canonical_label": label,
            "aliases": [],
            "roles": [role] if role in ROLE_LABELS else [],
            "note": "",
            "status": "active",
            "identity_mode": "legacy_role_name" if source != "world_entity" else "explicit_world_entity",
            "identity_revision": 1,
            "created_on": _date(game_date),
            "event_links": [],
            "identity_history": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        _append_identity_history(row, operation="created_from_interaction", game_date=game_date)
        entities[entity_id] = row
    elif not _owned(state, entity_id, row, universe_id=universe_id, protagonist_id=protagonist_id):
        raise ValueError("주변 인물 식별자가 다른 선수 또는 세계선에 속합니다.")
    row.setdefault("protagonist_id", str(protagonist_id))
    row.setdefault("canonical_label", str(row.get("label") or label))
    row.setdefault("aliases", [])
    row.setdefault("roles", [])
    row.setdefault("identity_revision", 1)
    row.setdefault("identity_mode", "legacy_role_name")
    row.setdefault("identity_history", [])
    row.setdefault("event_links", [])
    if role in ROLE_LABELS and role not in row["roles"]:
        row["roles"].append(role)
    if not any(link.get("event_id") == event_id for link in row["event_links"] if isinstance(link, dict)):
        row["event_links"].append(
            {
                "event_id": str(event_id),
                "game_date": _date(game_date),
                "relation": "interaction_participant",
            }
        )
    row["last_seen_on"] = _date(game_date)
    row["updated_at"] = _now()
    return row


def protagonist_entity_id(universe_id: str, protagonist_id: object) -> str:
    return f"protagonist:{nc.stable_id(universe_id, protagonist_id, 'world-protagonist', length=20)}"


def ensure_protagonist_entity(
    state: dict,
    *,
    universe_id: str,
    protagonist_id: object,
    label: str,
    game_date: str,
) -> dict:
    entity_id = protagonist_entity_id(universe_id, protagonist_id)
    entities = state.setdefault("world_entities", {})
    row = entities.get(entity_id)
    if not isinstance(row, dict):
        row = {
            "schema_version": 1,
            "entity_id": entity_id,
            "kind": PROTAGONIST_KIND,
            "universe_id": str(universe_id),
            "protagonist_id": str(protagonist_id),
            "label": _text(label, label="선수 이름", limit=80) or "선수",
            "aliases": [],
            "created_on": _date(game_date),
            "created_at": _now(),
            "updated_at": _now(),
        }
        entities[entity_id] = row
    elif str(row.get("universe_id")) != str(universe_id) or str(row.get("protagonist_id")) != str(protagonist_id):
        raise ValueError("선수 배경 원장이 다른 세계선에 속합니다.")
    return row


def catalog() -> dict:
    return {
        "version": VERSION,
        "roles": [{"id": key, "label": value} for key, value in ROLE_LABELS.items()],
        "identity_policy": "explicit_entity_on_ambiguity",
        "deletion_policy": "retire_only",
    }
