#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frozen league context, transparent sabermetrics, and salary projections.

Only metrics supported by verified save fields plus user-confirmed inputs are
calculated. Missing inputs remain visible instead of being silently invented.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone

import story_engine


_STAT_KEY_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,48}$")
_SABER_FIELDS = {
    "bat_BB",
    "bat_IBB",
    "bat_HBP",
    "bat_2B",
    "bat_3B",
    "bat_SF",
    "bat_CS",
    "pit_ER",
    "pit_BB",
    "pit_HBP",
    "pit_HR",
    "fip_constant",
    "manual_war",
    "yen_per_war",
    "batting_runs",
    "baserunning_runs",
    "fielding_runs",
    "positional_runs",
    "league_adjustment_runs",
    "replacement_runs",
    "runs_per_win",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: object, field: str, limit: int = 100) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        raise ValueError(f"{field}을(를) 입력해 주세요.")
    if len(text) > limit:
        raise ValueError(f"{field}은(는) {limit}자 이하여야 합니다.")
    return text


def _number(
    value: object,
    field: str,
    *,
    minimum: float = 0,
    maximum: float = 1_000_000_000_000,
    integer: bool = False,
    allow_blank: bool = False,
) -> int | float | None:
    if value in (None, ""):
        if allow_blank:
            return None
        if minimum <= 0:
            return 0 if integer else 0.0
        raise ValueError(f"{field}을(를) 입력해 주세요.")
    if isinstance(value, bool):
        raise ValueError(f"{field} 값이 올바르지 않습니다.")
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}은(는) 숫자여야 합니다.") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{field}은(는) {minimum:g}~{maximum:g} 범위여야 합니다.")
    if integer:
        if not number.is_integer():
            raise ValueError(f"{field}은(는) 정수여야 합니다.")
        return int(number)
    return number


def _optional_signed(value: object, field: str, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 값이 올바르지 않습니다.")
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}은(는) 숫자여야 합니다.") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field}은(는) {minimum:g}~{maximum:g} 범위여야 합니다.")
    return number


def empty_context(snapshot: dict) -> dict:
    stamp = _now()
    return {
        "schema_version": 1,
        "game_date": story_engine.date_key(snapshot),
        "status": "open",
        "verified_snapshot": story_engine.snapshot_fact(snapshot),
        "standings": [],
        "leaderboards": [],
        "saber_inputs": {},
        "salary": {},
        "provenance": "manual_confirmed",
        "created_at": stamp,
        "updated_at": stamp,
        "sealed_at": None,
    }


def _normalize_standings(rows: object) -> list[dict]:
    if rows is None:
        return []
    if not isinstance(rows, list) or len(rows) > 24:
        raise ValueError("순위표는 최대 24개 팀까지 입력할 수 있습니다.")
    normalized = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("순위표 행 형식이 올바르지 않습니다.")
        league = _clean_text(raw.get("league") or "리그", "리그 이름", 40)
        rank = _number(raw.get("rank"), "순위", minimum=1, maximum=24, integer=True)
        key = (league.casefold(), rank)
        if key in seen:
            raise ValueError(f"{league} {rank}위가 중복됐습니다.")
        seen.add(key)
        games_back = _optional_signed(raw.get("games_back"), "게임차", -99, 99)
        normalized.append(
            {
                "league": league,
                "rank": rank,
                "team": _clean_text(raw.get("team"), "팀 이름", 80),
                "wins": _number(raw.get("wins"), "승", maximum=999, integer=True),
                "losses": _number(raw.get("losses"), "패", maximum=999, integer=True),
                "ties": _number(raw.get("ties"), "무", maximum=999, integer=True),
                "games_back": games_back,
            }
        )
    return sorted(normalized, key=lambda row: (row["league"], row["rank"]))


