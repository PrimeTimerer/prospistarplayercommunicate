"""Bounded, original editorial planning for new feeds; never rewrite archives.

Facts -> purpose -> evidence allocation -> outline -> authored moves -> audit.
The compatibility body/comments fields remain plain projections of the stored
blocks and reply graph. This module performs no network, model, or ledger I/O.
Only the two versioned, bundled authored packs are read (once per process).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from collections import Counter
from functools import lru_cache
from datetime import date
from pathlib import Path

import realism_gate as rg
import stat_engine
import template_store as ts

VERSION = "1.1.0"
MEMORY_KEY = "editorial_v1"
PACK_ROOT = Path(__file__).resolve().parent / "data" / "editorial"
OUTLINES = (
    ("report_then_explain", ("opening", "evidence", "reading", "caution", "next")),
    ("evidence_then_question", ("evidence", "opening", "caution", "reading", "next")),
    ("claim_then_test", ("opening", "reading", "evidence", "caution", "next")),
    ("bounded_comparison", ("opening", "caution", "evidence", "reading", "next")),
    ("evidence_and_alternative", ("evidence", "reading", "opening", "caution", "next")),
    ("question_and_limits", ("opening", "reading", "caution", "evidence", "next")),
)
STANCES = ("support", "qualify", "ask", "joke", "oppose", "correct", "redirect")
HANDLES = {
    "support": ("주말직관", "파란스코어북", "응원석끝자리"),
    "qualify": ("느린리플레이", "야구수첩", "길게봅시다"),
    "ask": ("아직배우는중", "기록찾는사람", "다음페이지"),
    "joke": ("커피와야구", "야근끝9회", "메모장두개"),
    "oppose": ("원정석메모", "다른팀팬", "찬물한잔"),
    "correct": ("기록대조중", "숫자옆날짜", "분모부터"),
    "redirect": ("내일도야구", "다음경기노트", "시즌끝에다시"),
}
PLATFORM_HANDLES = {
    "dc": {
        "opener": ("ㅇㅇ(118.***)", "직관만감", "야구보는밤"),
        "support": ("ㅇㅇ(39.***)", "오늘도직관", "공끝미쳤다"),
        "qualify": ("ㅇㅇ(211.***)", "표본은보자", "일단한경기"),
        "ask": ("ㅇㅇ(223.***)", "야알못질문", "기록어디서봄"),
        "joke": ("ㅇㅇ(106.***)", "퇴근못함", "치킨식는중"),
        "oppose": ("ㅇㅇ(175.***)", "타팀눈팅", "설레발금지"),
        "correct": ("ㅇㅇ(121.***)", "기록표가져옴", "분모부터봐"),
        "redirect": ("ㅇㅇ(58.***)", "다음경기언제", "짤대기중"),
    },
    "fmk": {
        "opener": ("야구보는퇴근러", "직관가는날", "오늘의기록"),
        "support": ("포텐대기중", "타팀팬입니다", "야구는낭만"),
        "qualify": ("한경기더보자", "표본수집가", "중립팬인척"),
        "ask": ("야구뉴비", "기록찾는중", "움짤어디감"),
        "joke": ("라면불었다", "출근포기", "짤줍는사람"),
        "oppose": ("원정팀팬", "설레발은금물", "반대도한표"),
        "correct": ("팩트만정리", "스탯표첨부", "기록실지박령"),
        "redirect": ("다음경기존버", "하이라이트대기", "시즌끝에보자"),
    },
    "mlb": {
        "opener": ("BlueSeat", "BallparkNote", "잠실외야석"),
        "support": ("SeamReader", "직관20년", "OPS_believer"),
        "qualify": ("표본먼저", "긴시즌", "ColdZone"),
        "ask": ("기록질문", "DataRookie", "비교표찾는중"),
        "joke": ("야구는매일", "퇴근후불펜", "연장전싫어요"),
        "oppose": ("원정석시선", "반론있습니다", "다른구단팬"),
        "correct": ("분모확인", "기록대조", "파크팩터"),
        "redirect": ("다음등판", "시즌메모", "가을에다시"),
    },
}
BOARD_SPECS = (
    ("club-pulse", "구단 팬 포럼"), ("rival-watch", "상대팀 전력 토론"),
    ("record-room", "야구 기록 연구실"), ("league-live", "리그 야구 라이브"),
    ("broadcast-desk", "중계·해설 라운지"), ("global-translation", "국제 야구 토론방"),
    ("culture-trend", "야구 일상 게시판"), ("history-debate", "시즌 회고 게시판"),
)
COUNT_FIELDS = ("pit_IP", "pit_K", "pit_H", "pit_W", "pit_TBF", "bat_AB", "bat_H",
                "bat_HR", "bat_RBI", "bat_R", "bat_SO", "bat_SB")


def stable(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _stable_number(low: int, high: int, *parts) -> int:
    """Return deterministic fictional display metadata without touching facts."""
    if high <= low:
        return low
    return low + int(stable(*parts)[:8], 16) % (high - low + 1)


def _platform_author(ctx: dict, code: str, stance: str, ordinal: int) -> str:
    groups = PLATFORM_HANDLES.get(code) or {}
    choices = groups.get(stance) or HANDLES.get(stance) or ("야구팬",)
    if code == "dc":
        # A DC-style thread should visibly mix anonymous IP handles with fixed
        # nicknames.  Stable alternation avoids a random all-nickname thread.
        anonymous = tuple(value for value in choices if value.startswith("ㅇㅇ("))
        named = tuple(value for value in choices if not value.startswith("ㅇㅇ("))
        requested = anonymous if ordinal % 2 == 0 else named
        if requested:
            choices = requested
    return choices[_stable_number(0, len(choices) - 1, ctx["seed"], code, stance, ordinal)]


def _decorate_thread_display(ctx: dict, code: str, opener: dict, replies: list[dict]) -> dict:
    """Attach additive, explicitly fictional forum metadata for the renderer."""
    seed = ctx["seed"]
    rows = [opener, *replies]
    for ordinal, row in enumerate(rows):
        minute = _stable_number(0, 59, seed, code, "minute", ordinal)
        hour = _stable_number(11, 23, seed, code, "hour", ordinal)
        row["posted_at"] = f"{hour:02d}:{minute:02d}"
        row["reply_depth"] = 0 if ordinal == 0 else 1
        row["is_opener"] = ordinal == 0
        row["down"] = _stable_number(0, 18 if code == "dc" else 7, seed, code, "down", ordinal)
        row["display_metadata_fictional"] = True
    views_floor, views_ceiling = {
        "dc": (640, 18900), "fmk": (420, 14200), "mlb": (180, 6800),
    }.get(code, (120, 3200))
    return {
        "category": {"dc": "일반", "fmk": "야구", "mlb": "KBO·NPB"}.get(code, "토론"),
        "posted_at": opener["posted_at"],
        "views": _stable_number(views_floor, views_ceiling, seed, code, "views"),
        "recommendations": _stable_number(3, 420 if code == "fmk" else 150, seed, code, "recommend"),
        "reply_count": len(replies),
        "fictional": True,
    }


@lru_cache(maxsize=1)
def packs() -> tuple[dict, dict, str]:
    values, manifests = [], []
    for name in ("press-v1.json", "discussion-v1.json"):
        raw = (PACK_ROOT / name).read_bytes()
        value = json.loads(raw)
        if value.get("license_class") != "authored" or value.get("version") != VERSION:
            raise ValueError("unsupported editorial pack")
        values.append(value)
        manifests.append(hashlib.sha256(raw).hexdigest())
    press, discussion = values
    ids = [row["id"] for row in press["purposes"]]
    if len(ids) != len(set(ids)) or not set(ids) <= discussion["topics"].keys() or not set(ids) <= discussion["counterpoints"].keys():
        raise ValueError("incomplete editorial purpose registry")
    for purpose in press["purposes"]:
        required = ("headlines", "deks", "claim", "question", "outlet", "genre")
        if any(not purpose.get(key) for key in required):
            raise ValueError(f"incomplete editorial purpose: {purpose['id']}")
        if purpose["lang"] == "ko" and any(not purpose.get(key) for key in ("opening", "reading", "caution", "next")):
            raise ValueError(f"missing editorial move: {purpose['id']}")
        if purpose["lang"] != "ko" and any(len(pair) != 2 or not all(pair) for group in purpose["pair_variants"] for pair in group):
            raise ValueError(f"missing authored translation: {purpose['id']}")
    for code in ("dc", "fmk", "mlb"):
        if not discussion.get("platform_titles", {}).get(code):
            raise ValueError(f"missing platform titles: {code}")
        if not discussion.get("platform_openers", {}).get(code):
            raise ValueError(f"missing platform openers: {code}")
        voice = discussion.get("voices", {}).get(code) or {}
        if any(not voice.get(stance) for stance in STANCES):
            raise ValueError(f"incomplete platform voice: {code}")
    return values[0], values[1], hashlib.sha256("|".join(manifests).encode()).hexdigest()


def _number(stats: dict, key: str):
    value = stats.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _count(stats: dict, key: str):
    value = _number(stats, key)
    return int(value) if value is not None and int(value) == value else None


def _copula(text: str) -> str:
    return text + ("이다" if ts.particle(text, "이라라") == "이라" else "다")


def _date(snapshot: dict) -> str:
    d = snapshot.get("date") or {}
    try:
        return date(int(d["year"]), int(d["month"]), int(d["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return "unknown"


def _valid_stats(raw: dict) -> tuple[dict, list[str]]:
    """Reject contradictory inputs, not unusual but internally valid results."""
    stats, rejected = {}, []
    for key in COUNT_FIELDS:
        if key not in raw:
            continue
        value = _number(raw, key) if key == "pit_IP" else _count(raw, key)
        if value is None or (key == "pit_IP" and (not math.isfinite(value * 10) or not math.isclose(value * 10 % 10, round(value * 10 % 10), abs_tol=1e-6))):
            rejected.append(key)
            continue
        if key == "pit_IP" and round(value * 10) % 10 not in (0, 1, 2):
            rejected.append(key)
            continue
        stats[key] = value
    # Never silently repair a contradictory line into a plausible one.
    for left, right, family in (("bat_H", "bat_AB", "bat_"), ("bat_HR", "bat_H", "bat_"),
                                ("pit_K", "pit_TBF", "pit_"), ("pit_H", "pit_TBF", "pit_")):
        if left in stats and right in stats and stats[left] > stats[right]:
            rejected.extend(k for k in stats if k.startswith(family))
            stats = {k: v for k, v in stats.items() if not k.startswith(family)}
    return stats, sorted(set(rejected))


def _interval(event: dict, stats: dict, previous: dict | None) -> tuple[dict, list[str]]:
    raw = event.get("delta") or {}
    if not raw or event.get("kind") not in ("NEW_GAME", "SEASON_UPDATE") or event.get("baseline_only"):
        return {}, []
    delta, rejected = _valid_stats(raw)
    if rejected or any(k not in COUNT_FIELDS for k in raw):
        return {}, ["invalid_delta"]
    if previous is None:
        # Compatibility callers supply an already classified delta. Persistent
        # service callers always supply both snapshots and take the path below.
        return delta, []
    snap = event["snapshot"]
    now_date, prev_date = _date(snap), _date(previous)
    same_player = (snap.get("player") or {}).get("id") == (previous.get("player") or {}).get("id")
    same_career = (snap.get("date") or {}).get("career_year") == (previous.get("date") or {}).get("career_year")
    if not same_player or not same_career or "unknown" in (now_date, prev_date) or now_date[:4] != prev_date[:4] or now_date < prev_date:
        return {}, ["interval_binding_mismatch"]
    prior, _ = _valid_stats(previous.get("stats") or {})
    if not snap.get("content_hash") or not previous.get("content_hash"):
        return {}, ["missing_interval_source"]
    calculated = {k: stat_engine.innings_delta(stats[k], prior[k]) if k == "pit_IP" else stats[k] - prior[k]
                  for k in COUNT_FIELDS if k in stats and k in prior}
    if any(v < 0 for v in calculated.values()) or any(k not in calculated or calculated[k] != v for k, v in delta.items()):
        return {}, ["unverified_interval_delta"]
    # The legacy ledger omits zero deltas. Restore a known zero only when both
    # snapshots contain the field; four at-bats with unchanged hits is hitless.
    checked, errors = _valid_stats(calculated)
    return ({}, ["inconsistent_interval_line"]) if errors else (checked, [])


def _pitch_line(stats: dict) -> str:
    parts = []
    innings = _number(stats, "pit_IP")
    if innings is not None:
        parts.append(f"{innings:g}이닝")
    for key, before, after in (("pit_K", "", "탈삼진"), ("pit_H", "피안타 ", "개"), ("pit_W", "", "승")):
        value = _count(stats, key)
        if value is not None:
            parts.append(f"{before}{value}{after}")
    return ", ".join(parts)


def _bat_line(stats: dict) -> str:
    ab, hits = _count(stats, "bat_AB"), _count(stats, "bat_H")
    parts = []
    if ab is not None and hits is not None and 0 <= hits <= ab:
        parts.append(f"{ab}타수 무안타" if ab > 0 and hits == 0 else f"{ab}타수 {hits}안타")
    else:
        if ab is not None:
            parts.append(f"{ab}타수")
        if hits is not None:
            parts.append(f"{hits}안타")
    for key, unit in (("bat_HR", "홈런"), ("bat_RBI", "타점"), ("bat_R", "득점"), ("bat_SB", "도루")):
        value = _count(stats, key)
        if value is not None:
            parts.append(f"{value}{unit}")
    return ", ".join(parts)


def context(event: dict, *, universe_id: str = "", previous: dict | None = None,
            story: dict | None = None, story_id: str = "", game_bundle: dict | None = None,
            spotlight: dict | None = None) -> dict:
    """Project only this protagonist's evidence; no prose or private text input."""
    snap = event["snapshot"]
    player = snap.get("player") or {}
    player_id = str(player.get("id") if player.get("id") is not None else "unknown")
    source_hash = str(snap.get("content_hash") or "unknown")
    # Direct legacy callers have no persistent world. The service always passes
    # the real world ID; preview identity must never be persisted by the caller.
    world = str(universe_id or f"preview:{player_id}:{source_hash}")
    binding = {"universe_id": world, "protagonist_id": player_id, "game_date": _date(snap)}
    sources = [f"snapshot:{source_hash}"]
    stats, rejected = _valid_stats(snap.get("stats") or {})
    delta, interval_errors = _interval(event, stats, previous)
    if delta and previous and previous.get("content_hash"):
        sources.insert(0, f"snapshot:{previous['content_hash']}")
    role = str(event.get("role") or "no_appearance")
    show_pitch = role in ("pitching", "two_way", "no_appearance")
    show_bat = role in ("batting", "two_way", "no_appearance")
    facts: dict[str, dict] = {}

    def fact(key, text, *, evidence="save_verified", scope="season", values=None, source_ids=None, formula=None):
        ident = f"editorial-fact:{stable(world, player_id, source_hash, key, text, story_id if scope == 'intervention' else '')}"
        row = {"fact_id": ident, "kind": key, "text": text, "evidence_class": evidence,
               "scope": scope, "source_ids": list(source_ids or sources[-1:]),
               "values": copy.deepcopy(values or {}), **binding}
        if formula:
            row["formula"] = formula
        facts[ident] = row
        return ident

    name, team = str(player.get("name") or "선수"), str(player.get("team") or "소속 구단")
    identity = fact("player", f"{team} 소속 {name}", scope="identity", values=player)
    anchors, anchor_refs = [], []
    metrics = (("pit_K", "탈삼진", show_pitch), ("bat_HR", "홈런", show_bat),
               ("bat_H", "안타", show_bat), ("pit_W", "승", show_pitch),
               ("bat_RBI", "타점", show_bat), ("bat_SB", "도루", show_bat))
    for key, unit, eligible in metrics:
        n = _count(stats, key)
        if eligible and n is not None and n > 0:
            text = f"시즌 {n}{unit}"
            anchors.append(text)
            anchor_refs.append(fact(key, text, values={key: n}))

    season_lines, season_refs = [], []
    for enabled, prefix, line, family in ((show_pitch, "투수로서", _pitch_line(stats), "pitching"),
                                           (show_bat, "타자로서", _bat_line(stats), "batting")):
        if enabled and line:
            text = f"{prefix} 시즌 기록은 {_copula(line)}."
            season_lines.append(text)
            season_refs.append(fact(f"season_{family}", text, values={k: v for k, v in stats.items() if k.startswith('pit_' if family == 'pitching' else 'bat_') and _number(stats, k) is not None}))

    # Attention is not evidence. Only explicit honor drivers can supplement an
    # otherwise empty snapshot; grades, scores, synthesized slogans cannot.
    for driver in (spotlight or {}).get("drivers") or []:
        key = str(driver.get("key") or "")
        label = str(driver.get("label") or "")
        if key.startswith("honor:") and key not in ("honor:mvp_sweep", "honor:japan_series_peak") and label and rg.audit_public_prose(label).ok:
            ref = fact(key, label, evidence=str(driver.get("provenance") or "manual_confirmed"), scope="career", source_ids=[f"career:{world}:{player_id}:{key}"])
            anchors.append(label)
            anchor_refs.append(ref)
            season_lines.append(f"이전 경력에는 {ts.attach(label, '이가')} 남아 있다.")
            season_refs.append(ref)

    use_delta = bool(delta)
    delta_lines, delta_refs = [], []
    if use_delta:
        for enabled, label, line, family in ((show_pitch, "투구", _pitch_line(delta), "pitching"),
                                            (show_bat, "타격", _bat_line(delta), "batting")):
            if enabled and line:
                text = f"직전 확인 이후 {label} 변화분은 {_copula(line)}."
                delta_lines.append(text)
                delta_refs.append(fact(f"delta_{family}", text, evidence="derived_analysis", scope="observation_interval", values=delta, source_ids=sources, formula="verified_snapshot_delta/1.0.0"))

    primary = anchors[0] if anchors else "현재의 출전 기록"
    primary_refs = anchor_refs[:1] or [identity]
    if delta_lines:
        primary = (_pitch_line(delta) if show_pitch and _pitch_line(delta) else _bat_line(delta))
        primary_refs = delta_refs[:1]
    bundle = game_bundle or {}
    matches = (bool(bundle) and bundle.get("universe_id") == world
               and str(bundle.get("protagonist_id")) == player_id
               and bundle.get("source_hash") == source_hash
               and bundle.get("game_date") == binding["game_date"])
    lead = ((bundle.get("dominant") or {}).get("lead") or {}) if matches else {}
    if lead.get("verified") and lead.get("label") and rg.audit_public_prose(lead["label"]).ok:
        primary = str(lead["label"])
        bundle_sources = list(bundle.get("source_facts") or [])
        if not bundle_sources:
            bundle_sources = [ref for row in (bundle.get("reactions") or {}).get("media") or [] for ref in row.get("fact_ids") or []]
        primary_refs = [fact("dominant", primary, evidence="derived_analysis", scope="observation_interval", source_ids=list(dict.fromkeys(bundle_sources + sources + list(lead.get("fact_ids") or []))), formula="dominant_event/1.0.0")]
        if not use_delta and event.get("kind") == "NO_CHANGE" and bundle.get("observed_delta"):
            replay_delta, replay_errors = _valid_stats(bundle["observed_delta"])
            if not replay_errors and bundle_sources:
                delta, use_delta = replay_delta, True
                for family, label, line in (("pit_", "투구", _pitch_line(delta)), ("bat_", "타격", _bat_line(delta))):
                    if line and any(value > 0 for key, value in delta.items() if key.startswith(family)):
                        text = f"마지막으로 확인한 {label} 변화분은 {_copula(line)}."
                        delta_lines.append(text)
                        delta_refs.append(fact(f"replayed_delta_{family}", text, evidence="derived_analysis",
                                               scope="observation_interval", values=delta, source_ids=bundle_sources,
                                               formula="stored_verified_snapshot_delta/1.0.0"))

    public_action = bool(story and story.get("visibility") in ("public", "social"))
    public_scene = bool(public_action and str((story or {}).get("scene_summary") or "").strip())
    action_id = (story or {}).get("situation") if public_action and not public_scene else None
    action_id = action_id if action_id in packs()[0].get("public_actions", {}) else None
    if public_action:
        if public_scene:
            scene_summary = re.sub(r"\s+", " ", str(story.get("scene_summary") or "")).strip()[:500]
            scene_label = re.sub(r"\s+", " ", str(story.get("scene_label") or scene_summary)).strip()[:72]
            audit = rg.audit_public_prose(scene_summary, protagonist_names=[name])
            if not audit.ok:
                raise ValueError("공개 반응 설명에 현실 몰입을 깨거나 검증이 필요한 표현이 있습니다.")
            source_ids = [str(row) for row in (story.get("source_fact_ids") or []) if row]
            source_ids = source_ids or [f"story:{world}:{story_id}"]
            text = f"{name}과 관련해 공개가 확인된 장면은 다음과 같다. {scene_summary}"
            action_ref = fact("public_scene", text, evidence="fictional_intervention", scope="intervention", source_ids=source_ids)
            primary = scene_label or "공개가 확인된 하루의 장면"
        else:
            verb = packs()[0]["actions"].get(story.get("situation"), "공개적인 선택을 남겼다")
            text = f"{name}: {verb}."
            action_ref = fact("public_action", text, evidence="fictional_intervention", scope="intervention", source_ids=[f"story:{world}:{story_id}"])
            # A nominal subject avoids conjugating arbitrary catalog/user prose.
            primary = packs()[0]["action_subjects"].get(story.get("situation"), "공개적인 선택")
        delta_lines, delta_refs = [text], [action_ref]
        primary_refs = [action_ref]
    privacy = "public_intervention" if public_action else "public_record_background" if story else "public_records"

    insights = []
    if show_pitch:
        innings = _number(stats, "pit_IP")
        ip = stat_engine.innings_float(innings) if innings is not None else 0
        for key, title, tail in (("pit_K", "탈삼진", "삼진으로 잡는 아웃의 비중을 생각하게 하는 수치다"),
                                  ("pit_H", "피안타", "안타 허용을 같은 이닝 단위로 비교할 출발점이다")):
            count = _count(stats, key)
            if ip > 0 and count is not None:
                rate = count * 9 / ip
                text = f"{name}의 시즌 {ts.attach(title, '은는')} 9이닝당 {rate:.2f}개다. {tail}."
                ref = fact(f"rate_{key}", text, evidence="derived_analysis", values={key: count, "pit_outs": stat_engine.innings_to_outs(innings)}, formula=f"{key}*27/pit_outs/1.0.0")
                insights.append((text, ref))
    if show_bat:
        ab, hits, hr = (_count(stats, k) for k in ("bat_AB", "bat_H", "bat_HR"))
        if ab and hits is not None and 0 <= hits <= ab:
            text = f"{name}의 시즌 타율은 {hits / ab:.3f}다. {ab}타수에서 나온 {hits}안타를 같은 기준으로 읽은 값이다."
            insights.append((text, fact("batting_average", text, evidence="derived_analysis", values={"bat_AB": ab, "bat_H": hits}, formula="bat_H/bat_AB/1.0.0")))
        if ab and hits is not None and hr is not None and 0 <= hr <= hits <= ab:
            text = f"{name}의 시즌 타수 가운데 홈런의 비율은 {hr * 100 / ab:.1f}%다. 타석 전체가 아니라 타수를 분모로 삼은 값이다."
            insights.append((text, fact("hr_per_ab", text, evidence="derived_analysis", values={"bat_AB": ab, "bat_HR": hr}, formula="100*bat_HR/bat_AB/1.0.0")))
        if hr is not None and hr > 0:
            text = f"시즌 {hr}홈런으로 만든 루타만 {hr * 4}루타다. 단타·2루타·3루타에서 얻은 루타는 별도다."
            insights.append((text, fact("home_run_bases", text, evidence="derived_analysis", values={"bat_HR": hr}, formula="4*bat_HR/1.0.0")))
    checkpoints = []
    for key, step, unit, eligible in (("pit_K", 50, "탈삼진", show_pitch), ("bat_HR", 10, "홈런", show_bat),
                                      ("bat_H", 50, "안타", show_bat), ("pit_W", 5, "승", show_pitch)):
        n = _count(stats, key)
        if eligible and n:
            target = (n // step + 1) * step
            text = f"시즌 {target}{unit}까지 남은 수는 {target - n}개다."
            if key == "pit_W":
                text = f"시즌 {target}승까지 {target - n}승이 남았다."
            checkpoints.append((text, fact(f"checkpoint_{key}", text, evidence="derived_analysis",
                                          values={key: n, "target": target}, formula="next_round_checkpoint-current/1.0.0")))
    if not insights:
        insights = [(f"{name}의 현재 기록과 앞으로의 결과는 따로 읽을 필요가 있다.", identity)]

    limit = ("타구의 질과 수비 위치는 타수·안타만으로 확정할 수 없다." if role == "batting" else
             "구종별 선택과 주자 상황은 결과 숫자만으로 설명되지 않는다." if role == "pitching" else
             "투타의 역할과 성과를 하나의 인상으로 합칠 수는 없다." if show_pitch and show_bat and _pitch_line(stats) and _bat_line(stats) else
             "성적표의 숫자가 선수의 속마음까지 알려 주는 것은 아니다.")
    if public_action:
        limit = (
            "공개가 확인된 장면과 그 뒤에 이어질 반응은 구분해서 봐야 한다."
            if public_scene
            else "공개된 선택의 취지와 실제로 이어질 행동은 구분해서 봐야 한다."
        )
    evidence = " ".join(delta_lines + season_lines[:2]) or f"{name}의 자세한 성적은 아직 정리할 자료가 충분하지 않다."
    refs = list(dict.fromkeys(delta_refs + season_refs[:2])) or [identity]
    historical_scale = (_count(stats, "pit_K") or 0) >= 300 or (_count(stats, "bat_HR") or 0) >= 50
    established_scale = (_count(stats, "pit_K") or 0) >= 150 or (_count(stats, "bat_HR") or 0) >= 20
    established_scale = established_scale or any(row["kind"] in ("honor:season_mvp", "honor:japan_series_mvp") for row in facts.values())
    impact = "exceptional" if historical_scale else "established" if established_scale else "developing"
    if delta.get("pit_K", 0) >= 15 or delta.get("bat_HR", 0) >= 3:
        impact = "exceptional"
    return {"binding": binding, "name": name, "team": team, "source_hash": source_hash,
            "identity_ref": identity, "facts": facts, "sources": sources,
            "primary": primary, "primary_refs": primary_refs, "anchors": anchors,
            "anchor_refs": anchor_refs, "evidence": evidence, "evidence_refs": refs,
            "insights": insights, "checkpoints": checkpoints, "limit": limit, "privacy": privacy,
            "season_lines": season_lines, "season_refs": season_refs, "delta_lines": delta_lines,
            "impact": impact,
            "scope_label": "공개가 확인된 장면과 시즌 누적" if public_scene else "공개된 선택과 시즌 누적" if public_action else "직전 확인 이후의 변화와 시즌 누적" if delta_lines else "시즌 누적·보관 경력",
            "delta": delta if use_delta else {}, "stats": stats, "role": role,
            "rejected_inputs": rejected + interval_errors,
            "game_instance_id": bundle.get("instance_id") if matches else None,
            "seed": stable(world, player_id, source_hash, event.get("kind"), story_id),
            "public_action": public_action, "public_action_id": action_id,
            "public_scene": public_scene}


def _public_purpose(ctx: dict, purpose: dict) -> dict:
    """Known authored public actions only; no private transcript/state input."""
    action = packs()[0].get("public_actions", {}).get(ctx.get("public_action_id"))
    if not action:
        if not ctx.get("public_scene"):
            return purpose
        updated = copy.deepcopy(purpose)
        updated["deks"] = list(packs()[0]["public_prose"]["deks"])
        return updated
    writing = packs()[0]
    lens = writing["public_lenses"][purpose["id"]]
    updated = copy.deepcopy(purpose)
    updated.update(claim=ts.render_text(lens["claim"], {"slots": {"action_topic": action["topic"]}}),
                   question=lens["question"], public_topic=lens["topic"], public_counterpoint=action["limit"])
    if purpose["lang"] == "ko":
        updated.update(headlines=lens["headlines"], deks=writing["public_prose"]["deks"],
                       public_moves={**writing["public_prose"], "reading": action["readings"][purpose["id"]]})
    return updated


def _memory(memory: dict | None, ctx: dict) -> dict:
    memory = memory if isinstance(memory, dict) else {}
    state = memory.get(MEMORY_KEY)
    state = state if isinstance(state, dict) else {}
    binding = state.get("binding") or {}
    if binding and any(binding.get(k) != ctx["binding"][k] for k in ("universe_id", "protagonist_id")):
        return {"articles": [], "phrases": []}
    articles = state.get("articles") if isinstance(state.get("articles"), list) else []
    phrases = [row for key, source in (("recent_phrases", memory), ("phrases", state))
               for row in (source.get(key) if isinstance(source.get(key), list) else []) if isinstance(row, str)]
    return {"articles": [row for row in articles if isinstance(row, dict)][-50:],
            "phrases": list(dict.fromkeys(phrases))[-240:]}


def _slots(ctx: dict, purpose: dict, index: int) -> tuple[dict, dict]:
    discussion = packs()[1]
    insight, insight_ref = ctx["insights"][index % len(ctx["insights"])]
    anchor, anchor_refs = ctx["primary"], ctx["primary_refs"]
    scope = ctx["scope_label"]
    # Secondary desks allocate different known evidence, not a new outlet label
    # on the same report. The first desk always retains the dominant observation.
    if index > 0 and ctx["anchors"] and not ctx["public_action"]:
        pick = (index - 1) % len(ctx["anchors"])
        anchor, anchor_refs = ctx["anchors"][pick], [ctx["anchor_refs"][pick]]
        scope = "시즌 누적·보관 경력"
    anchor_kind = ctx["facts"][anchor_refs[0]]["kind"]
    if purpose["id"] in ("record", "season") and ctx["checkpoints"]:
        insight, insight_ref = next((row for row in ctx["checkpoints"] if ctx["facts"][row[1]]["kind"] == f"checkpoint_{anchor_kind}"), ctx["checkpoints"][0])
    elif anchor_kind.startswith("bat_"):
        desired = ("home_run_bases", "hr_per_ab", "batting_average") if purpose["id"] == "value" else ("batting_average", "hr_per_ab", "home_run_bases")
        insight, insight_ref = next((row for key in desired for row in ctx["insights"] if ctx["facts"][row[1]]["kind"] == key), (insight, insight_ref))
    elif anchor_kind.startswith("pit_"):
        insight, insight_ref = next((row for row in ctx["insights"] if ctx["facts"][row[1]]["kind"] == "rate_pit_K"), (insight, insight_ref))
    writing = packs()[0]
    impact = ctx["impact"]
    fan_reading = writing["fan_temperature"][impact]["high" if ctx["heat"] >= 8 else "low"]
    if ctx["delta"].get("bat_AB", 0) > 0 and ctx["delta"].get("bat_H") == 0:
        fan_reading = "이번 타격에 안타가 없었던 건 따로 인정하고 다른 역할의 기록을 보자."
    action = writing.get("public_actions", {}).get(ctx.get("public_action_id"))
    if action:
        readings = action["readings"][purpose["id"]]
        insight = ts.render_text(readings[index % len(readings)], {"slots": {"player": ctx["name"]}})
        insight_ref = anchor_refs[0]
        fan_reading = ts.render_text(action["fan_reading"], {"slots": {"player": ctx["name"]}})
        scope = "공개 발언의 취지"
    slots = {"player": ctx["name"], "team": ctx["team"], "anchor": anchor,
             "anchor_predicate": _copula(anchor),
             "period": ctx["binding"]["game_date"] if ctx["binding"]["game_date"] != "unknown" else "현재", "scope_label": scope,
             "evidence": ctx["evidence"], "secondary": insight, "insight": insight,
             "limit": action["limit"] if action else ctx["limit"], "claim": purpose["claim"], "question": purpose["question"] + "?",
             "topic": purpose.get("public_topic", discussion["topics"][purpose["id"]]),
             "counterpoint": purpose.get("public_counterpoint", discussion["counterpoints"][purpose["id"]]),
             "fan_reading": fan_reading,
             "temperature": writing["temperature"][impact],
             "lead_sentence": ctx["delta_lines"][0] if ctx["delta_lines"] else f"{ctx['name']}의 현재 기록에서 눈에 들어오는 것은 {_copula(anchor)}."}
    refs = {"player": [ctx["identity_ref"]], "team": [ctx["identity_ref"]],
            "anchor": anchor_refs, "anchor_predicate": anchor_refs, "period": [ctx["identity_ref"]], "scope_label": anchor_refs,
            "evidence": ctx["evidence_refs"], "secondary": [insight_ref], "insight": [insight_ref],
            "fan_reading": ctx["evidence_refs"], "temperature": ctx["evidence_refs"],
            "lead_sentence": ctx["evidence_refs"]}
    if ctx["public_action"]:
        refs.update({key: list(anchor_refs) for key in ("topic", "claim", "question", "counterpoint", "limit", "fan_reading")})
    return slots, refs


def _near_body(signature: list[str], previous: list[str]) -> bool:
    if not signature or not previous:
        return False
    shared = sum((Counter(signature) & Counter(previous)).values())
    return shared / max(len(signature), len(previous)) >= .6


def _line(template: str, template_id: str, slots: dict, refs: dict, ctx: dict) -> dict:
    text = ts.render_text(template, {"slots": slots})
    fact_ids = list(dict.fromkeys(ref for slot in ts.slot_names(template) for ref in refs.get(slot, []))) or [ctx["identity_ref"]]
    return {"type": "paragraph", "text": text, "template_id": template_id,
            "fact_ids": fact_ids, "evidence_class": "fictional_intervention" if ctx["public_action"] else "derived_analysis",
            "corpus_manifest": packs()[2], "protagonist_relation": "subject", **ctx["binding"]}


def _title(candidates, slots, refs, ctx, rng, blocked, siblings):
    order = list(enumerate(candidates))
    rng.shuffle(order)
    names = [ctx["name"]]
    for index, template in order:
        text = ts.render_text(template, {"slots": slots})
        skeleton = rg.headline_skeleton(text, names)
        if text in blocked["phrases"] or any(skeleton == row.get("skeleton") for row in blocked["articles"][-50:]):
            continue
        if any(rg.is_skeleton_clone(text, other, names) for other in siblings):
            continue
        if rg.audit_public_prose(text, protagonist_names=names).ok:
            return text, index, skeleton
    return None


def _foreign_stat(ctx: dict, language: str, preferred: str = ""):
    stats, role = ctx["stats"], ctx["role"]
    keys = ("bat_HR", "bat_H", "pit_K", "pit_W") if role == "batting" else ("pit_K", "pit_W", "bat_HR", "bat_H")
    if preferred in keys:
        keys = (preferred, *[k for k in keys if k != preferred])
    labels = {"pit_K": ("탈삼진", "奪三振", "strikeouts"), "pit_W": ("승", "勝", "wins"),
              "bat_HR": ("홈런", "本塁打", "home runs"), "bat_H": ("안타", "安打", "hits")}
    for key in keys:
        if role == "pitching" and key.startswith("bat_") or role == "batting" and key.startswith("pit_"):
            continue
        n = _count(stats, key)
        if n is None:
            continue
        ko, ja, en = labels[key]
        return f"{n}{ja}" if language == "ja" else f"{n} {en}", f"{ko} {n}개" if key != "pit_W" else f"{n}승", [r["fact_id"] for r in ctx["facts"].values() if r["kind"] in (key, "season_pitching" if key.startswith("pit_") else "season_batting")]
    return None


def _article(ctx, purpose, index, rng, memory, articles):
    purpose = _public_purpose(ctx, purpose)
    slots, refs = _slots(ctx, purpose, index)
    title = _title(purpose["headlines"], slots, refs, ctx, rng, memory, [a["title"] for a in articles])
    if not title:
        return None
    headline, headline_index, skeleton = title
    dek_index = rng.randrange(len(purpose["deks"]))
    sub = ts.render_text(purpose["deks"][dek_index], {"slots": slots})
    outline_choices = list(OUTLINES)
    rng.shuffle(outline_choices)
    recent = [row.get("outline") for row in memory["articles"][-20:]]
    section = "public" if ctx["public_action"] else "records"
    outline_name, moves = next(((key, moves) for key, moves in outline_choices if f"{purpose['id']}:{section}:{key}" not in recent), outline_choices[0])
    blocks = []
    original, translated = [], []
    if purpose["lang"] != "ko":
        # Public interventions need an authored translation of the action,
        # rather than a foreign article that quietly drops its actual subject.
        if ctx["public_action"]:
            return None
        outline_name = "original_translation_pairs"
        foreign = _foreign_stat(ctx, purpose["lang"], ctx["facts"][refs["anchor"][0]]["kind"])
        if not foreign:
            return None
        slots["foreign_stat"], slots["translated_stat"], fact_ids = foreign
        slots["translated_stat_predicate"] = _copula(slots["translated_stat"])
        refs["foreign_stat"] = refs["translated_stat"] = fact_ids or [ctx["identity_ref"]]
        refs["translated_stat_predicate"] = refs["translated_stat"]
        for _attempt in range(24):
            blocks, original, translated = [], [], []
            for pair_index, options in enumerate(purpose["pair_variants"]):
                choice = rng.randrange(len(options))
                source, translation = options[choice]
                source_row = _line(source, f"{purpose['id']}.source.{pair_index}.{choice}", slots, refs, ctx)
                translated_row = _line(translation, f"{purpose['id']}.ko.{pair_index}.{choice}", slots, refs, ctx)
                original.append(source_row["text"])
                translated.append(translated_row["text"])
                pair_id = stable(ctx["seed"], purpose["id"], pair_index, choice)
                source_row.update({"language": purpose["lang"], "bilingual_pair_id": pair_id})
                translated_row.update({"text": "한국어: " + translated_row["text"],
                                       "original": source_row["text"], "translation_ko": translated[-1],
                                       "fact_ids": list(dict.fromkeys(source_row["fact_ids"] + translated_row["fact_ids"])),
                                       "original_template_id": source_row["template_id"], "translation_method": "authored_bilingual",
                                       "language": "ko", "bilingual_pair_id": pair_id})
                # The existing HTML renders each body entry as a paragraph.
                # Separate entries preserve translation spacing without CSS or
                # renderer changes and without touching archived legacy prose.
                blocks.extend((source_row, translated_row))
            signature = rg.body_signature([b["text"] for b in blocks], [ctx["name"]])
            if not any(_near_body(signature, row.get("body_signature") or []) for row in memory["articles"]):
                break
        else:
            return None
    else:
        for attempt in range(18):
            blocks = []
            for move_index, move in enumerate(moves):
                variants = purpose["public_moves"] if ctx["public_action"] else purpose
                options = packs()[0]["evidence"] if move == "evidence" and not ctx.get("public_action_id") else variants[move]
                choice = (rng.randrange(len(options)) + attempt + move_index) % len(options)
                section_id = f"{section}.{ctx['public_action_id']}" if ctx.get("public_action_id") else section
                blocks.append(_line(options[choice], f"{purpose['id']}.{section_id}.{move}.{choice}", slots, refs, ctx))
            body = [b["text"] for b in blocks]
            signature = rg.body_signature(body, [ctx["name"]])
            if not any(rg.is_body_clone(body, article["body"], [ctx["name"]]) for article in articles) and not any(_near_body(signature, row.get("body_signature") or []) for row in memory["articles"]):
                break
        else:
            return None
    texts = [headline, sub, *[b["text"] for b in blocks]]
    if any(len(b["text"]) > 650 for b in blocks) or any(not rg.audit_public_prose(t, protagonist_names=[ctx["name"]]).ok for t in texts):
        return None
    row = {"id": stable(ctx["seed"], purpose["id"], headline), "outlet": purpose["outlet"],
           "flag": purpose["flag"], "lang": purpose["lang"], "title": headline, "sub": sub,
           "body": [b["text"] for b in blocks], "blocks": blocks, "editorial_purpose": purpose["id"],
           "body_signature": rg.body_signature([b["text"] for b in blocks], [ctx["name"]]),
           "genre": purpose["genre"], "outline_signature": f"{purpose['id']}:{section}:{outline_name}",
           "headline_skeleton": skeleton, "title_template_id": f"{purpose['id']}.headline.{headline_index}",
           "sub_template_id": f"{purpose['id']}.dek.{dek_index}",
           "fact_ids": list(dict.fromkeys([ref for b in blocks for ref in b["fact_ids"]]
                                         + [ref for slot in ts.slot_names(purpose["headlines"][headline_index]) for ref in refs.get(slot, [])])),
           "corpus_manifest": packs()[2], "renderer_version": VERSION, "license_class": "authored",
           "protagonist_relation": "subject", "context_visibility": ctx["privacy"],
           "provenance": "fictional_intervention" if ctx["public_action"] else "fictional_press_simulation",
           "creative_marker": "창작 기사", **ctx["binding"]}
    if ctx.get("public_action_id"):
        row.update(public_action_id=ctx["public_action_id"],
                   title_template_id=f"{purpose['id']}.{ctx['public_action_id']}.headline.{headline_index}",
                   sub_template_id=f"{purpose['id']}.{ctx['public_action_id']}.dek.{dek_index}")
    if original:
        row.update({"original_body": original, "translation_ko": translated, "translation_method": "authored_bilingual"})
    return row


def _thread(ctx, purpose, index, spec, count, rng, memory, used):
    # Keep each visible thread identity stable when only expression heat
    # changes.  Earlier or sharper boards must not consume random state that
    # silently renames a later platform thread.
    rng = random.Random(int(stable(ctx["seed"], "thread-rng", index, spec[0]), 16))
    purpose = _public_purpose(ctx, purpose)
    slots, refs = _slots(ctx, purpose, index)
    action_specific = bool(ctx.get("public_action"))
    discussion_pack = packs()[1]
    discussion = discussion_pack["public"] if action_specific else discussion_pack
    prefix = "discussion.public" if action_specific else "discussion"
    title_candidates = discussion_pack["platform_titles"].get(spec[0]) or discussion["titles"]
    title = _title(title_candidates, slots, refs, ctx, rng, memory, [])
    if not title:
        return None
    text, title_index, _ = title
    # Different purposes are allowed to use the same grammatical title, but not
    # the same displayed title within this publication.
    if text in used:
        remaining = [t for t in title_candidates if ts.render_text(t, {"slots": slots}) not in used]
        title = _title(remaining, slots, refs, ctx, rng, memory, [])
        if not title:
            return None
        text, _ignored_index, _ = title
        title_index = next(i for i, t in enumerate(title_candidates) if ts.render_text(t, {"slots": slots}) == text)
    used.add(text)
    thread_id = stable(ctx["seed"], "thread", index, text)
    claim_id = stable(thread_id, "claim")
    openers = list(enumerate(discussion_pack["platform_openers"].get(spec[0]) or discussion["openers"]))
    rng.shuffle(openers)
    opener = None
    for opener_index, template in openers:
        line = _line(template, f"{prefix}.{spec[0]}.opener.{opener_index}", slots, refs, ctx)
        if line["text"] not in used and line["text"] not in memory["phrases"][-48:] and rg.audit_public_prose(line["text"], protagonist_names=[ctx["name"]]).ok:
            opener = line
            break
    if opener is None:
        return None
    opener.update({"post_id": stable(thread_id, "opener"), "claim_id": claim_id,
                   "thread_claim_id": claim_id, "parent_post_id": None, "addressed_claim_id": None,
                   "stance": "qualify", "emotional_function": "frame_question",
                   "author": _platform_author(ctx, spec[0], "opener", 0),
                   "persona": f"{spec[0]}:opener",
                   "up": _stable_number(2, 75, ctx["seed"], spec[0], "vote", "opener"),
                   "votes_fictional": True})
    used.add(opener["text"])
    replies = []
    sequence = list(STANCES)
    # Keep each thread's discussion order and voices stable for this state.
    offset = index % len(sequence)
    sequence = sequence[offset:] + sequence[:offset]
    if ctx["heat"] <= 4:
        sequence = [s for s in sequence if s not in ("joke", "oppose")] + ["joke", "oppose"]
    for ordinal in range(max(0, count - 1)):
        stance = sequence[ordinal % len(sequence)]
        generic = [(f"{prefix}.{stance}.{i}", t) for i, t in enumerate(discussion["replies"][stance])]
        voice = (discussion_pack.get("voices") or {}).get(spec[0], {}).get(stance) or []
        platform = [(f"discussion.{spec[0]}.{stance}.{i}", t) for i, t in enumerate(voice)]
        hot_voice = (discussion_pack.get("hot_voices") or {}).get(spec[0], {}).get(stance) or []
        hot = [(f"discussion.{spec[0]}.hot.{stance}.{i}", t) for i, t in enumerate(hot_voice)]
        language_voice = (
            (discussion_pack.get("language_voices") or {})
            .get(spec[0], {})
            .get(str(ctx.get("language_level") or 2), {})
            .get(stance)
            or []
        )
        language = [
            (f"discussion.{spec[0]}.language.{ctx.get('language_level')}.{stance}.{i}", t)
            for i, t in enumerate(language_voice)
        ]
        rng.shuffle(generic)
        rng.shuffle(platform)
        rng.shuffle(hot)
        rng.shuffle(language)
        # Platform register always wins. High heat adds sharper authored lines;
        # it never changes facts or grants the model permission to invent any.
        candidates = [
            *language,
            *(hot if ctx["heat"] >= 8 else []),
            *platform,
            *generic,
        ]
        chosen = None
        for template_id, template in candidates:
            line = _line(template, template_id, slots, refs, ctx)
            if line["text"] not in used and line["text"] not in memory["phrases"] and rg.audit_public_prose(line["text"], protagonist_names=[ctx["name"]]).ok:
                chosen = line
                break
        if chosen is None:
            # A finite authored pack can be saturated. Do not fake variety by
            # adding a date, hash, counter, nickname, or mechanical suffix.
            continue
        chosen.update({"post_id": stable(thread_id, "reply", ordinal),
                       "claim_id": stable(thread_id, ordinal, "reply-claim"),
                       "thread_claim_id": claim_id, "parent_post_id": opener["post_id"],
                       "addressed_claim_id": claim_id, "stance": stance,
                       "emotional_function": stance, "persona": f"{spec[0]}:{stance}",
                       "author": _platform_author(ctx, spec[0], stance, ordinal + 1),
                       "up": _stable_number(2, 75, ctx["seed"], spec[0], "vote", ordinal),
                       "votes_fictional": True})
        used.add(chosen["text"])
        replies.append(chosen)
    display_meta = _decorate_thread_display(ctx, spec[0], opener, replies)
    return {"id": thread_id, "board": spec[1], "code": spec[0], "title": text,
            "title_template_id": f"{prefix}.{spec[0]}.title.{title_index}",
            "claim": purpose["claim"], "thread_claim_id": claim_id, "topic": purpose["id"],
            # Existing UI renders comments, not an independent post body.
            # Project the opener as the first comment so every reply has a
            # visible claim without adding or rearranging UI controls.
            "comments": [opener, *replies], "posts": [copy.deepcopy(opener), *copy.deepcopy(replies)],
            "thread_meta": display_meta,
            "stance_sequence": [r["stance"] for r in replies], "corpus_manifest": packs()[2],
            "fact_ids": opener["fact_ids"], "protagonist_relation": "subject",
            "context_visibility": ctx["privacy"], "provenance": "fictional_intervention" if ctx["public_action"] else "fictional_ambient_simulation",
            **ctx["binding"]}


def build(event: dict, *, budget: dict, memory: dict | None = None, universe_id: str = "",
          previous: dict | None = None, story: dict | None = None, story_id: str = "",
          game_bundle: dict | None = None, spotlight: dict | None = None, platforms=(), heat: int = 7,
          language_level: int = 2) -> dict:
    ctx = context(event, universe_id=universe_id, previous=previous, story=story,
                  story_id=story_id, game_bundle=game_bundle, spotlight=spotlight)
    ctx["heat"] = max(1, min(10, int(heat)))
    ctx["language_level"] = max(1, min(5, int(language_level)))
    rng = random.Random(int(ctx["seed"], 16))
    mem = _memory(memory, ctx)
    purposes = packs()[0]["purposes"]
    last_used = {row.get("purpose"): i for i, row in enumerate(mem["articles"])}
    order = [purposes[0], *sorted(purposes[1:], key=lambda row: last_used.get(row["id"], -1))]
    media, boards = [], []
    target_media = max(0, int(budget.get("media") or 0))
    for purpose in order:
        if len(media) >= target_media:
            break
        article = _article(ctx, purpose, len(media), rng, mem, media)
        if article:
            media.append(article)
    platform_names = {"dc": "DCInside", "fmk": "FMKorea", "mlb": "MLBPARK"}
    specs = [(code, platform_names[code]) for code in dict.fromkeys(platforms) if code in platform_names]
    specs += [spec for spec in BOARD_SPECS if spec[0] not in dict(specs)]
    specs = specs[:max(0, int(budget.get("boards") or 0))]
    comment_count = max(len(specs), int(budget.get("comments") or len(specs) * 4)) if specs else 0
    used = {row["title"] for row in media}
    for index, spec in enumerate(specs):
        count = comment_count // len(specs) + (1 if index < comment_count % len(specs) else 0)
        board = _thread(ctx, purposes[index % len(purposes)], index, spec, count, rng, mem, used)
        if board:
            boards.append(board)
    result = {"media": media, "boards": boards}
    result["editorial"] = {"version": VERSION, "corpus_manifest": packs()[2],
                           "binding": ctx["binding"], "game_instance_id": ctx["game_instance_id"],
                           "facts": list(ctx["facts"].values()), "budget": copy.deepcopy(budget),
                           "rejected_inputs": ctx["rejected_inputs"], "impact": ctx["impact"],
                           "expression_heat": ctx["heat"],
                           "community_language_level": ctx["language_level"],
                           "capacity_limited": len(media) < int(budget.get("media") or 0) or len(boards) < int(budget.get("boards") or 0) or sum(len(b["comments"]) for b in boards) < comment_count,
                           "audit": rg.audit_feed(result, names=[ctx["name"]])}
    return result


def remember(memory: dict, publication: dict) -> None:
    """Append bounded routing memory only at the caller's existing commit gate.

    Published pieces and frozen archives are not bounded or changed here.
    Old memory rows remain readable; absence of this new field needs no migration.
    """
    editorial = publication.get("editorial") or {}
    if editorial.get("version") not in ("1.0.0", VERSION) or not editorial.get("binding"):
        return
    binding = editorial["binding"]
    existing = memory.get(MEMORY_KEY)
    existing = existing if isinstance(existing, dict) else {}
    if any((existing.get("binding") or {}).get(k) != binding.get(k) for k in ("universe_id", "protagonist_id")):
        existing = {}
    articles = [row for row in existing.get("articles") or [] if isinstance(row, dict) and row.get("id")]
    for row in publication.get("media") or []:
        articles.append({"id": row["id"], "skeleton": row["headline_skeleton"], "outline": row["outline_signature"],
                         "purpose": row["editorial_purpose"], "body_hash": stable(*row["body"]),
                         "body_signature": list(row.get("body_signature") or [])})
    # De-duplicate stable publication IDs so replay does not age out the window.
    articles = list({row["id"]: row for row in articles}.values())[-50:]
    phrases = [row for row in existing.get("phrases") or [] if isinstance(row, str)]
    phrases.extend(row["title"] for row in publication.get("media") or [])
    for board in publication.get("boards") or []:
        phrases.append(board["title"])
        phrases.extend(row["text"] for row in board.get("comments") or [])
    memory[MEMORY_KEY] = {"version": VERSION, "binding": copy.deepcopy(binding),
                          "articles": articles, "phrases": list(dict.fromkeys(phrases))[-180:]}
