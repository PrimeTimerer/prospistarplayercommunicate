#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline career ledger, NPB reference comparison, and milestone derivation.

The module keeps verified save facts, user-confirmed historical facts, and
bundled NPB reference data visibly separate. It never performs a network
request at runtime.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path


BATTING_COUNTING_STATS = (
    "bat_G", "bat_PA", "bat_AB", "bat_H", "bat_1B", "bat_2B", "bat_3B",
    "bat_HR", "bat_RBI", "bat_R", "bat_SO", "bat_SH", "bat_SF", "bat_BB",
    "bat_HBP", "bat_SB",
)
PITCHING_COUNTING_STATS = (
    "pit_G", "pit_TBF", "pit_H", "pit_K", "pit_W", "pit_L", "pit_SV",
    "pit_HP", "pit_HLD", "pit_CG", "pit_SHO", "pit_BB", "pit_HBP", "pit_R",
    "pit_ER",
)
COUNTING_STATS = (*BATTING_COUNTING_STATS, *PITCHING_COUNTING_STATS)
CANONICAL_STATS = (*COUNTING_STATS, "pit_IP_outs")
STAT_LABELS = {
    "bat_G": "타격 출전",
    "bat_PA": "타석",
    "bat_AB": "타수",
    "bat_H": "안타",
    "bat_1B": "단타",
    "bat_2B": "2루타",
    "bat_3B": "3루타",
    "bat_HR": "홈런",
    "bat_RBI": "타점",
    "bat_R": "득점",
    "bat_SO": "타자 삼진",
    "bat_SH": "희생번트",
    "bat_SF": "희생플라이",
    "bat_BB": "볼넷",
    "bat_HBP": "사구",
    "bat_SB": "도루",
    "pit_G": "등판",
    "pit_TBF": "상대한 타자",
    "pit_H": "피안타",
    "pit_K": "탈삼진",
    "pit_W": "승리",
    "pit_L": "패전",
    "pit_SV": "세이브",
    "pit_HP": "홀드 포인트",
    "pit_HLD": "홀드",
    "pit_CG": "완투",
    "pit_SHO": "완봉",
    "pit_BB": "볼넷 허용",
    "pit_HBP": "사구 허용",
    "pit_R": "실점",
    "pit_ER": "자책점",
    "pit_IP_outs": "투구 이닝",
}
HONOR_LABELS = {
    "japan_series_champion": "일본시리즈 우승",
    "japan_series_mvp": "일본시리즈 MVP",
    "league_champion": "리그 우승",
    "season_mvp": "시즌 MVP",
    "monthly_mvp": "월간 MVP",
    "all_star_selection": "올스타 선정",
    "title": "개인 타이틀",
    "trophy": "트로피·수상",
    "season_record": "시즌 기록 갱신",
    "career_record": "통산 기록 갱신",
    "other": "기타 마일스톤",
}
_INNINGS_RE = re.compile(r"^(\d+)(?:\.([012]))?$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_profile() -> dict:
    return {
        "schema_version": 1,
        "tracking_started_at": None,
        "seasons": [],
        "honors": [],
    }


def migrate_profile(value: object) -> dict:
    profile = empty_profile()
    if isinstance(value, dict):
        profile["tracking_started_at"] = value.get("tracking_started_at")
        if isinstance(value.get("seasons"), list):
            profile["seasons"] = [row for row in value["seasons"] if isinstance(row, dict)]
        if isinstance(value.get("honors"), list):
            profile["honors"] = [row for row in value["honors"] if isinstance(row, dict)]
    return profile


@lru_cache(maxsize=1)
def catalog() -> dict:
    path = Path(__file__).resolve().parent / "data" / "npb_records.json"
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("invalid bundled NPB record catalog")
    return value


def _clean_text(value: object, field: str, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{field}은(는) {limit}자 이하여야 합니다.")
    return text


def _count(value: object, field: str, *, maximum: int = 10_000_000) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field} 값이 올바르지 않습니다.")
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}은(는) 0 이상의 정수여야 합니다.") from exc
    if number < 0 or number > maximum:
        raise ValueError(f"{field}은(는) 0~{maximum} 범위여야 합니다.")
    return number


def innings_to_outs(value: object) -> int:
    """Convert baseball innings notation (for example 153.2) to outs."""
    if value in (None, ""):
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ValueError("투구 이닝은 0 이상이어야 합니다.")
        return value * 3
    text = str(value).strip()
    match = _INNINGS_RE.fullmatch(text)
    if not match:
        raise ValueError("투구 이닝은 153, 153.1, 153.2 형식으로 입력해 주세요.")
    whole = int(match.group(1))
    remainder = int(match.group(2) or 0)
    if whole > 100_000:
        raise ValueError("투구 이닝 값이 허용 범위를 넘었습니다.")
    return whole * 3 + remainder