def _normalize_leaderboard(value: object, player_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Top 5 기록 형식이 올바르지 않습니다.")
    stat_key = str(value.get("stat_key") or "").strip()
    if not _STAT_KEY_RE.fullmatch(stat_key):
        raise ValueError("Top 5 기록 키는 영문·숫자·점·밑줄·하이픈만 사용할 수 있습니다.")
    label = _clean_text(value.get("label"), "기록 이름", 80)
    league = _clean_text(value.get("league") or "리그", "리그 이름", 40)
    entries = value.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 5:
        raise ValueError("Top 5에는 1~5명의 순위를 입력해 주세요.")
    normalized = []
    ranks = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("Top 5 행 형식이 올바르지 않습니다.")
        rank = _number(raw.get("rank"), "Top 5 순위", minimum=1, maximum=5, integer=True)
        if rank in ranks:
            raise ValueError(f"Top 5 {rank}위가 중복됐습니다.")
        ranks.add(rank)
        name = _clean_text(raw.get("name"), "선수 이름", 80)
        display_value = _clean_text(raw.get("value"), "기록 값", 40)
        normalized.append(
            {
                "rank": rank,
                "name": name,
                "team": str(raw.get("team") or "").strip()[:80],
                "value": display_value,
                "is_player": bool(raw.get("is_player")) or name.casefold() == player_name.casefold(),
            }
        )
    normalized.sort(key=lambda row: row["rank"])
    player_rank = next((row["rank"] for row in normalized if row["is_player"]), None)
    if player_rank is None:
        raise ValueError("이 보관함에는 내 선수가 포함된 Top 5 기록만 저장합니다.")
    return {
        "stat_key": stat_key,
        "label": label,
        "league": league,
        "entries": normalized,
        "player_rank": player_rank,
        "provenance": "manual_confirmed",
        "updated_at": _now(),
    }


def _normalize_saber_inputs(value: object, existing: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("세이버 입력 형식이 올바르지 않습니다.")
    result = copy.deepcopy(existing)
    signed = {
        "manual_war": (-10, 30),
        "batting_runs": (-200, 300),
        "baserunning_runs": (-100, 100),
        "fielding_runs": (-200, 200),
        "positional_runs": (-100, 100),
        "league_adjustment_runs": (-100, 100),
        "replacement_runs": (-100, 300),
    }
    decimals = {"fip_constant": (0, 10), "yen_per_war": (0, 100_000_000_000), "runs_per_win": (1, 30)}
    for key, raw in value.items():
        if key not in _SABER_FIELDS:
            continue
        if raw in (None, ""):
            result.pop(key, None)
            continue
        if key in signed:
            result[key] = _optional_signed(raw, key, *signed[key])
        elif key in decimals:
            result[key] = _number(raw, key, minimum=decimals[key][0], maximum=decimals[key][1])
        else:
            result[key] = _number(raw, key, maximum=10_000_000, integer=True)
    doubles = int(result.get("bat_2B") or 0)
    triples = int(result.get("bat_3B") or 0)
    if doubles < 0 or triples < 0:
        raise ValueError("장타 입력은 0 이상이어야 합니다.")
    return result


def _normalize_salary(value: object, existing: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("연봉 입력 형식이 올바르지 않습니다.")
    result = copy.deepcopy(existing)
    currency_fields = ("current_yen", "previous_yen")
    eval_fields = ("manager_eval", "club_eval", "star_level")
    for key in currency_fields:
        if key in value:
            if value[key] in (None, ""):
                result.pop(key, None)
            else:
                result[key] = _number(value[key], key, maximum=1_000_000_000_000, integer=True)
    for key in eval_fields:
        if key in value:
            if value[key] in (None, ""):
                result.pop(key, None)
            else:
                result[key] = _number(value[key], key, maximum=100, integer=True)
    if "mission_successes" in value:
        if value["mission_successes"] in (None, ""):
            result.pop("mission_successes", None)
        else:
            result["mission_successes"] = _number(
                value["mission_successes"], "미션 성공 횟수", maximum=999, integer=True
            )
    if "actual_offers" in value:
        rows = value.get("actual_offers")
        if not isinstance(rows, list) or len(rows) > 30:
            raise ValueError("실제 연봉 이력은 최대 30시즌까지 입력할 수 있습니다.")
        offers = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("실제 연봉 이력 형식이 올바르지 않습니다.")
            offers.append(
                {
                    "season_year": _number(row.get("season_year"), "시즌 연도", minimum=1936, maximum=9999, integer=True),
                    "before_yen": _number(row.get("before_yen"), "갱신 전 연봉", maximum=1_000_000_000_000, integer=True),
                    "after_yen": _number(row.get("after_yen"), "갱신 후 연봉", maximum=1_000_000_000_000, integer=True),
                }
            )
        result["actual_offers"] = offers
    return result


def merge_context(existing: dict | None, payload: dict, snapshot: dict) -> dict:
    context = copy.deepcopy(existing) if isinstance(existing, dict) else empty_context(snapshot)
    if context.get("status") == "sealed":
        raise ValueError("이미 마감된 날짜의 리그·가치 스냅샷은 수정할 수 없습니다.")
    if context.get("game_date") != story_engine.date_key(snapshot):
        raise ValueError("현재 검증 날짜와 입력하려는 스냅샷 날짜가 다릅니다.")
    context["verified_snapshot"] = story_engine.snapshot_fact(snapshot)
    if "standings" in payload:
        context["standings"] = _normalize_standings(payload.get("standings"))
    player_name = str((snapshot.get("player") or {}).get("name") or "")
    if "leaderboard" in payload:
        row = _normalize_leaderboard(payload.get("leaderboard"), player_name)
        boards = [
            item
            for item in context.get("leaderboards") or []
            if item.get("stat_key") != row["stat_key"] or item.get("league") != row["league"]
        ]
        boards.append(row)
        context["leaderboards"] = sorted(boards, key=lambda item: (item.get("league", ""), item.get("label", "")))
    if "remove_leaderboard" in payload:
        remove = payload.get("remove_leaderboard") or {}
        stat_key = str(remove.get("stat_key") or "")
        league = str(remove.get("league") or "")
        context["leaderboards"] = [
            item
            for item in context.get("leaderboards") or []
            if not (item.get("stat_key") == stat_key and item.get("league") == league)
        ]
    if "saber_inputs" in payload:
        context["saber_inputs"] = _normalize_saber_inputs(
            payload.get("saber_inputs"), context.get("saber_inputs") or {}
        )
    if "salary" in payload:
        context["salary"] = _normalize_salary(payload.get("salary"), context.get("salary") or {})
    context["updated_at"] = _now()
    return context


def seal(context: dict) -> None:
    if context.get("status") == "sealed":
        return
    context["status"] = "sealed"
    context["sealed_at"] = _now()
    context["updated_at"] = context["sealed_at"]


def _innings(stats: dict) -> float:
    value = stats.get("pit_IP")
    if value in (None, ""):
        return 0.0
    text = str(value).strip()
    if "." in text:
        whole, _, remainder = text.partition(".")
        if remainder in ("1", "2") and whole.isdigit():
            return int(whole) + int(remainder) / 3.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _metric(
    metric_id: str,
    label: str,
    value: float | None,
    *,
    digits: int = 3,
    formula: str,
    provenance: str,
    missing: list[str] | None = None,
    note: str | None = None,
) -> dict:
    available = value is not None and math.isfinite(value)
    display = None
    if available:
        display = f"{value:.{digits}f}"
        if digits == 3 and value < 1:
            display = display.lstrip("0")
    return {
        "id": metric_id,
        "label": label,
        "available": available,
        "value": round(value, max(digits, 4)) if available else None,
        "display": display or "입력 필요",
        "formula": formula,
        "provenance": provenance,
        "missing": missing or [],
        "note": note,
    }


def _safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def sabermetrics(stats: dict, inputs: dict) -> dict:
    h = int(stats.get("bat_H") or 0)
    ab = int(stats.get("bat_AB") or 0)
    hr = int(stats.get("bat_HR") or 0)
    so = int(stats.get("bat_SO") or 0)
    bb = inputs.get("bat_BB")
    hbp = inputs.get("bat_HBP")
    doubles = inputs.get("bat_2B")
    triples = inputs.get("bat_3B")
    sf = inputs.get("bat_SF")
    bat_metrics = [
        _metric("avg", "AVG", _safe_div(h, ab), formula="H / AB", provenance="save_derived")
    ]
    required_rate = [name for name, value in (("BB", bb), ("HBP", hbp), ("SF", sf)) if value is None]
    obp = None if required_rate else _safe_div(h + bb + hbp, ab + bb + hbp + sf)
    bat_metrics.append(
        _metric("obp", "OBP", obp, formula="(H+BB+HBP)/(AB+BB+HBP+SF)", provenance="save_plus_manual", missing=required_rate)
    )
    required_power = [name for name, value in (("2B", doubles), ("3B", triples)) if value is None]
    slg = None
    singles = None
    if not required_power and ab:
        singles = h - doubles - triples - hr
        if singles >= 0:
            slg = (singles + 2 * doubles + 3 * triples + 4 * hr) / ab
        else:
            required_power.append("H ≥ 1B+2B+3B+HR")
    bat_metrics.append(
        _metric("slg", "SLG", slg, formula="TB / AB", provenance="save_plus_manual", missing=required_power)
    )
    ops = obp + slg if obp is not None and slg is not None else None
    bat_metrics.append(
        _metric("ops", "OPS", ops, formula="OBP + SLG", provenance="derived", missing=[] if ops is not None else ["OBP", "SLG"])
    )
    avg = _safe_div(h, ab)
    iso = slg - avg if slg is not None and avg is not None else None
    bat_metrics.append(
        _metric("iso", "ISO", iso, formula="SLG - AVG", provenance="derived", missing=[] if iso is not None else ["SLG"])
    )
    babip_missing = [name for name, value in (("SF", sf),) if value is None]
    babip_denominator = ab - so - hr + sf if not babip_missing else 0
    babip = _safe_div(h - hr, babip_denominator) if not babip_missing else None
    bat_metrics.append(
        _metric("babip", "BABIP", babip, formula="(H-HR)/(AB-SO-HR+SF)", provenance="save_plus_manual", missing=babip_missing)
    )

    ip = _innings(stats)
    pit_k = int(stats.get("pit_K") or 0)
    pit_h = int(stats.get("pit_H") or 0)
    pit_bb = inputs.get("pit_BB")
    pit_hbp = inputs.get("pit_HBP")
    pit_hr = inputs.get("pit_HR")
    pit_er = inputs.get("pit_ER")
    pit_metrics = [
        _metric("k9", "K/9", _safe_div(pit_k * 9, ip), digits=2, formula="K × 9 / IP", provenance="save_derived"),
        _metric("h9", "H/9", _safe_div(pit_h * 9, ip), digits=2, formula="H × 9 / IP", provenance="save_derived"),
    ]
    pit_metrics.append(
        _metric("era", "ERA", _safe_div(pit_er * 9, ip) if pit_er is not None else None, digits=2, formula="ER × 9 / IP", provenance="save_plus_manual", missing=[] if pit_er is not None else ["ER"])
    )
    pit_metrics.append(
        _metric("whip", "WHIP", _safe_div(pit_h + pit_bb, ip) if pit_bb is not None else None, formula="(BB+H) / IP", provenance="save_plus_manual", missing=[] if pit_bb is not None else ["BB"])
    )
    pit_metrics.append(
        _metric("kbb", "K/BB", _safe_div(pit_k, pit_bb) if pit_bb is not None else None, digits=2, formula="K / BB", provenance="save_plus_manual", missing=[] if pit_bb is not None else ["BB"])
    )
    fip_missing = [name for name, value in (("BB", pit_bb), ("HBP", pit_hbp), ("HR", pit_hr), ("리그 FIP 상수", inputs.get("fip_constant"))) if value is None]
    fip = None
    if not fip_missing and ip:
        fip = (13 * pit_hr + 3 * (pit_bb + pit_hbp) - 2 * pit_k) / ip + inputs["fip_constant"]
    pit_metrics.append(
        _metric("fip", "FIP", fip, digits=2, formula="(13HR+3(BB+HBP)-2K)/IP + C", provenance="save_plus_manual", missing=fip_missing)
    )

    component_keys = (
        "batting_runs",
        "baserunning_runs",
        "fielding_runs",
        "positional_runs",
        "league_adjustment_runs",
        "replacement_runs",
        "runs_per_win",
    )
    component_missing = [key for key in component_keys if inputs.get(key) is None]
    component_war = None
    if not component_missing:
        runs = sum(inputs[key] for key in component_keys[:-1])
        component_war = runs / inputs["runs_per_win"]
    manual_war = inputs.get("manual_war")
    war = manual_war if manual_war is not None else component_war
    war_provenance = "manual_confirmed" if manual_war is not None else "manual_components"
    war_metric = _metric(
        "war",
        "WAR",
        war,
        digits=2,
        formula="사용자 확인 WAR" if manual_war is not None else "공격+주루+수비+포지션+리그+대체선 득점 / 득점당 승리",
        provenance=war_provenance,
        missing=[] if war is not None else component_missing,
        note="현재 세이브만으로 수비·주루·리그·파크 보정 WAR을 만들지 않습니다.",
    )
    return {
        "batting": bat_metrics,
        "pitching": pit_metrics,
        "war": war_metric,
        "sources": [
            {"label": "MLB Glossary · wOBA", "url": "https://www.mlb.com/glossary/advanced-stats/weighted-on-base-average"},
            {"label": "MLB Glossary · FIP", "url": "https://www.mlb.com/glossary/advanced-stats/fielding-independent-pitching"},
            {"label": "MLB Glossary · WAR", "url": "https://www.mlb.com/glossary/advanced-stats/wins-above-replacement"},
        ],
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def dominance_index(stats: dict) -> dict:
    ab = int(stats.get("bat_AB") or 0)
    h = int(stats.get("bat_H") or 0)
    hr = int(stats.get("bat_HR") or 0)
    ip = _innings(stats)
    k = int(stats.get("pit_K") or 0)
    allowed_h = int(stats.get("pit_H") or 0)
    wins = int(stats.get("pit_W") or 0)
    batting = None
    pitching = None
    if ab:
        avg = h / ab
        hr_rate = hr / ab
        batting = _clamp(100 * (0.62 * avg / 0.400 + 0.38 * hr_rate / 0.10), 0, 100)
    if ip:
        k9 = k * 9 / ip
        h9 = allowed_h * 9 / ip
        pitching = _clamp(100 * (0.58 * k9 / 15 + 0.27 * max(0, 1 - h9 / 9) + 0.15 * wins / 20), 0, 100)
    available = [value for value in (batting, pitching) if value is not None]
    overall = max(available) if len(available) == 1 else min(100, sum(available) / len(available) + 8) if available else 0
    return {
        "overall": round(overall, 1),
        "batting": round(batting, 1) if batting is not None else None,
        "pitching": round(pitching, 1) if pitching is not None else None,
        "label": "게임 서사 지배력 지수",
        "not_war": True,
        "method": "검증 세이브의 AVG·HR/AB·K/9·H/9·승수를 0~100으로 정규화한 서사용 지수",
    }


def salary_projection(stats: dict, inputs: dict, leaderboards: list[dict], saber: dict) -> dict:
    current = inputs.get("current_yen")
    official_rules = [
        {
            "label": "미션 성공은 다음 연도 연봉에 보정",
            "url": "https://www.konami.com/games/prospi/2024-2025_support/faq/0/jp/ja/pc/item?no=61",
        },
        {
            "label": "계약 갱신 협상은 최대 3회",
            "url": "https://www.konami.com/games/prospi/2024-2025_support/faq/0/jp/ja/pc/item?no=58",
        },
    ]
    index = dominance_index(stats)
    manual_war = saber.get("war", {}).get("value") if saber.get("war", {}).get("available") else None
    yen_per_war = inputs.get("yen_per_war")
    market_value = None
    if manual_war is not None and yen_per_war is not None:
        market_value = max(0, round(manual_war * yen_per_war))
    if current is None:
        return {
            "available": False,
            "missing": ["현재 연봉"],
            "confidence": "계산 전",
            "dominance_index": index,
            "saber_market_value_yen": market_value,
            "official_rules": official_rules,
            "disclaimer": "게임 내부의 비공개 연봉 공식을 확정값처럼 만들지 않습니다.",
        }
    top5_count = sum(1 for row in leaderboards if row.get("player_rank") in (1, 2, 3, 4, 5))
    mission = int(inputs.get("mission_successes") or 0)
    evaluations = [inputs[key] for key in ("manager_eval", "club_eval", "star_level") if inputs.get(key) is not None]
    eval_average = sum(evaluations) / len(evaluations) if evaluations else 50.0
    ratio = 0.90 + 0.006 * index["overall"] + min(0.20, top5_count * 0.04) + min(0.15, mission * 0.015)
    ratio += _clamp((eval_average - 50) / 250, -0.15, 0.20)
    calibration = []
    for row in inputs.get("actual_offers") or []:
        before = row.get("before_yen") or 0
        after = row.get("after_yen") or 0
        if before > 0 and after >= 0:
            calibration.append(after / before)
    if calibration:
        observed = sum(calibration) / len(calibration)
        blend = min(0.55, 0.20 + 0.08 * len(calibration))
        ratio = ratio * (1 - blend) + observed * blend
    ratio = _clamp(ratio, 0.65, 3.0)
    evidence_score = 1 + len(evaluations) + int("mission_successes" in inputs) + min(3, len(calibration)) + min(2, top5_count)
    if evidence_score >= 7:
        confidence, spread = "중간", 0.14
    elif evidence_score >= 4:
        confidence, spread = "낮음", 0.22
    else:
        confidence, spread = "매우 낮음", 0.32
    center = round(current * ratio)
    low = max(0, round(center * (1 - spread)))
    high = round(center * (1 + spread))
    gap = market_value - center if market_value is not None else None
    return {
        "available": True,
        "current_yen": current,
        "projected_yen": center,
        "low_yen": low,
        "high_yen": high,
        "projected_ratio": round(ratio, 3),
        "confidence": confidence,
        "evidence_score": evidence_score,
        "top5_count": top5_count,
        "mission_successes": mission,
        "evaluation_average": round(eval_average, 1) if evaluations else None,
        "calibration_seasons": len(calibration),
        "negotiation_rounds": [
            {"round": 1, "target_yen": low, "label": "안전선"},
            {"round": 2, "target_yen": center, "label": "중앙 추정"},
            {"round": 3, "target_yen": high, "label": "상단 요구"},
        ],
        "dominance_index": index,
        "saber_market_value_yen": market_value,
        "market_gap_yen": gap,
        "official_rules": official_rules,
        "disclaimer": "게임 공식 산식이 공개되지 않았으므로 검증 기록·평가·미션·실제 갱신 이력으로 보정한 추정 범위입니다.",
    }


def build_view(context: dict | None, snapshot: dict) -> dict:
    value = copy.deepcopy(context) if isinstance(context, dict) else empty_context(snapshot)
    frozen = value.get("verified_snapshot") or story_engine.snapshot_fact(snapshot)
    stats = frozen.get("stats") or {}
    saber = sabermetrics(stats, value.get("saber_inputs") or {})
    salary_inputs = copy.deepcopy(value.get("salary") or {})
    if "manual_war" in (value.get("saber_inputs") or {}):
        salary_inputs["manual_war"] = value["saber_inputs"]["manual_war"]
    if "yen_per_war" in (value.get("saber_inputs") or {}):
        salary_inputs["yen_per_war"] = value["saber_inputs"]["yen_per_war"]
    return {
        "game_date": value.get("game_date") or story_engine.date_key(snapshot),
        "status": value.get("status", "open"),
        "provenance": value.get("provenance", "manual_confirmed"),
        "standings": value.get("standings") or [],
        "leaderboards": value.get("leaderboards") or [],
        "saber_inputs": value.get("saber_inputs") or {},
        "salary_inputs": value.get("salary") or {},
        "sabermetrics": saber,
        "dominance_index": dominance_index(stats),
        "salary_projection": salary_projection(stats, salary_inputs, value.get("leaderboards") or [], saber),
        "verified_snapshot": frozen,
        "updated_at": value.get("updated_at"),
        "sealed_at": value.get("sealed_at"),
    }
