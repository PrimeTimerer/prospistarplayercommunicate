#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transparent offline world-attention and ambient-reaction scaling.

The index is a narrative routing value, not an official baseball metric. It
uses only save-verified season facts, provenance-preserving career records,
user-confirmed honors, and the current verified event. It never upgrades a
fictional reaction into a fact.
"""

from __future__ import annotations

import hashlib
import json
import random

import stat_engine


TIERS = (
    {
        "id": "private",
        "min_score": 0,
        "label": "개인 세계선",
        "short_label": "개인",
        "description": "아직 주변 세계가 스스로 소란스러워질 단계는 아닙니다. 개인 감정과 가까운 관계가 서사의 중심입니다.",
        "base_budget": (1, 0, 1, 3),
    },
    {
        "id": "local",
        "min_score": 12,
        "label": "지역 관심",
        "short_label": "지역",
        "description": "작은 선택도 팀 주변과 지역 팬에게는 다음 이야깃거리로 읽히기 시작합니다.",
        "base_budget": (1, 1, 2, 5),
    },
    {
        "id": "club",
        "min_score": 28,
        "label": "구단 스타",
        "short_label": "구단",
        "description": "평범한 하루도 구단 운영, 팬 기대, 다음 경기의 신호로 확대됩니다.",
        "base_budget": (2, 1, 3, 9),
    },
    {
        "id": "league",
        "min_score": 45,
        "label": "리그 중심",
        "short_label": "리그",
        "description": "새 대기록이 없어도 상대팀과 리그 전체가 이 선수를 기준으로 다음 장면을 해석합니다.",
        "base_budget": (3, 2, 4, 15),
    },
    {
        "id": "national",
        "min_score": 62,
        "label": "전국 과열",
        "short_label": "전국",
        "description": "경기 밖의 짧은 행동까지 전국 기사, 해설, 팬덤 논쟁으로 번지는 위상입니다.",
        "base_budget": (5, 4, 6, 25),
    },
    {
        "id": "global",
        "min_score": 80,
        "label": "세계적 현상",
        "short_label": "세계",
        "description": "특별한 사건이 없는 날에도 누적 위상 자체가 뉴스입니다. 국내외 여러 집단이 서로의 반응을 다시 증폭합니다.",
        "base_budget": (7, 6, 8, 38),
    },
)


HONOR_POINTS = {
    "japan_series_champion": 7,
    "japan_series_mvp": 14,
    "league_champion": 5,
    "season_mvp": 14,
    "monthly_mvp": 4,
    "all_star_selection": 3,
    "title": 4,
    "trophy": 4,
    "season_record": 7,
    "career_record": 9,
    "other": 2,
}


WAVE_TEMPLATES = (
    (
        "inner",
        "본인·가까운 사람",
        "개인",
        "이날은 기록보다 본인의 감정과 가까운 관계가 먼저 움직인다.",
    ),
    (
        "clubhouse",
        "팀 동료·코칭스태프",
        "관리",
        "팀 안에서는 평범한 선택도 다음 경기와 컨디션 관리의 신호로 읽힌다.",
    ),
    (
        "home_fans",
        "홈 팬덤",
        "기대",
        "홈 팬들은 작은 행동 하나도 최근 누적 성적과 연결해 다시 해석한다.",
    ),
    (
        "rivals",
        "상대팀·전력분석",
        "경계",
        "직접 사건이 없는 날에도 상대 쪽은 다음 맞대결과 대응법부터 떠올린다.",
    ),
    (
        "national_media",
        "전국 언론·중계",
        "과열",
        "새 기록이 없어도 기록 카운트다운과 과거 장면이 다시 기사와 방송 소재가 된다.",
    ),
    (
        "baseball_establishment",
        "해설·원로·기록계",
        "역사 비교",
        "현재 수치는 단일 경기 평가를 넘어 과거의 기준과 비교하는 논쟁으로 옮겨간다.",
    ),
    (
        "international",
        "해외 팬덤·매체",
        "번역 확산",
        "국내 반응이 번역되고 비교표와 짧은 장면으로 재가공되며 다시 역수입된다.",
    ),
    (
        "public_culture",
        "비야구 대중·상업권",
        "문화 현상",
        "야구를 보지 않던 사람들까지 선수의 일상과 한마디를 하나의 문화 현상처럼 소비한다.",
    ),
)


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _step(value: float, thresholds: tuple[tuple[float, int], ...]) -> int:
    score = 0
    for threshold, points in thresholds:
        if value >= threshold:
            score = points
    return score


def _driver(target: list[dict], key: str, label: str, points: int, provenance: str) -> None:
    if points <= 0:
        return
    target.append(
        {
            "key": key,
            "label": label,
            "points": int(points),
            "provenance": provenance,
        }
    )


def _batting_score(stats: dict, drivers: list[dict]) -> int:
    ab = _number(stats.get("bat_AB"))
    hits = _number(stats.get("bat_H"))
    home_runs = _number(stats.get("bat_HR"))
    rbi = _number(stats.get("bat_RBI"))
    steals = _number(stats.get("bat_SB"))
    average = _number(stats.get("bat_AVG"))
    if not average and ab:
        average = hits / ab

    home_run_points = _step(
        home_runs,
        ((10, 2), (20, 4), (30, 7), (40, 10), (50, 14), (60, 17), (80, 20), (100, 23)),
    )
    average_points = (
        _step(average, ((0.300, 2), (0.350, 4), (0.400, 8), (0.500, 12), (0.600, 16), (0.700, 20)))
        if ab >= 50
        else 0
    )
    rbi_points = _step(rbi, ((50, 1), (100, 3), (150, 5), (200, 7)))
    steal_points = _step(steals, ((20, 1), (50, 3), (100, 7)))
    hit_points = _step(hits, ((100, 2), (150, 4), (200, 6)))

    _driver(drivers, "season_home_runs", f"시즌 {int(home_runs)}홈런", home_run_points, "save_verified")
    if average_points:
        _driver(drivers, "season_average", f"시즌 타율 {average:.3f}", average_points, "save_verified")
    _driver(drivers, "season_rbi", f"시즌 {int(rbi)}타점", rbi_points, "save_verified")
    _driver(drivers, "season_steals", f"시즌 {int(steals)}도루", steal_points, "save_verified")
    _driver(drivers, "season_hits", f"시즌 {int(hits)}안타", hit_points, "save_verified")
    return min(40, home_run_points + average_points + rbi_points + steal_points + hit_points)


def _pitching_score(stats: dict, drivers: list[dict]) -> int:
    # Real innings (153.1 -> 153.333) so K/9 and H/9 are not skewed by the
    # X.1 / X.2 display notation.
    innings = stat_engine.innings_float(stats.get("pit_IP"))
    strikeouts = _number(stats.get("pit_K"))
    hits = _number(stats.get("pit_H"))
    wins = _number(stats.get("pit_W"))
    k9 = strikeouts * 9.0 / innings if innings else 0.0
    h9 = hits * 9.0 / innings if innings else 99.0

    strikeout_points = _step(strikeouts, ((50, 2), (100, 5), (200, 10), (300, 15), (400, 18)))
    win_points = _step(wins, ((5, 1), (10, 3), (15, 6), (20, 8)))
    k9_points = _step(k9, ((9, 2), (12, 4), (15, 7), (20, 10))) if innings >= 20 else 0
    h9_points = 0
    if innings >= 20:
        if h9 <= 1:
            h9_points = 9
        elif h9 <= 3:
            h9_points = 6
        elif h9 <= 6:
            h9_points = 3
        elif h9 <= 9:
            h9_points = 1

    _driver(drivers, "season_strikeouts", f"시즌 {int(strikeouts)}탈삼진", strikeout_points, "save_verified")
    _driver(drivers, "season_wins", f"시즌 {int(wins)}승", win_points, "save_verified")
    if k9_points:
        _driver(drivers, "strikeout_rate", f"K/9 {k9:.1f}", k9_points, "save_derived")
    if h9_points:
        _driver(drivers, "hit_suppression", f"H/9 {h9:.2f}", h9_points, "save_derived")
    return min(36, strikeout_points + win_points + k9_points + h9_points)


def _honor_score(career: dict, drivers: list[dict]) -> int:
    total = 0
    kinds = set()
    for row in career.get("honor_summary") or []:
        kind = str(row.get("kind") or "other")
        kinds.add(kind)
        count = max(1, int(_number(row.get("count"))))
        unit = HONOR_POINTS.get(kind, HONOR_POINTS["other"])
        points = min(unit * count, unit + max(0, count - 1) * max(1, unit // 2))
        label = str(row.get("label") or kind)
        _driver(
            drivers,
            f"honor:{kind}",
            f"{label} {count}회",
            points,
            str(row.get("provenance") or "manual_confirmed"),
        )
        total += points
    if {"season_mvp", "japan_series_mvp"}.issubset(kinds):
        _driver(
            drivers,
            "honor:mvp_sweep",
            "시즌 MVP·일본시리즈 MVP 동시 보유",
            10,
            "manual_confirmed",
        )
        total += 10
    if {"japan_series_champion", "japan_series_mvp"}.issubset(kinds):
        _driver(
            drivers,
            "honor:japan_series_peak",
            "일본시리즈 우승·MVP 서사",
            4,
            "manual_confirmed",
        )
        total += 4
    return min(50, total)


def _stature_score(career: dict, drivers: list[dict]) -> int:
    grade = career.get("player_grade") or {}
    score = int(_number(grade.get("score")))
    points = _step(score, ((15, 3), (30, 7), (45, 12), (60, 18), (72, 26), (90, 34)))
    if points:
        _driver(
            drivers,
            "career_stature",
            f"커리어 등급 {grade.get('code') or '—'} · {grade.get('label') or '누적 위상'}",
            points,
            str(grade.get("provenance") or "derived_career_grade"),
        )
    return points


def _record_score(career: dict, drivers: list[dict]) -> int:
    notable = []
    for scope, rows in (career.get("records") or {}).items():
        for row in rows or []:
            if row.get("status") not in ("tied", "broken"):
                continue
            notable.append((scope, row))
    if notable:
        labels = [str(row.get("label") or "기록") for _scope, row in notable]
        summary = " · ".join(labels[:3])
        if len(labels) > 3:
            summary += f" 외 {len(labels) - 3}개"
        # Four or more simultaneous season-record comparisons are sufficient
        # to move an otherwise transcendent two-way season into the global
        # narrative tier without requiring a manually entered award.
        points = min(20, 5 * len(notable))
        _driver(
            drivers,
            "npb_record_comparisons",
            f"NPB 참고 기록 동률·초과 {len(notable)}개 ({summary})",
            points,
            "derived_reference_comparison",
        )
        return points
    return 0


def _milestone_score(career: dict, drivers: list[dict]) -> int:
    rows = [row for row in (career.get("timeline") or []) if row.get("entry_type") == "milestone"]
    if not rows:
        return 0
    points = min(8, len(rows) * 2)
    latest = str(rows[0].get("label") or "통산 마일스톤")
    _driver(drivers, "career_milestones", f"보관된 마일스톤 {len(rows)}개 · {latest}", points, "derived_or_imported")
    return points


def _event_score(event: dict | None, drivers: list[dict]) -> int:
    if not event or event.get("baseline_only") or event.get("kind") in ("NO_CHANGE", "REST_DAY", "WORLD_INIT"):
        return 0
    delta = event.get("delta") or {}
    strikeouts = _number(delta.get("pit_K"))
    home_runs = _number(delta.get("bat_HR"))
    hits = _number(delta.get("bat_H"))
    steals = _number(delta.get("bat_SB"))
    points = min(12, len(event.get("milestones") or []) * 4)
    points += _step(strikeouts, ((10, 3), (15, 6), (20, 10), (27, 14)))
    points += _step(home_runs, ((2, 3), (3, 6), (4, 10), (5, 13)))
    points += _step(hits, ((4, 2), (5, 4)))
    points += _step(steals, ((3, 2), (5, 4), (10, 7)))
    points = min(18, points)
    if points:
        labels = [text for _kind, text in (event.get("milestones") or [])]
        label = labels[0] if labels else "최근 검증 경기의 강한 기록 변화"
        provenance = "stored_same_day_save_delta" if event.get("same_day_echo") else "derived_save_delta"
        _driver(drivers, "current_event", label, points, provenance)
    return points


def _tier_for(score: int) -> tuple[int, dict]:
    index = 0
    for candidate, tier in enumerate(TIERS):
        if score >= tier["min_score"]:
            index = candidate
    return index, TIERS[index]


def _reaction_budget(tier_index: int, tier: dict, config: dict) -> dict:
    boards, media, waves, comments = tier["base_budget"]
    mode = str(config.get("mode") or "standard")
    heat = max(1, min(10, int(_number(config.get("heat") or 7))))
    if mode == "quick":
        boards = min(1, boards)
        media = 0
        waves = min(2, waves)
        comments = min(5, comments)
    elif mode == "explosion":
        boards = min(9, boards + (2 if tier_index >= 3 else 1))
        media = min(8, media + (2 if tier_index >= 3 else 1))
        waves = min(len(WAVE_TEMPLATES), waves + (2 if tier_index >= 2 else 1))
        comments = min(54, comments + 5 + tier_index * 2)
    # Heat is an expression control. Volume is owned by mode and the player's
    # verified stature, so moving the heat slider never manufactures more
    # people, posts, or coverage around the same event.
    comments = max(2 if boards else 0, min(60, comments))
    return {
        "boards": int(boards),
        "media": int(media),
        "waves": int(waves),
        "comments": int(comments),
        "mode": mode,
        "expression_heat": heat,
    }


def _ambient_waves(snapshot: dict, tier_index: int, budget: dict, drivers: list[dict]) -> list[dict]:
    player_name = str((snapshot.get("player") or {}).get("name") or "선수")
    anchor = drivers[0]["label"] if drivers else "현재까지 확인된 시즌 흐름"
    payload = json.dumps(
        {"hash": snapshot.get("content_hash"), "tier": tier_index, "anchor": anchor},
        ensure_ascii=False,
        sort_keys=True,
    )
    rng = random.Random(int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], 16))
    available = list(WAVE_TEMPLATES[: max(1, tier_index + 3)])
    if tier_index <= 1:
        available = list(WAVE_TEMPLATES[: tier_index + 1])
    selected = available[:]
    if len(selected) > 2:
        fixed = selected[:2]
        rest = selected[2:]
        rng.shuffle(rest)
        selected = fixed + rest
    selected = selected[: min(budget["waves"], len(WAVE_TEMPLATES))]
    waves = []
    for index, (wave_id, circle, tone, detail) in enumerate(selected, start=1):
        waves.append(
            {
                "id": wave_id,
                "sequence": index,
                "circle": circle,
                "tone": tone,
                "headline": f"{circle}, {player_name}을(를) 다시 말하기 시작한다",
                "detail": detail,
                "anchor": anchor,
                "provenance": "fictional_ambient_simulation",
            }
        )
    return waves


def evaluate(
    snapshot: dict,
    career: dict | None = None,
    event: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Build a transparent narrative-attention profile for one snapshot."""
    career = career if isinstance(career, dict) else {}
    config = config if isinstance(config, dict) else {}
    stats = snapshot.get("stats") or {}
    drivers: list[dict] = []

    batting = _batting_score(stats, drivers)
    pitching = _pitching_score(stats, drivers)
    if batting and pitching:
        two_way = 14 if batting >= 20 and pitching >= 20 else 10 if batting >= 8 and pitching >= 8 else 4
        _driver(drivers, "two_way", "투타 양쪽에서 누적 영향력 확인", two_way, "save_verified")
    else:
        two_way = 0
    performance = min(60, max(batting, pitching) + round(min(batting, pitching) * 0.5) + two_way)

    honor = _honor_score(career, drivers)
    stature = _stature_score(career, drivers)
    records = _record_score(career, drivers)
    milestones = _milestone_score(career, drivers)
    career_year = int(_number((snapshot.get("date") or {}).get("career_year")))
    continuity = min(4, max(0, career_year - 1))
    if continuity:
        _driver(drivers, "career_continuity", f"커리어 {career_year}년차", continuity, "save_verified")
    event_points = _event_score(event, drivers)

    score = min(100, performance + honor + stature + records + milestones + continuity + event_points)
    tier_index, tier = _tier_for(score)
    drivers.sort(key=lambda row: (-row["points"], row["key"]))
    budget = _reaction_budget(tier_index, tier, config)
    waves = _ambient_waves(snapshot, tier_index, budget, drivers)
    same_day_echo = bool(event and event.get("same_day_echo"))
    quiet_current_event = (
        not event
        or event.get("kind") in ("NO_CHANGE", "REST_DAY", "WORLD_INIT")
        or bool(event.get("baseline_only"))
        or same_day_echo
    )

    return {
        "schema_version": 1,
        "metric": "narrative_attention_index",
        "score": score,
        "tier": tier["id"],
        "tier_index": tier_index,
        "label": tier["label"],
        "short_label": tier["short_label"],
        "description": tier["description"],
        "quiet_current_event": quiet_current_event,
        "same_day_echo_active": same_day_echo,
        "ambient_active": tier_index >= 1,
        "reaction_budget": budget,
        "components": {
            "season_performance": performance,
            "honors": honor,
            "career_stature": stature,
            "npb_record_comparisons": records,
            "career_milestones": milestones,
            "career_continuity": continuity,
            "current_event": event_points,
        },
        "drivers": drivers[:10],
        "memory_anchors": [row["label"] for row in drivers[:6]],
        "waves": waves,
        "provenance": "derived_from_save_verified_and_provenance_preserving_career_facts",
        "disclaimer": "공식 인기·시장가치 지표가 아닌 오프라인 서사 반응 규모 계산입니다.",
    }


def story_budget(spotlight: dict | None, visibility: str) -> dict:
    """Scale one same-day intervention without making private facts public."""
    value = spotlight if isinstance(spotlight, dict) else {}
    tier = max(0, min(5, int(_number(value.get("tier_index")))))
    visibility = visibility if visibility in ("private", "clubhouse", "public", "social") else "clubhouse"
    if visibility == "private":
        table = ((0, 0, 1), (0, 0, 1), (0, 0, 2), (1, 0, 3), (2, 1, 4), (3, 2, 6))
    elif visibility == "clubhouse":
        table = ((0, 0, 1), (0, 0, 2), (1, 0, 3), (2, 1, 4), (3, 2, 5), (4, 3, 7))
    else:
        table = ((1, 0, 1), (1, 1, 2), (2, 1, 3), (3, 2, 4), (5, 4, 6), (7, 6, 8))
    boards, media, waves = table[tier]
    return {"boards": boards, "media": media, "waves": waves, "visibility": visibility}