def outs_to_innings(value: object) -> str:
    outs = _count(value, "투구 아웃 수", maximum=30_000_000)
    whole, remainder = divmod(outs, 3)
    return str(whole) if remainder == 0 else f"{whole}.{remainder}"


def normalize_stats(value: object, *, snapshot: bool = False) -> dict:
    source = value if isinstance(value, dict) else {}
    normalized = {key: _count(source.get(key), STAT_LABELS[key]) for key in COUNTING_STATS}
    if snapshot:
        normalized["pit_IP_outs"] = innings_to_outs(source.get("pit_IP"))
    elif "pit_IP_outs" in source:
        normalized["pit_IP_outs"] = _count(
            source.get("pit_IP_outs"), "투구 아웃 수", maximum=30_000_000
        )
    else:
        normalized["pit_IP_outs"] = innings_to_outs(source.get("pit_IP"))
    return normalized


def public_stats(raw: dict) -> dict:
    result = {key: int(raw.get(key, 0) or 0) for key in COUNTING_STATS}
    result["pit_IP_outs"] = int(raw.get("pit_IP_outs", 0) or 0)
    result["pit_IP"] = outs_to_innings(result["pit_IP_outs"])
    result["bat_AVG"] = (
        round(result["bat_H"] / result["bat_AB"], 3) if result["bat_AB"] else None
    )
    obp_denominator = (
        result["bat_AB"] + result["bat_BB"] + result["bat_HBP"] + result["bat_SF"]
    )
    result["bat_OBP"] = (
        round((result["bat_H"] + result["bat_BB"] + result["bat_HBP"]) / obp_denominator, 3)
        if obp_denominator
        else None
    )
    hit_parts = result["bat_1B"] + result["bat_2B"] + result["bat_3B"] + result["bat_HR"]
    result["bat_SLG"] = (
        round(
            (result["bat_1B"] + result["bat_2B"] * 2 + result["bat_3B"] * 3 + result["bat_HR"] * 4)
            / result["bat_AB"],
            3,
        )
        if result["bat_AB"] and hit_parts == result["bat_H"]
        else None
    )
    result["bat_OPS"] = (
        round(result["bat_OBP"] + result["bat_SLG"], 3)
        if result["bat_OBP"] is not None and result["bat_SLG"] is not None
        else None
    )
    result["pit_ERA"] = (
        round(result["pit_ER"] * 27 / result["pit_IP_outs"], 2)
        if result["pit_IP_outs"]
        else None
    )
    return result


def _sum_stats(rows: list[dict]) -> dict:
    total = {key: 0 for key in CANONICAL_STATS}
    for row in rows:
        stats = row.get("stats") if isinstance(row, dict) else None
        if not isinstance(stats, dict):
            continue
        for key in CANONICAL_STATS:
            total[key] += int(stats.get(key, 0) or 0)
    return total


def aggregate(profile: dict, current_snapshot: dict | None = None) -> dict:
    rows = list(profile.get("seasons") or [])
    total = _sum_stats(rows)
    if current_snapshot:
        current = normalize_stats(current_snapshot.get("stats"), snapshot=True)
        for key in CANONICAL_STATS:
            total[key] += current[key]
    return total


