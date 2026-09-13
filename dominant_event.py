#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Same-day event graph, dominant-event selection, and claim guards (5.3, 5.4.1).

The extractor never reduces a game to good/bad. It turns a verified save
delta into structured candidates, keeps batting and pitching separate, and
selects the dominant event(s) with hard rules: a verified perfect game,
no-hit game, major record, championship, award, debut, retirement, or severe
injury cannot be omitted; a completed rare achievement outranks an ordinary
negative secondary line; a hitless batting line may be discussed only as a
contrast and can never produce praise for successful hitting.
"""

from __future__ import annotations

import stat_engine

CANNOT_OMIT = frozenset(
    {
        "SP.GAME.PITCH.PERFECT_GAME",
        "SP.GAME.PITCH.NO_HITTER",
        "SP.MILESTONE.FIRST_EVER",
        "SP.MILESTONE.SURPASSED",
        "SP.MILESTONE.FRANCHISE_RECORD",
        "SP.STANDINGS.TITLE",
        "SP.CALENDAR.JAPAN_SERIES",
        "SP.AWARD.MVP",
        "SP.AWARD.JAPAN_SERIES_MVP",
        "SP.ROSTER.FIRST_START",
        "SP.IDENTITY.DEBUT_PROFILE",
        "SP.LEGACY.CEREMONY",
        "SP.HEALTH.INJURED_LIST",
    }
)

RARITY = {
    "SP.GAME.PITCH.PERFECT_GAME": 1.0,
    "SP.GAME.PITCH.NO_HITTER": 0.95,
    "SP.GAME.PITCH.SHUTOUT": 0.7,
    "SP.GAME.PITCH.COMPLETE_GAME": 0.6,
    "SP.GAME.PITCH.STRIKEOUT": 0.3,
    "SP.GAME.PITCH.HIT_ALLOWED": 0.2,
    "SP.GAME.PITCH.WALK": 0.1,
    "SP.GAME.BAT.HOME_RUN": 0.4,
    "SP.GAME.BAT.HIT": 0.2,
    "SP.GAME.BAT.HITLESS_GAME": 0.25,
    "SP.GAME.BAT.PLATE_APPEARANCE": 0.05,
    "SP.GAME.RUN.STEAL_ATTEMPT": 0.25,
    "SP.MILESTONE.SEASON_THRESHOLD": 0.6,
    "SP.MILESTONE.CAREER_THRESHOLD": 0.7,
    "SP.MILESTONE.TIED": 0.85,
    "SP.MILESTONE.SURPASSED": 0.95,
    "SP.MILESTONE.FIRST_EVER": 1.0,
    "SP.MILESTONE.FRANCHISE_RECORD": 0.9,
    "SP.GAME.POST.REST_DAY": 0.05,
    "SP.GAME.PRE.REST_DAY": 0.05,
    "SP.GAME.POST.NO_APPEARANCE": 0.05,
}


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def candidate(
    event_type: str,
    label: str,
    *,
    verified: bool,
    fact_ids=(),
    agency: str = "direct",
    leverage: float = 0.5,
    milestone_state: str | None = None,
    season_importance: float = 0.3,
    outcome: str = "neutral",
    domain_role: str = "pitching",
    rarity: float | None = None,
    facts: dict | None = None,
) -> dict:
    return {
        "event_type": event_type,
        "label": label,
        "verified": bool(verified),
        "fact_ids": list(fact_ids),
        "agency": agency,
        "leverage": max(0.0, min(1.0, float(leverage))),
        "milestone_state": milestone_state,
        "season_importance": max(0.0, min(1.0, float(season_importance))),
        "outcome": outcome,
        "domain_role": domain_role,
        "rarity": RARITY.get(event_type, 0.3) if rarity is None else float(rarity),
        "facts": dict(facts or {}),
    }


def extract_game_candidates(event: dict, *, extra_facts=()) -> list[dict]:
    """Structured candidates from a verified save delta plus confirmed facts.

    Pitching and batting are extracted separately. The save exposes IP, TBF,
    K, H, W for pitching and AB, H, HR, RBI, R, SO, SB for batting; nothing
    else is inferred. ``extra_facts`` are user-confirmed or otherwise
    verified facts (dicts with ``event_type``, ``label``, ``fact_id``).
    """
    delta = dict((event or {}).get("delta") or {})
    rows: list[dict] = []
    kind = (event or {}).get("kind")
    if kind in ("REST_DAY",):
        rows.append(candidate("SP.GAME.PRE.REST_DAY", "휴식일", verified=True, agency="participated", leverage=0.0, outcome="neutral", domain_role="none"))
        return rows + [_confirmed(row) for row in extra_facts]
    outs = stat_engine.innings_to_outs(delta.get("pit_IP", 0))
    tbf = int(_number(delta.get("pit_TBF")))
    strikeouts = int(_number(delta.get("pit_K")))
    hits_allowed = int(_number(delta.get("pit_H")))
    if outs > 0 or tbf > 0:
        innings_text = stat_engine.outs_to_innings_value(outs)
        base_facts = {"pit_IP": innings_text, "pit_TBF": tbf, "pit_K": strikeouts, "pit_H": hits_allowed}
        if event.get("delta_only"):
            # A save interval is not an independently decoded complete game.
            rows.append(candidate("SP.GAME.PITCH.LINE", f"{innings_text}이닝 투구 · 피안타 {hits_allowed} · 탈삼진 {strikeouts}", verified=True, leverage=0.5, outcome="positive" if strikeouts >= 10 else "neutral", facts=base_facts))
        elif outs >= 27 and hits_allowed == 0:
            # Nine innings without a hit is verifiable from the box score.
            # Runs, walks, and errors are not stored, so this is never
            # promoted to a perfect game or a no-hit no-run game.
            rows.append(
                candidate(
                    "SP.GAME.PITCH.NO_HITTER", f"{innings_text}이닝 무피안타 · 상대 타자 {tbf}명", verified=True,
                    agency="direct", leverage=1.0, outcome="positive", season_importance=0.9, facts=base_facts,
                )
            )
            if tbf == 27:
                rows[-1]["facts"]["batters_equal_outs"] = True
                rows[-1]["label"] = f"{innings_text}이닝 무피안타 · 27타자 상대 27아웃"
        elif outs >= 27:
            rows.append(candidate("SP.GAME.PITCH.COMPLETE_GAME", f"{innings_text}이닝 완투 · 피안타 {hits_allowed}", verified=True, leverage=0.8, outcome="positive", season_importance=0.6, facts=base_facts))
        else:
            outcome = "positive" if hits_allowed <= max(2, outs // 9) else "neutral" if hits_allowed <= outs // 3 else "negative"
            rows.append(candidate("SP.GAME.PITCH.HIT_ALLOWED", f"{innings_text}이닝 · 피안타 {hits_allowed} · 탈삼진 {strikeouts}", verified=True, leverage=0.5, outcome=outcome, facts=base_facts))
        if strikeouts >= 10:
            rows.append(candidate("SP.GAME.PITCH.STRIKEOUT", f"{strikeouts}탈삼진", verified=True, leverage=0.6, outcome="positive", season_importance=0.5, rarity=min(0.9, 0.3 + strikeouts / 30.0), facts={"pit_K": strikeouts}))
    at_bats = int(_number(delta.get("bat_AB")))
    hits = int(_number(delta.get("bat_H")))
    home_runs = int(_number(delta.get("bat_HR")))
    rbi = int(_number(delta.get("bat_RBI")))
    steals = int(_number(delta.get("bat_SB")))
    if at_bats > 0:
        bat_facts = {"bat_AB": at_bats, "bat_H": hits, "bat_HR": home_runs, "bat_RBI": rbi}
        if hits == 0:
            rows.append(candidate("SP.GAME.BAT.HITLESS_GAME", f"{at_bats}타수 무안타", verified=True, leverage=0.3, outcome="negative", domain_role="batting", facts=bat_facts))
        else:
            if home_runs > 0:
                rows.append(candidate("SP.GAME.BAT.HOME_RUN", f"{home_runs}홈런 · {at_bats}타수 {hits}안타", verified=True, leverage=0.6, outcome="positive", domain_role="batting", rarity=min(0.95, 0.4 + 0.2 * home_runs), facts=bat_facts))
            else:
                rows.append(candidate("SP.GAME.BAT.HIT", f"{at_bats}타수 {hits}안타" + (f" {rbi}타점" if rbi else ""), verified=True, leverage=0.4, outcome="positive", domain_role="batting", facts=bat_facts))
    if steals > 0:
        rows.append(candidate("SP.GAME.RUN.STEAL_ATTEMPT", f"도루 {steals}개", verified=True, leverage=0.4, outcome="positive", domain_role="running", facts={"bat_SB": steals}))
    for _kind, label in (event or {}).get("milestones") or []:
        rows.append(candidate("SP.MILESTONE.SEASON_THRESHOLD", str(label), verified=True, leverage=0.7, milestone_state="completed", outcome="positive", season_importance=0.7, domain_role="record"))
    for row in (event or {}).get("career_milestones") or []:
        if isinstance(row, dict):
            status = str(row.get("status") or "")
            event_type = "SP.MILESTONE.SURPASSED" if status == "broken" else "SP.MILESTONE.TIED" if status == "tied" else "SP.MILESTONE.CAREER_THRESHOLD"
            rows.append(candidate(event_type, str(row.get("label") or "통산 기준선"), verified=True, fact_ids=[str(row.get("id") or "")], leverage=0.8, milestone_state="completed", outcome="positive", season_importance=0.8, domain_role="record"))
    rows.extend(_confirmed(row) for row in extra_facts)
    if not rows and kind in ("NO_CHANGE", "WORLD_INIT", None):
        rows.append(candidate("SP.GAME.POST.NO_APPEARANCE", "새 경기 기록 없음", verified=True, agency="participated", leverage=0.0, domain_role="none"))
    return rows


def _confirmed(row: dict) -> dict:
    return candidate(
        str(row.get("event_type")),
        str(row.get("label") or row.get("event_type")),
        verified=bool(row.get("verified", True)),
        fact_ids=[str(row.get("fact_id"))] if row.get("fact_id") else [],
        agency=str(row.get("agency") or "direct"),
        leverage=float(row.get("leverage", 1.0)),
        milestone_state=row.get("milestone_state") or ("completed" if str(row.get("event_type", "")).startswith("SP.MILESTONE") else None),
        season_importance=float(row.get("season_importance", 0.9)),
        outcome=str(row.get("outcome") or "positive"),
        domain_role=str(row.get("domain_role") or "pitching"),
        rarity=row.get("rarity"),
        facts=row.get("facts"),
    )


def dominance_score(row: dict) -> float:
    agency = {"direct": 1.0, "contributed": 0.6, "participated": 0.2}.get(row.get("agency"), 0.5)
    completed = 1.0 if row.get("milestone_state") == "completed" else 0.3 if row.get("milestone_state") == "approaching" else 0.0
    score = (
        0.35 * float(row.get("rarity") or 0)
        + 0.20 * agency
        + 0.15 * float(row.get("leverage") or 0)
        + 0.15 * completed
        + 0.10 * float(row.get("season_importance") or 0)
        + 0.05 * (1.0 if row.get("verified") else 0.0)
    )
    if row.get("event_type") in CANNOT_OMIT and row.get("verified"):
        score += 1.0
    return round(score, 4)


def select_dominant(candidates: list[dict]) -> dict:
    """Return lead, secondary contrasts, mandatory items, and claim guards."""
    rows = [dict(row, score=dominance_score(row)) for row in candidates]
    rows.sort(key=lambda row: (-row["score"], row["event_type"]))
    if not rows:
        return {"lead": None, "secondary": [], "mandatory": [], "guards": [], "two_way": False}
    lead = rows[0]
    mandatory = [row for row in rows if row["event_type"] in CANNOT_OMIT and row["verified"]]
    secondary = [row for row in rows[1:] if row is not lead]
    roles = {row.get("domain_role") for row in rows}
    two_way = "pitching" in roles and "batting" in roles
    guards: list[dict] = []
    perfect_verified = any(r["event_type"] == "SP.GAME.PITCH.PERFECT_GAME" and r["verified"] for r in rows)
    for row in rows:
        if row["event_type"] == "SP.GAME.PITCH.LINE":
            guards.append({"guard": "save_delta_not_complete_game", "reason": "a save delta does not certify a complete game or team result", "forbidden_phrases": ["퍼펙트", "완전 시합", "노히트노런", "노히트 노런", "완봉", "완투"]})
        if row["event_type"] == "SP.GAME.BAT.HITLESS_GAME":
            guards.append({"guard": "no_batting_praise", "reason": row["label"], "forbidden_phrases": ["맹타", "타격 활약", "타석에서도 빛", "안타를 몰아", "방망이도", "타격도 좋았"], "allowed_role": "secondary_contrast"})
        if row["event_type"] == "SP.GAME.PITCH.NO_HITTER" and not perfect_verified:
            # The box score proves nine hitless innings only. Runs, walks,
            # and errors are not stored, so stronger claims need a fact.
            guards.append({"guard": "no_runs_claim", "reason": "runs, walks, and errors are not stored", "forbidden_phrases": ["노히트노런", "퍼펙트", "완전 시합", "완봉"], "allowed_phrase": row["label"]})
        if row["event_type"] == "SP.GAME.PITCH.STRIKEOUT" and (row.get("facts") or {}).get("pit_K") and not perfect_verified:
            guards.append({"guard": "no_perfect_promotion", "reason": "all-strikeout box score is not a perfect game", "forbidden_phrases": ["퍼펙트"]})
    negative_lead = lead["outcome"] == "negative"
    positive_rare = [row for row in rows if row["outcome"] == "positive" and row["rarity"] >= 0.6 and row["verified"]]
    if negative_lead and positive_rare:
        lead = positive_rare[0]
        secondary = [row for row in rows if row is not lead]
    if two_way:
        guards.append({"guard": "two_way_separation", "reason": "batting and pitching are evaluated separately before synthesis", "pitching": [row["label"] for row in rows if row.get("domain_role") == "pitching"], "batting": [row["label"] for row in rows if row.get("domain_role") == "batting"]})
    return {
        "lead": lead,
        "secondary": secondary,
        "mandatory": mandatory,
        "guards": guards,
        "two_way": two_way,
        "headline_fact": lead["label"],
        "score_table": [{"event_type": row["event_type"], "score": row["score"], "outcome": row["outcome"]} for row in rows],
    }


def guard_violations(text: str, guards: list[dict]) -> list[dict]:
    """Check emitted prose against the claim guards of the day."""
    problems = []
    value = str(text or "")
    for guard in guards or []:
        for phrase in guard.get("forbidden_phrases") or []:
            if phrase in value:
                problems.append({"guard": guard.get("guard"), "phrase": phrase, "reason": guard.get("reason")})
    return problems


def outcome_context(selection: dict) -> dict:
    """Context keys used by reaction templates (outcome, batting_outcome, dominant_event)."""
    lead = (selection or {}).get("lead") or {}
    rows = [lead] + list((selection or {}).get("secondary") or [])
    batting = [row for row in rows if row.get("domain_role") == "batting"]
    pitching = [row for row in rows if row.get("domain_role") == "pitching"]
    return {
        "dominant_event": lead.get("event_type"),
        "outcome": lead.get("outcome") or "neutral",
        "batting_outcome": (batting[0].get("outcome") if batting else None),
        "pitching_outcome": (pitching[0].get("outcome") if pitching else None),
        "role": "two_way" if batting and pitching else "batting" if batting else "pitching" if pitching else "none",
    }
