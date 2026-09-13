"""Read-only, provenance-preserving bridge from reviewed facts to narrative.

Never merge a user-confirmed game line into save-owned season statistics.
Unknown dates, unreviewed attachments, and conflicting results fail closed.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, timedelta

import narrative_contracts as nc

VERSION = "1.0.0"
FACT_LABELS = {"save_verified": "세이브 검증", "user_confirmed": "사용자 확인"}


def requested_date(text: str, open_date: str) -> str | None:
    explicit = re.search(r"\b(\d{4})[-./](\d{1,2})[-./](\d{1,2})\b", text)
    try:
        if explicit:
            return date(*(int(part) for part in explicit.groups())).isoformat()
        current = date.fromisoformat(open_date)
        if re.search(r"그제|그저께", text):
            return (current - timedelta(days=2)).isoformat()
        if "어제" in text:
            return (current - timedelta(days=1)).isoformat()
        if re.search(r"내일|다음|지난|그날|그때|작년|예전", text):
            return None
        return current.isoformat()
    except (TypeError, ValueError):
        return None


def for_context(state: dict, *, universe_id: str, protagonist_id: object, game_date: str | None) -> list[dict]:
    """Return independent evidence rows owned by this world/player/date."""
    if not game_date or (state.get("world_id") and str(state["world_id"]) != str(universe_id)):
        return []
    accepted = []
    for row in (state.get("fact_registry") or {}).values():
        if not isinstance(row, dict) or not row.get("fact_id"):
            continue
        if row.get("game_date") != game_date or row.get("evidence_class") not in FACT_LABELS:
            continue
        if row.get("status") in ("revoked", "rejected", "superseded", "invalid"):
            continue
        if any(row.get(key) is not None and str(row[key]) != str(value) for key, value in (("universe_id", universe_id), ("protagonist_id", protagonist_id))):
            continue
        source = str(row.get("source") or "")
        if source.startswith("attachment:"):
            record = (state.get("attachment_records") or {}).get(source.split(":", 1)[1]) or {}
            binding = record.get("binding") or {}
            if record.get("status") != "committed" or binding.get("game_date") != game_date:
                continue
            if str(binding.get("universe_id")) != str(universe_id) or str(binding.get("protagonist_id")) != str(protagonist_id):
                continue
        accepted.append(copy.deepcopy(row))
    return sorted(accepted, key=lambda row: (str(row.get("recorded_at") or ""), str(row["fact_id"])))


def _result_value(row: dict) -> dict | None:
    value = row.get("value")
    if row.get("kind") != "game.result" or not isinstance(value, dict):
        return None
    runs_for, runs_against = value.get("runs_for"), value.get("runs_against")
    if any(type(number) is not int or not 0 <= number <= 999 for number in (runs_for, runs_against)):
        return None
    outcome = "win" if runs_for > runs_against else "loss" if runs_for < runs_against else "draw"
    if value.get("outcome") not in (outcome, "tie" if outcome == "draw" else outcome):
        return None
    return {"runs_for": runs_for, "runs_against": runs_against, "outcome": outcome, "opponent": str(value.get("opponent") or "상대 미확인")[:160], "side": str(value.get("side") or "unknown")}


def result_blocks(facts: list[dict], game_date: str | None) -> list[dict]:
    """Project result facts without silently choosing among disagreements."""
    if game_date is None:
        return [nc.block("paragraph", "어느 날짜의 경기인지 먼저 알려 주세요. 날짜를 특정하지 않고 오늘의 결과로 대신 답하지는 않겠습니다.")]
    results = [(row, _result_value(row)) for row in facts]
    results = [(row, value) for row, value in results if value is not None]
    if not results:
        return [nc.block("paragraph", f"{game_date} 경기의 확인된 점수·승패 기록이 아직 없습니다. 결과 화면을 읽고 내용을 확인하거나, 해당 날짜의 기록을 먼저 확인해 주세요.")]
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for row, value in results:
        groups.setdefault(json.dumps(value, sort_keys=True, ensure_ascii=False), []).append((row, value))
    blocks = []
    if len(groups) > 1:
        blocks.append(nc.block("paragraph", f"{game_date}에 서로 다른 경기 결과가 남아 있습니다. 별개의 경기인지 입력 충돌인지 확인하기 전에는 하나를 정답으로 고르지 않겠습니다."))
    for group in groups.values():
        for evidence in FACT_LABELS:
            rows = [(row, value) for row, value in group if row["evidence_class"] == evidence]
            if not rows:
                continue
            value = rows[0][1]
            side = {"home": "홈", "away": "원정", "visitor": "원정"}.get(value["side"], "홈·원정 미확인")
            outcome = {"win": "승리", "loss": "패배", "draw": "무승부"}[value["outcome"]]
            text = f"{game_date}, {value['opponent']} 상대 {side} 경기: {value['runs_for']}–{value['runs_against']} {outcome}."
            blocks.append(nc.block("fact_callout", text, label=FACT_LABELS[evidence], evidence_class=evidence, fact_ids=[row["fact_id"] for row, _value in rows], source_ids=[row.get("source") for row, _value in rows]))
    if any(row["evidence_class"] == "user_confirmed" for row, _value in results):
        blocks.append(nc.block("paragraph", "사용자 확인 기록은 세이브가 직접 검증한 수치와 구분해 보관하고 있습니다."))
    return blocks