def _date_text(snapshot: dict | None) -> str | None:
    values = (snapshot or {}).get("date") or {}
    try:
        return date(int(values["year"]), int(values["month"]), int(values["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def normalize_season(
    payload: object,
    *,
    current_year: int | None,
    existing: dict | None = None,
    provenance: str = "manual_confirmed",
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("지난 시즌 입력값이 없습니다.")
    year = _count(payload.get("season_year"), "시즌 연도", maximum=9999)
    if year < 1936:
        raise ValueError("시즌 연도는 1936년 이후여야 합니다.")
    if current_year and year >= current_year and provenance == "manual_confirmed":
        raise ValueError("현재 시즌은 세이브 누계에 이미 포함됩니다. 지난 시즌만 입력해 주세요.")
    stats = normalize_stats(payload.get("stats"))
    if not any(stats.values()):
        raise ValueError("지난 시즌 기록을 하나 이상 입력해 주세요.")
    now = _now()
    row = {
        "id": (existing or {}).get("id") or f"season-{year}",
        "season_year": year,
        "team": _clean_text(payload.get("team"), "팀", 80),
        "league": "NPB",
        "stats": stats,
        "provenance": provenance,
        "note": _clean_text(payload.get("note"), "메모", 500),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    return row


def normalize_honor(payload: object, *, existing: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("수상·마일스톤 입력값이 없습니다.")
    kind = str(payload.get("kind") or "other")
    if kind not in HONOR_LABELS:
        raise ValueError("지원하지 않는 수상·마일스톤 종류입니다.")
    occurred_on = str(payload.get("occurred_on") or "").strip() or None
    parsed_date = None
    if occurred_on:
        try:
            parsed_date = date.fromisoformat(occurred_on)
        except ValueError as exc:
            raise ValueError("달성일은 YYYY-MM-DD 형식이어야 합니다.") from exc
    year = _count(payload.get("season_year"), "시즌 연도", maximum=9999)
    if not year and parsed_date:
        year = parsed_date.year
    if year and year < 1936:
        raise ValueError("시즌 연도는 1936년 이후여야 합니다.")
    count = _count(payload.get("count") or 1, "횟수", maximum=999)
    if count < 1:
        raise ValueError("횟수는 1 이상이어야 합니다.")
    title = _clean_text(payload.get("title"), "표시 이름", 160) or HONOR_LABELS[kind]
    now = _now()
    return {
        "id": (existing or {}).get("id") or f"honor-{uuid.uuid4().hex[:16]}",
        "kind": kind,
        "kind_label": HONOR_LABELS[kind],
        "title": title,
        "count": count,
        "occurred_on": occurred_on,
        "season_year": year or None,
        "team": _clean_text(payload.get("team"), "팀", 80),
        "note": _clean_text(payload.get("note"), "메모", 500),
        "provenance": "manual_confirmed",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }


def upsert_season(profile: dict, row: dict) -> None:
    seasons = list(profile.get("seasons") or [])
    seasons = [
        item
        for item in seasons
        if item.get("id") != row["id"] and item.get("season_year") != row["season_year"]
    ]
    seasons.append(row)
    profile["seasons"] = sorted(seasons, key=lambda item: int(item.get("season_year", 0)))


def upsert_honor(profile: dict, row: dict) -> None:
    honors = [item for item in (profile.get("honors") or []) if item.get("id") != row["id"]]
    honors.append(row)
    profile["honors"] = honors


def remove_profile_item(profile: dict, entity: str, item_id: str) -> bool:
    key = "seasons" if entity == "season" else "honors" if entity == "honor" else None
    if not key:
        raise ValueError("삭제할 기록 종류가 올바르지 않습니다.")
    before = len(profile.get(key) or [])
    profile[key] = [row for row in (profile.get(key) or []) if row.get("id") != item_id]
    return len(profile[key]) != before


def archive_completed_season(profile: dict, snapshot: dict) -> dict:
    values = snapshot.get("date") or {}
    year = int(values.get("year") or 0)
    existing = next(
        (row for row in profile.get("seasons", []) if int(row.get("season_year", 0)) == year),
        None,
    )
    payload = {
        "season_year": year,
        "team": (snapshot.get("player") or {}).get("team", ""),
        "stats": snapshot.get("stats") or {},
        "note": (existing or {}).get("note", ""),
    }
    row = normalize_season(
        payload,
        current_year=None,
        existing=existing,
        provenance="save_verified_completed_season",
    )
    if existing and existing.get("provenance") == "manual_confirmed":
        row["manual_replaced_at"] = _now()
    upsert_season(profile, row)
    return row


def sync_verified_history(profile: dict, snapshot: dict) -> dict:
    """Upsert save-verified prior seasons without duplicating manual rows."""
    history = (snapshot.get("career_history") or {}).get("seasons") or []
    if not isinstance(history, list):
        history = []
    imported: list[int] = []
    replaced_manual: list[int] = []
    changed = False
    for source in history:
        if not isinstance(source, dict):
            continue
        year = int(source.get("season_year") or 0)
        if year < 1936:
            continue
        existing = next(
            (row for row in profile.get("seasons", []) if int(row.get("season_year") or 0) == year),
            None,
        )
        normalized_stats = normalize_stats(source.get("stats"))
        same = bool(
            existing
            and existing.get("provenance") == "save_verified_historical_season"
            and normalize_stats(existing.get("stats")) == normalized_stats
            and int(existing.get("career_year") or 0) == int(source.get("career_year") or 0)
        )
        if same:
            imported.append(year)
            continue
        payload = {
            "season_year": year,
            "team": "",
            "stats": source.get("stats") or {},
            "note": (existing or {}).get("note") or "세이브 커리어 히스토리에서 자동 복원",
        }
        row = normalize_season(
            payload,
            current_year=None,
            existing=existing,
            provenance="save_verified_historical_season",
        )
        row.update(
            {
                "career_year": int(source.get("career_year") or 0) or None,
                "date_precision": "season_year",
                "source_hash": snapshot.get("content_hash"),
                "parser_format": (snapshot.get("career_history") or {}).get("format"),
                "team_provenance": "unavailable_in_season_summary",
            }
        )
        if existing and existing.get("provenance") == "manual_confirmed":
            row["manual_replaced_at"] = _now()
            row["manual_note_preserved"] = bool(existing.get("note"))
            replaced_manual.append(year)
        upsert_season(profile, row)
        imported.append(year)
        changed = True
    return {
        "changed": changed,
        "imported_years": sorted(imported),
        "manual_replaced_years": sorted(replaced_manual),
    }


def _display_value(stat_key: str, value: int) -> str:
    return outs_to_innings(value) if stat_key == "pit_IP_outs" else f"{value:,}"


def _comparison(record: dict, raw_value: int, *, incomplete: bool = False) -> dict:
    target = int(record["value"])
    if raw_value > target:
        status = "broken"
    elif raw_value == target:
        status = "tied"
    else:
        status = "approaching"
    remaining = max(0, target - raw_value)
    result = dict(record)
    result.update(
        {
            "current": raw_value,
            "current_display": _display_value(record["stat_key"], raw_value),
            "record_display": record.get("display_value")
            or _display_value(record["stat_key"], target),
            "remaining": remaining,
            "remaining_display": _display_value(record["stat_key"], remaining),
            "progress": min(100.0, round(raw_value * 100.0 / target, 1)) if target else 0,
            "status": status,
            "incomplete_history": bool(incomplete),
        }
    )
    return result


def _record_rows(scope: str, raw: dict, *, incomplete: bool = False) -> list[dict]:
    return [
        _comparison(record, int(raw.get(record["stat_key"], 0) or 0), incomplete=incomplete)
        for record in catalog().get("records", [])
        if record.get("scope") == scope
    ]


def _threshold_entries(
    old: dict,
    new: dict,
    *,
    existing_ids: set[str],
    occurred_on: str | None,
    source_hash: str | None,
    provenance: str,
) -> list[dict]:
    entries: list[dict] = []
    for stat_key, thresholds in catalog().get("milestone_thresholds", {}).items():
        old_value = int(old.get(stat_key, 0) or 0)
        new_value = int(new.get(stat_key, 0) or 0)
        for threshold in thresholds:
            threshold = int(threshold)
            entry_id = f"career-threshold:{stat_key}:{threshold}"
            if entry_id in existing_ids or not old_value < threshold <= new_value:
                continue
            entries.append(
                {
                    "id": entry_id,
                    "kind": "career_threshold",
                    "scope": "career",
                    "stat_key": stat_key,
                    "threshold": threshold,
                    "label": f"통산 {_display_value(stat_key, threshold)} {STAT_LABELS[stat_key]} 달성",
                    "occurred_on": occurred_on,
                    "observed_at": _now(),
                    "date_precision": "save_day" if occurred_on else "historical_import",
                    "provenance": provenance,
                    "source_hash": source_hash,
                }
            )
            existing_ids.add(entry_id)
    return entries


def _record_entries(
    scope: str,
    old: dict,
    new: dict,
    *,
    season_year: int | None,
    existing_ids: set[str],
    occurred_on: str | None,
    source_hash: str | None,
    provenance: str,
) -> list[dict]:
    entries: list[dict] = []
    for record in catalog().get("records", []):
        if record.get("scope") != scope:
            continue
        stat_key = record["stat_key"]
        old_value = int(old.get(stat_key, 0) or 0)
        new_value = int(new.get(stat_key, 0) or 0)
        target = int(record["value"])
        if not old_value < target <= new_value:
            continue
        suffix = f":{season_year}" if scope == "season" else ""
        entry_id = f"npb-record:{record['id']}{suffix}"
        if entry_id in existing_ids:
            continue
        tied = new_value == target
        relation = "동률" if tied else "수치 초과"
        entries.append(
            {
                "id": entry_id,
                "kind": "npb_record_reference",
                "scope": scope,
                "stat_key": stat_key,
                "threshold": target,
                "label": (
                    f"NPB {'시즌' if scope == 'season' else '통산'} {STAT_LABELS[stat_key]} "
                    f"기록과 {relation} ({record.get('record_display') or _display_value(stat_key, target)})"
                ),
                "comparison": "tied" if tied else "exceeded",
                "record_id": record["id"],
                "record_holder": record.get("holder"),
                "record_source_url": record.get("source_url"),
                "season_year": season_year,
                "occurred_on": occurred_on,
                "observed_at": _now(),
                "date_precision": "save_day" if occurred_on else "historical_import",
                "provenance": provenance,
                "source_hash": source_hash,
            }
        )
        existing_ids.add(entry_id)
    return entries


def detect_snapshot_milestones(
    profile: dict,
    ledger_entries: list[dict],
    previous: dict | None,
    current: dict,
    *,
    season_rollover: bool = False,
) -> list[dict]:
    if not previous:
        return []
    old_total = aggregate(profile, previous)
    next_profile = copy.deepcopy(profile)
    if season_rollover:
        archive_completed_season(next_profile, previous)
    new_total = aggregate(next_profile, current)
    existing_ids = {str(row.get("id")) for row in ledger_entries if isinstance(row, dict)}
    occurred_on = _date_text(current)
    source_hash = current.get("content_hash")
    entries = _threshold_entries(
        old_total,
        new_total,
        existing_ids=existing_ids,
        occurred_on=occurred_on,
        source_hash=source_hash,
        provenance="derived_save_delta",
    )
    if not season_rollover:
        old_season = normalize_stats(previous.get("stats"), snapshot=True)
        new_season = normalize_stats(current.get("stats"), snapshot=True)
        season_year = int((current.get("date") or {}).get("year") or 0) or None
        entries.extend(
            _record_entries(
                "season",
                old_season,
                new_season,
                season_year=season_year,
                existing_ids=existing_ids,
                occurred_on=occurred_on,
                source_hash=source_hash,
                provenance="derived_save_delta",
            )
        )
    entries.extend(
        _record_entries(
            "career",
            old_total,
            new_total,
            season_year=None,
            existing_ids=existing_ids,
            occurred_on=occurred_on,
            source_hash=source_hash,
            provenance="derived_save_delta",
        )
    )
    return entries


_GENERATED_IMPORT_PROVENANCE = {
    "manual_profile_import",
    "save_verified_historical_import",
    "save_verified_historical_milestone",
}

_FIRST_MILESTONES = (
    ("appearance", "첫 1군 출전", ("bat_G", "bat_AB", "pit_G", "pit_TBF")),
    ("bat_H", "첫 안타", ("bat_H",)),
    ("bat_HR", "첫 홈런", ("bat_HR",)),
    ("bat_RBI", "첫 타점", ("bat_RBI",)),
    ("bat_SB", "첫 도루", ("bat_SB",)),
    ("pit_W", "첫 승리", ("pit_W",)),
    ("pit_K", "첫 탈삼진", ("pit_K",)),
)


def _first_milestone_entries(
    profile: dict,
    current_snapshot: dict | None,
    *,
    existing_ids: set[str],
) -> list[dict]:
    rows = [row for row in profile.get("seasons", []) if isinstance(row, dict)]
    if current_snapshot:
        rows.append(
            {
                "season_year": int((current_snapshot.get("date") or {}).get("year") or 0),
                "stats": normalize_stats(current_snapshot.get("stats"), snapshot=True),
                "provenance": "save_verified_current_season",
                "source_hash": current_snapshot.get("content_hash"),
            }
        )
    rows.sort(key=lambda row: int(row.get("season_year") or 0))
    output: list[dict] = []
    for key, label, stat_keys in _FIRST_MILESTONES:
        entry_id = f"career-first:{key}"
        if entry_id in existing_ids:
            continue
        season = next(
            (
                row
                for row in rows
                if int(row.get("season_year") or 0) >= 1936
                and any(int((row.get("stats") or {}).get(stat_key, 0) or 0) > 0 for stat_key in stat_keys)
            ),
            None,
        )
        if not season:
            continue
        year = int(season["season_year"])
        provenance = (
            "save_verified_historical_milestone"
            if str(season.get("provenance") or "").startswith("save_verified")
            else "manual_profile_import"
        )
        output.append(
            {
                "id": entry_id,
                "kind": "career_first",
                "scope": "career",
                "stat_key": key,
                "label": f"커리어 {label} · {year} 시즌 내 달성 확인",
                "season_year": year,
                "occurred_on": None,
                "observed_at": _now(),
                "date_precision": "season_year",
                "provenance": provenance,
                "source_hash": season.get("source_hash"),
                "note": "시즌 요약에는 정확한 경기 날짜가 없어 연도 단위로 보관합니다.",
            }
        )
        existing_ids.add(entry_id)
    return output


def rebuild_imported_milestones(
    profile: dict, ledger_entries: list[dict], current_snapshot: dict | None
) -> list[dict]:
    preserved = [
        row
        for row in ledger_entries
        if isinstance(row, dict) and row.get("provenance") not in _GENERATED_IMPORT_PROVENANCE
    ]
    existing_ids = {str(row.get("id")) for row in preserved}
    current_total = aggregate(profile, current_snapshot)
    zero = {key: 0 for key in CANONICAL_STATS}
    seasons = [row for row in profile.get("seasons", []) if isinstance(row, dict)]
    fully_verified = all(
        str(row.get("provenance") or "").startswith("save_verified") for row in seasons
    )
    import_provenance = (
        "save_verified_historical_import" if fully_verified else "manual_profile_import"
    )
    imported = _threshold_entries(
        zero,
        current_total,
        existing_ids=existing_ids,
        occurred_on=None,
        source_hash=None,
        provenance=import_provenance,
    )
    imported.extend(
        _record_entries(
            "career",
            zero,
            current_total,
            season_year=None,
            existing_ids=existing_ids,
            occurred_on=None,
            source_hash=None,
            provenance=import_provenance,
        )
    )
    for season in seasons:
        raw = season.get("stats") or {}
        season_provenance = (
            "save_verified_historical_import"
            if str(season.get("provenance") or "").startswith("save_verified")
            else "manual_profile_import"
        )
        imported.extend(
            _record_entries(
                "season",
                zero,
                raw,
                season_year=int(season.get("season_year") or 0) or None,
                existing_ids=existing_ids,
                occurred_on=None,
                source_hash=None,
                provenance=season_provenance,
            )
        )
    imported.extend(
        _first_milestone_entries(profile, current_snapshot, existing_ids=existing_ids)
    )
    return preserved + imported


def milestone_tuples(entries: list[dict]) -> list[tuple[str, str]]:
    values = []
    for row in entries:
        label = row.get("label")
        if label:
            values.append(("통산" if row.get("scope") == "career" else "NPB 기록", label))
    return values


def _season_public(row: dict) -> dict:
    result = dict(row)
    result["stats"] = public_stats(row.get("stats") or {})
    raw = row.get("stats") or {}
    notable = [item for item in _record_rows("season", raw) if item["status"] != "approaching"]
    result["npb_record_matches"] = notable
    return result


def _honor_summary(honors: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for row in honors:
        kind = row.get("kind", "other")
        bucket = totals.setdefault(kind, {"count": 0, "provenance": set()})
        bucket["count"] += int(row.get("count", 1) or 1)
        bucket["provenance"].add(str(row.get("provenance") or "manual_confirmed"))
    return [
        {
            "kind": key,
            "label": HONOR_LABELS.get(key, key),
            "count": value["count"],
            "provenance": (
                next(iter(value["provenance"]))
                if len(value["provenance"]) == 1
                else "mixed_confirmed_sources"
            ),
        }
        for key, value in totals.items()
    ]


def _next_milestones(total: dict) -> list[dict]:
    rows = []
    for stat_key, thresholds in catalog().get("milestone_thresholds", {}).items():
        current = int(total.get(stat_key, 0) or 0)
        next_value = next((int(value) for value in thresholds if int(value) > current), None)
        if next_value is None:
            continue
        rows.append(
            {
                "stat_key": stat_key,
                "label": STAT_LABELS[stat_key],
                "current_display": _display_value(stat_key, current),
                "target": next_value,
                "target_display": _display_value(stat_key, next_value),
                "remaining_display": _display_value(stat_key, next_value - current),
                "progress": min(100.0, round(current * 100.0 / next_value, 1)),
            }
        )
    return sorted(rows, key=lambda row: (-row["progress"], row["label"]))


def _step_points(value: float, thresholds: tuple[tuple[float, int], ...]) -> int:
    points = 0
    for threshold, candidate in thresholds:
        if value >= threshold:
            points = candidate
    return points


def _season_peak_score(raw: dict) -> tuple[int, dict]:
    values = public_stats(raw)
    batting = 0
    batting += _step_points(values["bat_HR"], ((10, 3), (30, 8), (60, 15), (100, 22)))
    batting += _step_points(values["bat_RBI"], ((50, 2), (100, 4), (150, 6), (200, 8)))
    batting += _step_points(values["bat_SB"], ((20, 2), (50, 4), (100, 8)))
    if values["bat_AB"] >= 50 and values["bat_AVG"] is not None:
        batting += _step_points(
            values["bat_AVG"], ((0.300, 3), (0.350, 5), (0.400, 8), (0.600, 12), (0.700, 15))
        )
    batting = min(38, batting)

    pitching = 0
    pitching += _step_points(values["pit_K"], ((50, 3), (100, 7), (200, 13), (300, 20), (400, 23)))
    pitching += _step_points(values["pit_W"], ((5, 2), (10, 5), (15, 8), (20, 11)))
    pitching += _step_points(values["pit_IP_outs"], ((90, 2), (300, 4), (450, 6), (600, 8)))
    if values["pit_IP_outs"] >= 60 and values["pit_ERA"] is not None:
        era = values["pit_ERA"]
        pitching += 10 if era <= 0.50 else 7 if era <= 1.50 else 4 if era <= 3.00 else 0
    pitching = min(38, pitching)

    two_way = 10 if batting >= 15 and pitching >= 15 else 5 if batting >= 6 and pitching >= 6 else 0
    peak = min(55, max(batting, pitching) + round(min(batting, pitching) * 0.35) + two_way)
    return peak, {"batting": batting, "pitching": pitching, "two_way": two_way}


def _player_grade(
    profile: dict,
    current_snapshot: dict | None,
    total: dict,
    honors: list[dict],
    *,
    incomplete: bool,
) -> dict:
    season_rows = [row for row in profile.get("seasons", []) if isinstance(row, dict)]
    scored_rows = [row.get("stats") or {} for row in season_rows]
    if current_snapshot:
        scored_rows.append(normalize_stats(current_snapshot.get("stats"), snapshot=True))
    peaks = [_season_peak_score(row) for row in scored_rows] or [(0, {"batting": 0, "pitching": 0, "two_way": 0})]
    peak_score, peak_detail = max(peaks, key=lambda row: row[0])

    batting_volume = (
        _step_points(total.get("bat_HR", 0), ((10, 2), (50, 5), (100, 8), (300, 13), (600, 18)))
        + _step_points(total.get("bat_H", 0), ((100, 2), (500, 5), (1000, 9), (2000, 14), (3000, 18)))
    )
    pitching_volume = (
        _step_points(total.get("pit_K", 0), ((100, 2), (500, 6), (1000, 10), (2000, 15), (3000, 18)))
        + _step_points(total.get("pit_W", 0), ((10, 2), (30, 5), (100, 10), (200, 15), (300, 18)))
        + _step_points(total.get("pit_IP_outs", 0), ((300, 2), (1500, 5), (3000, 8), (6000, 12)))
    )
    career_volume = min(
        25,
        max(batting_volume, pitching_volume)
        + round(min(batting_volume, pitching_volume) * 0.35),
    )

    honor_weights = {
        "season_mvp": 8, "japan_series_mvp": 8, "japan_series_champion": 4,
        "league_champion": 3, "monthly_mvp": 2, "all_star_selection": 2,
        "title": 3, "trophy": 2, "season_record": 4, "career_record": 5,
    }
    honor_score = min(
        15,
        sum(
            honor_weights.get(str(row.get("kind") or "other"), 1)
            * max(1, int(row.get("count") or 1))
            for row in honors
        ),
    )

    notable_records = 0
    for raw in scored_rows:
        notable_records += sum(
            row["status"] in ("tied", "broken") for row in _record_rows("season", raw)
        )
    notable_records += sum(
        row["status"] in ("tied", "broken") for row in _record_rows("career", total)
    )
    record_score = min(15, notable_records * 3)
    continuity = min(5, max(1 if scored_rows else 0, len(scored_rows)))
    score = min(100, peak_score + career_volume + honor_score + record_score + continuity)

    grades = (
        (0, "D", "육성·루키"),
        (15, "C", "1군 정착"),
        (30, "B", "주전급"),
        (45, "A", "올스타급"),
        (60, "S", "리그 스타"),
        (72, "S+", "MVP·슈퍼스타"),
        (90, "EX", "역사적 현상"),
    )
    code, label = grades[0][1], grades[0][2]
    for threshold, candidate_code, candidate_label in grades:
        if score >= threshold:
            code, label = candidate_code, candidate_label
    descriptions = {
        "D": "아직 누적 근거가 적어 성장과 개인 사건이 중심인 단계입니다.",
        "C": "1군에서 역할과 반복 가능한 성과가 보이기 시작한 단계입니다.",
        "B": "한 시즌을 맡길 수 있는 주전급 누적과 피크가 확인됩니다.",
        "A": "리그 전체가 주목할 올스타급 성적 피크가 확인됩니다.",
        "S": "평범한 날도 팀과 리그의 뉴스가 되는 스타급 위상입니다.",
        "S+": "MVP 논쟁과 역사 비교를 부르는 슈퍼스타급 누적입니다.",
        "EX": "기존 시즌·통산 기준 자체를 다시 보게 만드는 역사적 현상입니다.",
    }
    return {
        "schema_version": 1,
        "code": code,
        "label": label,
        "score": score,
        "description": descriptions[code],
        "components": {
            "best_season_peak": peak_score,
            "career_volume": career_volume,
            "confirmed_honors": honor_score,
            "npb_record_comparisons": record_score,
            "career_continuity": continuity,
        },
        "best_season_detail": peak_detail,
        "notable_record_count": notable_records,
        "history_incomplete": bool(incomplete),
        "confidence": "lower_bound" if incomplete else "complete_for_available_career_years",
        "provenance": "derived_from_save_verified_seasons_and_confirmed_honors",
        "disclaimer": "게임 내 공식 선수 등급이 아닌 투명한 오프라인 서사·커리어 등급입니다.",
    }


def build_view(state: dict, current_snapshot: dict | None) -> dict:
    profile = migrate_profile(state.get("career_profile"))
    total = aggregate(profile, current_snapshot)
    current_raw = (
        normalize_stats(current_snapshot.get("stats"), snapshot=True)
        if current_snapshot
        else {key: 0 for key in CANONICAL_STATS}
    )
    date_values = (current_snapshot or {}).get("date") or {}
    career_year = int(date_values.get("career_year") or 0)
    completed_years = {int(row.get("season_year") or 0) for row in profile.get("seasons", [])}
    expected_previous = max(0, career_year - 1)
    incomplete = expected_previous > len([year for year in completed_years if year])
    milestones = [row for row in state.get("milestone_ledger", []) if isinstance(row, dict)]
    honors = [row for row in profile.get("honors", []) if isinstance(row, dict)]
    verified_seasons = [
        row
        for row in profile.get("seasons", [])
        if str(row.get("provenance") or "").startswith("save_verified")
    ]
    manual_seasons = [
        row for row in profile.get("seasons", []) if row.get("provenance") == "manual_confirmed"
    ]
    player_grade = _player_grade(
        profile,
        current_snapshot,
        total,
        honors,
        incomplete=incomplete,
    )
    timeline = [dict(row, entry_type="milestone") for row in milestones]
    timeline.extend(dict(row, entry_type="honor", label=row.get("title")) for row in honors)
    timeline.sort(
        key=lambda row: (
            row.get("occurred_on") or f"{int(row.get('season_year') or 0):04d}-00-00",
            row.get("observed_at") or row.get("created_at") or "",
        ),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "tracking": {
            "started_at": profile.get("tracking_started_at"),
            "current_season_year": int(date_values.get("year") or 0) or None,
            "career_year": career_year or None,
            "previous_seasons_entered": len(completed_years),
            "previous_seasons_expected": expected_previous,
            "previous_seasons_save_verified": len(verified_seasons),
            "previous_seasons_manual": len(manual_seasons),
            "history_incomplete": incomplete,
            "history_source": (
                "save_verified_historical_season"
                if len(verified_seasons) == len(completed_years) and completed_years
                else "mixed" if verified_seasons else "manual_confirmed" if completed_years else "none"
            ),
        },
        "player_grade": player_grade,
        "totals": public_stats(total),
        "current_season": {
            "season_year": int(date_values.get("year") or 0) or None,
            "team": ((current_snapshot or {}).get("player") or {}).get("team"),
            "stats": public_stats(current_raw),
            "provenance": "save_verified",
        },
        "seasons": [_season_public(row) for row in sorted(profile.get("seasons", []), key=lambda item: int(item.get("season_year", 0)), reverse=True)],
        "honors": sorted(
            honors,
            key=lambda row: (row.get("occurred_on") or "", int(row.get("season_year") or 0)),
            reverse=True,
        ),
        "honor_summary": _honor_summary(honors),
        "award_decoder": copy.deepcopy(
            ((current_snapshot or {}).get("career_history") or {}).get("awards")
            or {"status": "unavailable", "honors": []}
        ),
        "timeline": timeline,
        "next_milestones": _next_milestones(total),
        "records": {
            "season": _record_rows("season", current_raw),
            "career": _record_rows("career", total, incomplete=incomplete),
        },
        "sources": catalog().get("sources", []),
        "catalog_version": catalog().get("catalog_version"),
        "runtime_offline": True,
    }
