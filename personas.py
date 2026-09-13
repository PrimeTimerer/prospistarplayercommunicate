#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fictional persona and institution registry (master plan 6.1, 6.3).

Every generated quote or reaction is attributed to one of these fictional
voices or to the save protagonist. No entry may reuse the name, logo,
handle, byline, or signature phrasing of a real publication, community
account, or person. Institution labels are team-agnostic; the club name
comes from the save at render time.
"""

from __future__ import annotations

REGISTRY_VERSION = "1.0.0"

# id -> definition. ``circle`` matches the reaction-plan bands (5.4) and
# ``locale`` decides whether an original-language line plus a Korean
# translation is required (14.1).
PERSONAS: dict[str, dict] = {
    # private / inner circle
    "inner_voice": {"label": "나 자신", "role": "self", "circle": "personal", "locale": "ko", "register": "reflective", "sentence_length": "medium"},
    "family_parent": {"label": "부모님", "role": "family", "circle": "personal", "locale": "ko", "register": "warm", "sentence_length": "short"},
    "partner": {"label": "가까운 사람", "role": "partner", "circle": "personal", "locale": "ko", "register": "warm", "sentence_length": "short"},
    "childhood_friend": {"label": "어릴 적 친구", "role": "friend", "circle": "personal", "locale": "ko", "register": "casual", "sentence_length": "short"},
    # clubhouse
    "veteran_teammate": {"label": "베테랑 동료", "role": "teammate", "circle": "club", "locale": "ko", "register": "plain", "sentence_length": "medium"},
    "catcher": {"label": "주전 포수", "role": "teammate", "circle": "club", "locale": "ko", "register": "technical", "sentence_length": "medium"},
    "rookie_teammate": {"label": "후배 선수", "role": "rookie", "circle": "club", "locale": "ko", "register": "polite", "sentence_length": "short"},
    "position_rival": {"label": "같은 자리를 노리는 동료", "role": "rival_teammate", "circle": "club", "locale": "ko", "register": "guarded", "sentence_length": "short"},
    "manager": {"label": "감독", "role": "manager", "circle": "club", "locale": "ko", "register": "authoritative", "sentence_length": "medium"},
    "bench_coach": {"label": "수석코치", "role": "coach", "circle": "club", "locale": "ko", "register": "practical", "sentence_length": "medium"},
    "pitching_coach": {"label": "투수코치", "role": "coach", "circle": "club", "locale": "ko", "register": "technical", "sentence_length": "medium"},
    "hitting_coach": {"label": "타격코치", "role": "coach", "circle": "club", "locale": "ko", "register": "technical", "sentence_length": "medium"},
    "trainer": {"label": "트레이너", "role": "medical", "circle": "club", "locale": "ko", "register": "cautious", "sentence_length": "medium"},
    "team_doctor": {"label": "팀 닥터", "role": "medical", "circle": "club", "locale": "ko", "register": "clinical", "sentence_length": "medium"},
    "club_pr": {"label": "구단 홍보팀", "role": "club_staff", "circle": "club", "locale": "ko", "register": "formal", "sentence_length": "medium"},
    "front_office": {"label": "구단 운영부", "role": "front_office", "circle": "club", "locale": "ko", "register": "formal", "sentence_length": "long"},
    "agent": {"label": "에이전트", "role": "agent", "circle": "personal", "locale": "ko", "register": "strategic", "sentence_length": "medium"},
    "mentor": {"label": "은사", "role": "mentor", "circle": "personal", "locale": "ko", "register": "warm", "sentence_length": "medium"},
    # opponents
    "opposing_star": {"label": "상대 팀 간판 선수", "role": "opponent", "circle": "league", "locale": "ko", "register": "guarded", "sentence_length": "short"},
    "opposing_catcher": {"label": "상대 팀 포수", "role": "opponent", "circle": "league", "locale": "ko", "register": "technical", "sentence_length": "short"},
    "opposing_scout": {"label": "상대 전력분석원", "role": "opponent_staff", "circle": "league", "locale": "ko", "register": "analytical", "sentence_length": "long"},
    # press and broadcast (fictional institutions)
    "beat_reporter": {"label": "구단 담당 기자", "role": "press", "circle": "local", "locale": "ko", "register": "reportorial", "sentence_length": "medium", "institution": "현장 취재진"},
    "local_paper": {"label": "지역 일간지 야구 데스크", "role": "press", "circle": "local", "locale": "ko", "register": "reportorial", "sentence_length": "medium", "institution": "지역 일간지"},
    "local_radio": {"label": "지역 라디오 스포츠 진행자", "role": "broadcast", "circle": "local", "locale": "ko", "register": "conversational", "sentence_length": "short", "institution": "지역 라디오"},
    "national_desk": {"label": "전국 야구 데스크", "role": "press", "circle": "national", "locale": "ko", "register": "formal", "sentence_length": "long", "institution": "전국 야구 데스크"},
    "columnist": {"label": "전국지 칼럼니스트", "role": "press", "circle": "national", "locale": "ko", "register": "opinion", "sentence_length": "long", "institution": "전국지 칼럼"},
    "tv_analyst": {"label": "중계 해설위원", "role": "broadcast", "circle": "national", "locale": "ko", "register": "conversational", "sentence_length": "medium", "institution": "중계석"},
    "former_pitcher": {"label": "전직 투수 해설", "role": "analyst", "circle": "national", "locale": "ko", "register": "technical", "sentence_length": "medium", "institution": "전직 투수 해설"},
    "former_hitter": {"label": "전직 타자 해설", "role": "analyst", "circle": "national", "locale": "ko", "register": "technical", "sentence_length": "medium", "institution": "전직 타자 해설"},
    "record_desk": {"label": "기록 분석실", "role": "analyst", "circle": "historical", "locale": "ko", "register": "precise", "sentence_length": "medium", "institution": "기록 분석실"},
    "archive_desk": {"label": "야구사 자료실", "role": "analyst", "circle": "historical", "locale": "ko", "register": "reflective", "sentence_length": "long", "institution": "야구사 자료실"},
    "data_analyst": {"label": "데이터 분석가", "role": "analyst", "circle": "national", "locale": "ko", "register": "analytical", "sentence_length": "long", "institution": "데이터 칼럼"},
    # supporters
    "home_supporter": {"label": "홈 응원석 단골", "role": "fan", "circle": "local", "locale": "ko", "register": "casual", "sentence_length": "short"},
    "skeptical_fan": {"label": "신중한 팬", "role": "fan", "circle": "local", "locale": "ko", "register": "skeptical", "sentence_length": "short"},
    "rival_fan": {"label": "원정 응원석", "role": "rival_fan", "circle": "league", "locale": "ko", "register": "teasing", "sentence_length": "short"},
    "youth_player": {"label": "유소년 야구부 선수", "role": "fan", "circle": "local", "locale": "ko", "register": "earnest", "sentence_length": "short"},
    "stat_fan": {"label": "기록 파는 팬", "role": "fan", "circle": "national", "locale": "ko", "register": "analytical", "sentence_length": "medium"},
    "longtime_fan": {"label": "30년 팬", "role": "fan", "circle": "local", "locale": "ko", "register": "nostalgic", "sentence_length": "medium"},
    # international (original language + Korean translation required)
    "ja_local_fan": {"label": "現地ファン", "label_ko": "현지 팬 (일본)", "role": "fan", "circle": "international", "locale": "ja", "register": "casual", "sentence_length": "short"},
    "ja_beat_reporter": {"label": "番記者", "label_ko": "담당 기자 (일본, 창작·번역)", "role": "press", "circle": "international", "locale": "ja", "register": "reportorial", "sentence_length": "medium"},
    "ja_analyst": {"label": "解説者", "label_ko": "해설자 (일본, 창작·번역)", "role": "analyst", "circle": "international", "locale": "ja", "register": "technical", "sentence_length": "medium"},
    "ja_rival_fan": {"label": "相手チームのファン", "label_ko": "상대 팀 팬 (일본, 창작·번역)", "role": "rival_fan", "circle": "international", "locale": "ja", "register": "teasing", "sentence_length": "short"},
    "ja_record_desk": {"label": "記録デスク", "label_ko": "기록 데스크 (일본, 창작·번역)", "role": "analyst", "circle": "international", "locale": "ja", "register": "precise", "sentence_length": "medium"},
    "ja_club_staff": {"label": "球団関係者", "label_ko": "구단 관계자 (일본, 창작·번역)", "role": "club_staff", "circle": "international", "locale": "ja", "register": "formal", "sentence_length": "medium"},
    "ja_veteran": {"label": "元選手", "label_ko": "전직 선수 (일본, 창작·번역)", "role": "analyst", "circle": "international", "locale": "ja", "register": "reflective", "sentence_length": "medium"},
    "en_overseas_desk": {"label": "Overseas baseball desk", "label_ko": "해외 야구 데스크 (창작·번역)", "role": "press", "circle": "international", "locale": "en", "register": "reportorial", "sentence_length": "medium"},
    "zh_forum": {"label": "台灣棒球論壇", "label_ko": "대만 야구 포럼 반응 (창작·번역)", "role": "fan", "circle": "international", "locale": "zh-Hant", "register": "casual", "sentence_length": "short"},
    "es_radio": {"label": "Radio de béisbol latinoamericana", "label_ko": "라틴아메리카 야구 라디오 반응 (창작·번역)", "role": "broadcast", "circle": "international", "locale": "es", "register": "conversational", "sentence_length": "short"},
}

CIRCLES = ("personal", "club", "local", "national", "international", "historical")


def get(persona_id: str) -> dict:
    row = PERSONAS.get(str(persona_id))
    if row is None:
        raise KeyError(f"unknown persona {persona_id!r}")
    return dict(row, id=str(persona_id))


def label(persona_id: str, *, korean: bool = False) -> str:
    row = PERSONAS.get(str(persona_id)) or {}
    if korean and row.get("label_ko"):
        return str(row["label_ko"])
    return str(row.get("label") or persona_id)


def display_label(persona_id: str) -> str:
    """Original label plus Korean gloss for foreign personas (14.1)."""
    row = PERSONAS.get(str(persona_id)) or {}
    if row.get("label_ko"):
        return f"{row['label']} — {row['label_ko']}"
    return str(row.get("label") or persona_id)


def all_labels() -> list[str]:
    values = []
    for row in PERSONAS.values():
        values.append(str(row["label"]))
        if row.get("label_ko"):
            values.append(str(row["label_ko"]))
    return values


def is_fictional(name: str) -> bool:
    return str(name) in set(all_labels()) or str(name) in PERSONAS


def by_circle(circle: str) -> list[str]:
    return [persona_id for persona_id, row in PERSONAS.items() if row.get("circle") == circle]


def by_role(role: str) -> list[str]:
    return [persona_id for persona_id, row in PERSONAS.items() if row.get("role") == role]


# Which personas may voice a reply to the protagonist for a given target.
TARGET_PERSONAS = {
    "manager": ["manager"],
    "coach": ["pitching_coach", "hitting_coach", "bench_coach"],
    "teammate": ["veteran_teammate", "catcher"],
    "rookie": ["rookie_teammate"],
    "reporter": ["beat_reporter", "national_desk"],
    "fans": ["home_supporter", "longtime_fan"],
    "rival": ["opposing_star", "opposing_catcher"],
    "agent": ["agent"],
    "family": ["family_parent"],
    "partner": ["partner"],
    "front_office": ["front_office"],
    "medical": ["trainer", "team_doctor"],
    "self": ["inner_voice"],
}


def responders_for(target: str | None) -> list[str]:
    return list(TARGET_PERSONAS.get(str(target or "self"), ["inner_voice"]))
