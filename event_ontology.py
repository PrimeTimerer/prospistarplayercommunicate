#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SP.* master event ontology (master plan sections 5.2 and 5.5).

Every Star Player mechanic maps to a stable hierarchical event type. The
first executable slice implements deterministic scene templates for five
domains (roster, game-post, relationship, media, milestone); every other
domain is registered so that an event can be stored and routed before it
has bespoke prose. ``MECHANICAL_ONLY`` and ``INTERNAL_SIGNAL`` are
deliberate non-events: the world never reacts to a settings toggle or to an
internal score.
"""

from __future__ import annotations

ONTOLOGY_VERSION = "1.0.0"

MECHANICAL_ONLY = "MECHANICAL_ONLY"
INTERNAL_SIGNAL = "INTERNAL_SIGNAL"

SLICE_ONE_DOMAINS = ("SP.ROSTER", "SP.GAME.POST", "SP.RELATION", "SP.MEDIA", "SP.MILESTONE")

# domain id -> definition. Families are (suffix, Korean label).
DOMAINS: dict[str, dict] = {
    "SP.IDENTITY": {
        "label": "선수 정체성",
        "surface": "Career start, original player, imported player, age, handedness, role",
        "owners": ["club PR", "local beat", "family", "fans"],
        "families": [
            ("DEBUT_PROFILE", "데뷔 프로필"),
            ("HOMETOWN", "고향·출신"),
            ("DRAFT_PEDIGREE", "드래프트 이력"),
            ("FOREIGN_PLAYER_CONTEXT", "외국인 선수 맥락"),
            ("IDENTITY_CORRECTION", "정체성 정정"),
            ("WORLD_SETUP", "리그 구성 등록"),
            ("REINCARNATION_SETUP", "설정형 프로필 등록"),
        ],
    },
    "SP.ROSTER": {
        "label": "등록·1군/2군",
        "surface": "First/second team, registration, bench, regular",
        "owners": ["manager", "coach", "beat reporter", "teammate"],
        "families": [
            ("CALL_UP", "1군 승격"),
            ("OPTION", "2군 강등"),
            ("OMISSION", "등록 제외"),
            ("FIRST_START", "첫 선발 출전"),
            ("REGULAR", "주전 확정"),
            ("LINEUP_DEMOTION", "타순·역할 강등"),
            ("EMERGENCY_REPLACEMENT", "긴급 대체"),
            ("RULE", "등록 규정 맥락"),
            ("TRANSACTION", "현역 드래프트·이적"),
            ("TRYOUT", "트라이아웃"),
        ],
    },
    "SP.ROLE": {
        "label": "보직·역할",
        "surface": "Position and pitching-role changes, player-manager, two-way play",
        "owners": ["manager", "analyst", "agent", "rival"],
        "families": [
            ("PITCHING_CHANGE", "선발·불펜·마무리 전환"),
            ("POSITION_CHANGE", "수비 위치 변경"),
            ("LINEUP", "타순 결정"),
            ("TWO_WAY", "이도류 운용"),
            ("PLAYER_MANAGER", "선수 겸 감독"),
            ("ROLE_DISPUTE", "역할 이견"),
            ("ROLE_REQUEST", "역할 요청"),
        ],
    },
    "SP.GAME.PRE": {
        "label": "경기 전",
        "surface": "Selection, matchup, streak, venue, standings pressure",
        "owners": ["preview desk", "opposing scout", "fans"],
        "families": [
            ("ANNOUNCED_START", "선발 예고"),
            ("REST_DAY", "휴식일"),
            ("RIVALRY", "라이벌전 예고"),
            ("RECORD_WATCH", "기록 관전 포인트"),
            ("RETURN_GAME", "복귀전"),
            ("MUST_WIN", "필승 맥락"),
        ],
    },
    "SP.GAME.PITCH": {
        "label": "투구",
        "surface": "Every pitching action",
        "owners": ["catcher", "pitching coach", "analyst", "crowd"],
        "families": [
            ("PITCH_MIX", "구종 배합"),
            ("LINE", "투구 기록"),
            ("VELOCITY", "구속"),
            ("COMMAND", "제구"),
            ("STRIKEOUT", "탈삼진"),
            ("WALK", "볼넷"),
            ("HIT_ALLOWED", "피안타"),
            ("HR_ALLOWED", "피홈런"),
            ("INNING_ESCAPE", "위기 탈출"),
            ("COMPLETE_GAME", "완투"),
            ("SHUTOUT", "완봉"),
            ("NO_HIT_BID", "노히트 도전"),
            ("PERFECT_GAME_BID", "퍼펙트 도전"),
            ("NO_HITTER", "노히트 노런"),
            ("PERFECT_GAME", "퍼펙트게임"),
        ],
    },
    "SP.GAME.BAT": {
        "label": "타격",
        "surface": "Every batting action",
        "owners": ["hitting coach", "teammate", "announcer", "fans"],
        "families": [
            ("PLATE_APPEARANCE", "타석"),
            ("QUALITY_CONTACT", "정타"),
            ("STRIKEOUT", "삼진"),
            ("WALK", "볼넷"),
            ("HIT", "안타"),
            ("HOME_RUN", "홈런"),
            ("BUNT", "번트"),
            ("CLUTCH_HIT", "결정타"),
            ("CYCLE_WATCH", "사이클링 히트 도전"),
            ("WALK_OFF", "끝내기"),
            ("HITLESS_GAME", "무안타 경기"),
        ],
    },
    "SP.GAME.FIELD": {
        "label": "수비",
        "surface": "Defense and throwing",
        "owners": ["fielding coach", "pitcher", "local press"],
        "families": [
            ("ROUTINE_PLAY", "평범한 수비"),
            ("ERROR", "실책"),
            ("DIFFICULT_CATCH", "호수비"),
            ("ASSIST", "보살"),
            ("DOUBLE_PLAY", "병살"),
            ("POSITION_ADJUSTMENT", "수비 위치 조정"),
            ("GAME_SAVING_DEFENSE", "승리를 지킨 수비"),
        ],
    },
    "SP.GAME.RUN": {
        "label": "주루",
        "surface": "Baserunning",
        "owners": ["coach", "opponent catcher", "fans"],
        "families": [
            ("STEAL_ATTEMPT", "도루 시도"),
            ("ADVANCE", "진루"),
            ("TAG_UP", "태그업"),
            ("PICKOFF", "견제사"),
            ("HESITATION", "주루 판단 지연"),
            ("WINNING_RUN", "결승 득점"),
        ],
    },
    "SP.GAME.POST": {
        "label": "경기 후",
        "surface": "Game completion and missions",
        "owners": ["manager", "clubhouse", "media desk"],
        "families": [
            ("WIN", "승리"),
            ("LOSS", "패배"),
            ("NO_DECISION", "노디시전"),
            ("PLAYER_OF_GAME", "경기 수훈"),
            ("MISSION", "시즌 미션 결과"),
            ("NO_APPEARANCE", "미출전"),
            ("POSTGAME_QUOTE", "경기 후 한마디"),
            ("RECAP", "경기 요약"),
            ("QUALITY_START", "퀄리티 스타트"),
        ],
    },
    "SP.TRAINING": {
        "label": "훈련·장비·시설",
        "surface": "Practice, equipment, houses/facilities, abilities, pitches",
        "owners": ["coach", "mentor", "teammate", "specialist"],
        "families": [
            ("TRAINING_CHOICE", "훈련 선택"),
            ("PLATEAU", "정체"),
            ("BREAKTHROUGH", "돌파"),
            ("MENTOR_LESSON", "선배 지도"),
            ("PITCH_ORIGINAL", "오리지널 구종"),
            ("ABILITY_LEARNED", "능력 습득"),
            ("FACILITY", "시설"),
            ("ITEM", "아이템"),
            ("LEARNING", "관계 기반 습득"),
            ("RELEARN", "재습득"),
            ("EXTRA_WORK", "추가 훈련"),
            ("ROUTINE_CHANGE", "루틴 변경"),
            ("VIDEO_STUDY", "영상 분석"),
        ],
    },
    "SP.HEALTH": {
        "label": "컨디션·부상",
        "surface": "Condition, fatigue, rest, injury, recovery",
        "owners": ["medical staff", "manager", "family", "press"],
        "families": [
            ("SORENESS", "통증·불편"),
            ("SKIPPED_WORK", "훈련 생략"),
            ("MEDICAL_CHECK", "검진"),
            ("INJURED_LIST", "부상자 명단"),
            ("REHAB_GAME", "재활 경기"),
            ("RETURN", "복귀"),
            ("WORKLOAD_WARNING", "과부하 경고"),
            ("AGING_RECOVERY", "회복력 변화"),
            ("CONDITION", "컨디션 회복"),
            ("MEDICAL", "몸 상태 회복"),
            ("REST", "휴식 선택"),
        ],
    },
    "SP.RELATION": {
        "label": "관계",
        "surface": "Meetings, meals, player relationships, status gains",
        "owners": ["participant personas", "clubhouse"],
        "families": [
            ("INTRODUCTION", "첫 만남"),
            ("MEAL", "식사"),
            ("ADVICE", "조언"),
            ("DISAGREEMENT", "의견 충돌"),
            ("TRUST_GAIN", "신뢰 상승"),
            ("TRUST_LOSS", "신뢰 하락"),
            ("MENTOR", "멘토"),
            ("PROTEGE", "후배 지도"),
            ("RECONCILIATION", "화해"),
            ("ENCOURAGEMENT", "격려"),
            ("PRIVATE_MEETING", "비공개 면담"),
            ("OPPONENT_EXCHANGE", "상대 선수와의 대화"),
            ("MANAGER", "감독과의 관계"),
        ],
    },
    "SP.PRIVATE": {
        "label": "사생활·외출",
        "surface": "Walking, outings, destinations, shopping, housing, hobbies",
        "owners": ["private contacts", "local fans", "gossip channel"],
        "families": [
            ("OUTING_DISCOVERY", "새 장소 발견"),
            ("QUIET_DAY", "조용한 하루"),
            ("PUBLIC_SIGHTING", "목격"),
            ("HOUSING", "이사·주거"),
            ("MEAL", "식사"),
            ("HOBBY", "취미"),
            ("FAMILY_VISIT", "가족 방문"),
            ("FAMILY_CALL", "가족 통화"),
            ("PRIVACY_REQUEST", "사생활 보호 요청"),
            ("PURCHASE", "구매"),
            ("DESTINATION", "장소 방문"),
            ("CONFESSION", "솔직한 고백"),
        ],
    },
    "SP.ROMANCE": {
        "label": "연애·인생 결정",
        "surface": "Girlfriend/partner candidates and life decisions",
        "owners": ["partner", "close friend", "restrained gossip"],
        "families": [
            ("INTRODUCTION", "만남"),
            ("GROWING_CLOSENESS", "가까워짐"),
            ("MISUNDERSTANDING", "오해"),
            ("SUPPORT", "지지"),
            ("PUBLIC_RUMOR", "공개 소문"),
            ("BOUNDARY", "경계 설정"),
            ("COMMITMENT", "약속"),
        ],
    },
    "SP.MEDIA": {
        "label": "미디어",
        "surface": "Interviews, press conferences, criticism, rumor",
        "owners": ["beat", "national desk", "TV analyst"],
        "families": [
            ("QUOTE_REQUEST", "인터뷰 요청"),
            ("QUOTE", "공개 발언"),
            ("REFUSAL", "인터뷰 거절"),
            ("APOLOGY", "공개 사과"),
            ("PREDICTION", "예고·전망"),
            ("CONTROVERSY", "논쟁"),
            ("CORRECTION", "정정"),
            ("FEATURE", "특집"),
            ("RESPONSE_TO_CRITICISM", "비판에 답함"),
            ("PRAISE_TEAMMATE", "동료 칭찬"),
            ("PRAISE_OPPONENT", "상대 칭찬"),
            ("CALL_OUT_OPPONENT", "상대 지목"),
            ("DEESCALATION", "대결 구도 완화"),
            ("SELF_CRITICISM", "자기 비판"),
            ("SILENCE", "침묵"),
            ("DEFLECT", "화제 전환"),
        ],
    },
    "SP.PUBLIC": {
        "label": "팬·평판·상품",
        "surface": "Fan service, goods income, reputation",
        "owners": ["supporters", "club business", "sponsor"],
        "families": [
            ("AUTOGRAPH", "사인회"),
            ("YOUTH_CLINIC", "유소년 클리닉"),
            ("MERCHANDISE", "상품 수요"),
            ("BOOING", "야유"),
            ("CHANT", "응원가"),
            ("CHARITY", "자선"),
            ("SPONSOR_INTEREST", "스폰서 관심"),
            ("FAN_MESSAGE", "팬 메시지"),
            ("FAN_SERVICE", "팬 서비스"),
            ("COMMUNITY_EVENT", "지역 행사"),
        ],
    },
    "SP.CONTRACT": {
        "label": "계약·연봉",
        "surface": "Salary negotiation, role request, contract and agent work",
        "owners": ["agent", "front office", "business press"],
        "families": [
            ("OFFER", "제시"),
            ("COUNTEROFFER", "역제시"),
            ("NEGOTIATION_ROUND", "협상 라운드"),
            ("INCENTIVE", "인센티브"),
            ("BREAKDOWN", "결렬"),
            ("AGREEMENT", "합의"),
            ("TRADE_RUMOR", "트레이드·FA 소문"),
            ("AGENT_MEETING", "에이전트 면담"),
            ("PRINCIPLE_STATEMENT", "계약 원칙 표명"),
            ("BUSINESS", "상품·수입"),
            ("NEGOTIATION", "연봉 협상"),
        ],
    },
    "SP.CALENDAR": {
        "label": "시즌 달력",
        "surface": "Camp, opening, interleague, All-Star, postseason, offseason",
        "owners": ["league desk", "club desk", "national press"],
        "families": [
            ("CAMP_ARRIVAL", "캠프 합류"),
            ("OPENING_ROSTER", "개막 로스터"),
            ("MIDSEASON_CHECKPOINT", "시즌 중간 점검"),
            ("PENNANT_RACE", "페넌트레이스"),
            ("CLIMAX_SERIES", "클라이맥스 시리즈"),
            ("JAPAN_SERIES", "일본시리즈"),
            ("WINTER_TRAINING", "겨울 훈련"),
            ("SKIP_CAPTURE", "일정 건너뛰기 복원"),
            ("MISSED_FREE_ACTION", "놓친 자유 행동"),
            ("ANNIVERSARY", "기념일"),
        ],
    },
    "SP.AWARD": {
        "label": "수상",
        "surface": "Monthly, seasonal, postseason, selection awards",
        "owners": ["league desk", "former players", "sponsors"],
        "families": [
            ("MONTHLY_MVP", "월간 MVP"),
            ("BEST_NINE", "베스트나인"),
            ("GOLDEN_GLOVE", "골든글러브"),
            ("MVP", "시즌 MVP"),
            ("ROOKIE_AWARD", "신인상"),
            ("JAPAN_SERIES_MVP", "일본시리즈 MVP"),
            ("ALL_STAR_SELECTION", "올스타 선정"),
            ("TITLE", "타이틀"),
        ],
    },
    "SP.MILESTONE": {
        "label": "기록·마일스톤",
        "surface": "Career/season/rookie/team/league records",
        "owners": ["record desk", "statistician", "archive desk"],
        "families": [
            ("APPROACHING", "기록 근접"),
            ("TIED", "기록 동률"),
            ("ACHIEVED", "기록 달성"),
            ("SURPASSED", "기록 경신"),
            ("FIRST_EVER", "최초"),
            ("FASTEST", "최단"),
            ("YOUNGEST_OLDEST", "최연소·최고령"),
            ("FRANCHISE_RECORD", "구단 기록"),
            ("SEASON_THRESHOLD", "시즌 기준선 통과"),
            ("CAREER_THRESHOLD", "통산 기준선 통과"),
        ],
    },
    "SP.STANDINGS": {
        "label": "순위",
        "surface": "Team and league context",
        "owners": ["national desk", "rival fans", "clubhouse"],
        "families": [
            ("LEAD_CHANGE", "선두 교체"),
            ("ELIMINATION", "탈락 확정"),
            ("MAGIC_NUMBER", "매직넘버"),
            ("CS_LINE", "클라이맥스 시리즈 경계"),
            ("TITLE", "우승 확정"),
        ],
    },
    "SP.AGING": {
        "label": "노화·적응",
        "surface": "Decline, adaptation, reduced role",
        "owners": ["coach", "analyst", "younger teammate"],
        "families": [
            ("VELOCITY_LOSS", "구속 저하"),
            ("RECOVERY_CHANGE", "회복 변화"),
            ("SKILL_ADAPTATION", "기술 적응"),
            ("VETERAN_LEADERSHIP", "베테랑 리더십"),
            ("SUCCESSION", "세대교체"),
            ("DECLINE", "능력 하락"),
        ],
    },
    "SP.LEGACY": {
        "label": "은퇴·유산",
        "surface": "Retirement and historical memory",
        "owners": ["archive desk", "alumni", "fans", "family"],
        "families": [
            ("RETIREMENT_CONSIDERATION", "은퇴 고민"),
            ("FINAL_SEASON", "마지막 시즌"),
            ("CEREMONY", "은퇴식"),
            ("NUMBER_DEBATE", "등번호 논쟁"),
            ("HALL_OF_FAME", "명예의 전당 논의"),
            ("RETROSPECTIVE", "회고"),
            ("PAST_CAREER_LINK", "과거 커리어 연결"),
        ],
    },
    "SP.USER": {
        "label": "사용자 개입",
        "surface": "User-authored intervention",
        "owners": ["target-specific reaction graph"],
        "families": [
            ("DECLARATION", "선언"),
            ("PRIVATE_CONVERSATION", "비공개 대화"),
            ("CHALLENGE", "도전"),
            ("APOLOGY", "사과"),
            ("PROMISE", "약속"),
            ("RUMOR_SEED", "소문의 씨앗"),
            ("CHARITABLE_ACT", "선행"),
            ("CONFRONTATION", "대립"),
            ("PROP", "서사 소품"),
            ("CONSOLE", "위로"),
            ("PRAISE", "칭찬"),
        ],
    },
}


def all_event_types() -> list[str]:
    rows = []
    for domain_id, definition in DOMAINS.items():
        for suffix, _label in definition["families"]:
            rows.append(f"{domain_id}.{suffix}")
    return rows


_EVENT_TYPES = frozenset(all_event_types())


def is_registered(event_type: object) -> bool:
    return str(event_type or "") in _EVENT_TYPES


def domain_of(event_type: str) -> str | None:
    text = str(event_type or "")
    best = None
    for domain_id in DOMAINS:
        if text == domain_id or text.startswith(domain_id + "."):
            if best is None or len(domain_id) > len(best):
                best = domain_id
    return best


def label_of(event_type: str) -> str:
    domain = domain_of(event_type)
    if not domain:
        return str(event_type)
    suffix = str(event_type)[len(domain) + 1 :]
    for family, label in DOMAINS[domain]["families"]:
        if family == suffix:
            return f"{DOMAINS[domain]['label']} · {label}"
    return DOMAINS[domain]["label"]


def in_slice_one(event_type: str) -> bool:
    return domain_of(event_type) in SLICE_ONE_DOMAINS


# --------------------------------------------------------------------------
# Official FAQ coverage checklist (5.5)
# --------------------------------------------------------------------------

FAQ_CHECKLIST: list[dict] = [
    {"mechanic": "Maximum thirty-year career", "mapping": ["SP.CALENDAR.ANNIVERSARY", "SP.AGING.DECLINE", "SP.LEGACY.RETROSPECTIVE"]},
    {"mechanic": "Spirits-created or original player", "mapping": ["SP.IDENTITY.DEBUT_PROFILE"]},
    {"mechanic": "Star Player-created player reuse", "mapping": ["SP.IDENTITY.DEBUT_PROFILE", "SP.LEGACY.PAST_CAREER_LINK"]},
    {"mechanic": "Manager, coach, overseas-transfer player availability", "mapping": ["SP.IDENTITY.FOREIGN_PLAYER_CONTEXT", "SP.ROSTER.RULE"]},
    {"mechanic": "Difficulty and pitch-speed settings", "mapping": [MECHANICAL_ONLY]},
    {"mechanic": "First-team registration size", "mapping": ["SP.ROSTER.RULE"]},
    {"mechanic": "Active-player draft", "mapping": ["SP.ROSTER.TRANSACTION"]},
    {"mechanic": "Tryout", "mapping": ["SP.ROSTER.TRYOUT"], "game_support": "unavailable",
     "restriction": "The official FAQ says Star Player has no tryout. Retain this ID for legacy or fictional history only.",
     "source_url": "https://www.konami.com/games/2026_support/faq/0/jp/ja/pc/item?no=106"},
    {"mechanic": "Edited-team participation", "mapping": ["SP.IDENTITY.WORLD_SETUP"]},
    {"mechanic": "Two-way play", "mapping": ["SP.ROLE.TWO_WAY"]},
    {"mechanic": "Player-manager", "mapping": ["SP.ROLE.PLAYER_MANAGER"], "game_support": "setup_only",
     "restriction": "Available only when the edited team starts with the same person as player and manager; no mid-career switch.",
     "source_url": "https://www.konami.com/games/2026_support/faq/0/jp/ja/ps5/item?no=109"},
    {"mechanic": "Item purchase", "mapping": ["SP.TRAINING.ITEM", "SP.PRIVATE.PURCHASE"]},
    {"mechanic": "House increases training-facility capacity", "mapping": ["SP.PRIVATE.HOUSING", "SP.TRAINING.FACILITY"]},
    {"mechanic": "Walking unlocks outing destinations", "mapping": ["SP.PRIVATE.OUTING_DISCOVERY"]},
    {"mechanic": "Meetings/outings raise personal statuses", "mapping": ["SP.RELATION.MEAL", "SP.PRIVATE.DESTINATION"]},
    {"mechanic": "Salary negotiation, including bounded rounds", "mapping": ["SP.CONTRACT.NEGOTIATION", "SP.CONTRACT.NEGOTIATION_ROUND"]},
    {"mechanic": "Promotion to first team", "mapping": ["SP.ROSTER.CALL_UP"]},
    {"mechanic": "Becoming a regular", "mapping": ["SP.ROSTER.REGULAR"]},
    {"mechanic": "Season mission success/failure", "mapping": ["SP.GAME.POST.MISSION"]},
    {"mechanic": "Fielder main-position change", "mapping": ["SP.ROLE.POSITION_CHANGE"]},
    {"mechanic": "Starter/reliever role change", "mapping": ["SP.ROLE.PITCHING_CHANGE"]},
    {"mechanic": "Defense/baserunning scene participation setting", "mapping": [MECHANICAL_ONLY]},
    {"mechanic": "Pitcher batting control setting", "mapping": [MECHANICAL_ONLY, "SP.GAME.BAT.PLATE_APPEARANCE"]},
    {"mechanic": "Original pitch learning", "mapping": ["SP.TRAINING.PITCH_ORIGINAL"]},
    {"mechanic": "Relationship-dependent learnable abilities/pitches", "mapping": ["SP.RELATION.MENTOR", "SP.TRAINING.LEARNING"]},
    {"mechanic": "Relearning an acquired ability", "mapping": ["SP.TRAINING.RELEARN"]},
    {"mechanic": "Low-condition recovery", "mapping": ["SP.HEALTH.CONDITION"]},
    {"mechanic": "Body discomfort/poor condition recovery", "mapping": ["SP.HEALTH.MEDICAL"]},
    {"mechanic": "Batting-order decision", "mapping": ["SP.ROLE.LINEUP"]},
    {"mechanic": "Ability decline", "mapping": ["SP.AGING.DECLINE", "SP.HEALTH.AGING_RECOVERY"]},
    {"mechanic": "Events during schedule skip", "mapping": ["SP.CALENDAR.SKIP_CAPTURE"]},
    {"mechanic": "Free-action day skipped by schedule skip", "mapping": ["SP.CALENDAR.MISSED_FREE_ACTION"]},
    {"mechanic": "Goods income", "mapping": ["SP.PUBLIC.MERCHANDISE", "SP.CONTRACT.BUSINESS"]},
    {"mechanic": "Partner candidate appearance", "mapping": ["SP.ROMANCE.INTRODUCTION"]},
    {"mechanic": "Star level", "mapping": [INTERNAL_SIGNAL]},
    {"mechanic": "Reincarnated professional draft candidate", "mapping": ["SP.IDENTITY.REINCARNATION_SETUP"]},
]


def faq_coverage_problems() -> list[str]:
    problems = []
    for row in FAQ_CHECKLIST:
        mapping = row.get("mapping") or []
        if not mapping:
            problems.append(f"{row['mechanic']}: no mapping")
            continue
        for value in mapping:
            if value in (MECHANICAL_ONLY, INTERNAL_SIGNAL):
                continue
            if not is_registered(value):
                problems.append(f"{row['mechanic']}: unregistered {value}")
    return problems


# --------------------------------------------------------------------------
# Legacy 28 situations -> stable event types
# --------------------------------------------------------------------------

LEGACY_SITUATION_MAP: dict[str, dict] = {
    "clubhouse.encourage_teammate": {"event_type": "SP.RELATION.ENCOURAGEMENT", "thread_type": "teammate_mentorship"},
    "clubhouse.mentor_rookie": {"event_type": "SP.RELATION.PROTEGE", "thread_type": "teammate_mentorship"},
    "clubhouse.address_tension": {"event_type": "SP.RELATION.RECONCILIATION", "thread_type": "manager_trust"},
    "clubhouse.team_dinner": {"event_type": "SP.RELATION.MEAL", "thread_type": "teammate_mentorship"},
    "media.postgame_interview": {"event_type": "SP.MEDIA.QUOTE", "thread_type": "public_reputation"},
    "media.answer_criticism": {"event_type": "SP.MEDIA.RESPONSE_TO_CRITICISM", "thread_type": "public_reputation"},
    "media.record_pressure": {"event_type": "SP.MEDIA.QUOTE", "thread_type": "milestone_chase", "related": ["SP.MILESTONE.APPROACHING"]},
    "media.praise_team": {"event_type": "SP.MEDIA.PRAISE_TEAMMATE", "thread_type": "public_reputation"},
    "fans.fan_message": {"event_type": "SP.PUBLIC.FAN_MESSAGE", "thread_type": "public_reputation"},
    "fans.surprise_visit": {"event_type": "SP.PUBLIC.FAN_SERVICE", "thread_type": "public_reputation"},
    "fans.signing_session": {"event_type": "SP.PUBLIC.AUTOGRAPH", "thread_type": "public_reputation"},
    "fans.community_event": {"event_type": "SP.PUBLIC.COMMUNITY_EVENT", "thread_type": "public_reputation"},
    "rivalry.respect_rival": {"event_type": "SP.MEDIA.PRAISE_OPPONENT", "thread_type": "rivalry"},
    "rivalry.challenge_rival": {"event_type": "SP.MEDIA.CALL_OUT_OPPONENT", "thread_type": "rivalry"},
    "rivalry.private_exchange": {"event_type": "SP.RELATION.OPPONENT_EXCHANGE", "thread_type": "rivalry"},
    "rivalry.defuse_feud": {"event_type": "SP.MEDIA.DEESCALATION", "thread_type": "rivalry"},
    "training.extra_work": {"event_type": "SP.TRAINING.EXTRA_WORK", "thread_type": "performance_pursuit"},
    "training.change_routine": {"event_type": "SP.TRAINING.ROUTINE_CHANGE", "thread_type": "performance_pursuit"},
    "training.study_video": {"event_type": "SP.TRAINING.VIDEO_STUDY", "thread_type": "performance_pursuit"},
    "training.recovery_day": {"event_type": "SP.HEALTH.REST", "thread_type": "injury_and_return"},
    "career.meet_agent": {"event_type": "SP.CONTRACT.AGENT_MEETING", "thread_type": "contract_negotiation"},
    "career.talk_manager": {"event_type": "SP.ROLE.ROLE_REQUEST", "thread_type": "role_competition"},
    "career.contract_signal": {"event_type": "SP.CONTRACT.PRINCIPLE_STATEMENT", "thread_type": "contract_negotiation"},
    "career.future_goal": {"event_type": "SP.USER.DECLARATION", "thread_type": "performance_pursuit"},
    "personal.quiet_evening": {"event_type": "SP.PRIVATE.QUIET_DAY", "thread_type": "private_life"},
    "personal.call_family": {"event_type": "SP.PRIVATE.FAMILY_CALL", "thread_type": "private_life"},
    "personal.visit_memory": {"event_type": "SP.PRIVATE.DESTINATION", "thread_type": "private_life"},
    "personal.share_honesty": {"event_type": "SP.PRIVATE.CONFESSION", "thread_type": "private_life"},
}


def resolve_legacy(category: str, situation: str) -> dict:
    key = f"{category}.{situation}"
    row = LEGACY_SITUATION_MAP.get(key)
    if not row:
        raise KeyError(f"unknown legacy situation {key}")
    return {"legacy_key": key, "related": [], **row}


# --------------------------------------------------------------------------
# Dialogue acts -> default event types for the first slice (8.3, 21.4)
# --------------------------------------------------------------------------

SLICE_ONE_DIALOGUE_ACTS = (
    "ask",
    "clarify",
    "vent",
    "console",
    "praise",
    "criticize_self",
    "declare",
    "apologize",
    "request_private_meeting",
    "give_public_quote",
)

DIALOGUE_ACT_EVENTS: dict[str, dict] = {
    "ask": {"event_type": None, "creates_event": False},
    "clarify": {"event_type": None, "creates_event": False},
    "vent": {"event_type": "SP.USER.PRIVATE_CONVERSATION", "creates_event": True, "visibility": "private"},
    "console": {"event_type": "SP.USER.CONSOLE", "creates_event": True, "visibility": "clubhouse"},
    "praise": {"event_type": "SP.USER.PRAISE", "creates_event": True, "visibility": "clubhouse"},
    "criticize_self": {"event_type": "SP.MEDIA.SELF_CRITICISM", "creates_event": True, "visibility": "national"},
    "declare": {"event_type": "SP.USER.DECLARATION", "creates_event": True, "visibility": "national"},
    "apologize": {"event_type": "SP.USER.APOLOGY", "creates_event": True, "visibility": "clubhouse"},
    "request_private_meeting": {"event_type": "SP.RELATION.PRIVATE_MEETING", "creates_event": True, "visibility": "private"},
    "give_public_quote": {"event_type": "SP.MEDIA.QUOTE", "creates_event": True, "visibility": "national"},
}


def catalog() -> dict:
    return {
        "ontology_version": ONTOLOGY_VERSION,
        "domains": [
            {
                "id": domain_id,
                "label": definition["label"],
                "surface": definition["surface"],
                "owners": list(definition["owners"]),
                "slice_one": domain_id in SLICE_ONE_DOMAINS,
                "families": [
                    {"id": f"{domain_id}.{suffix}", "label": label} for suffix, label in definition["families"]
                ],
            }
            for domain_id, definition in DOMAINS.items()
        ],
        "event_type_count": len(_EVENT_TYPES),
        "faq_rows": len(FAQ_CHECKLIST),
        "legacy_situations": len(LEGACY_SITUATION_MAP),
        "dialogue_acts": list(SLICE_ONE_DIALOGUE_ACTS),
    }


# Domain -> default legacy (category, situation) when no exact reverse mapping exists.
_DOMAIN_DEFAULT_SELECTION = {
    "SP.MEDIA": ("media", "postgame_interview"),
    "SP.RELATION": ("clubhouse", "encourage_teammate"),
    "SP.PUBLIC": ("fans", "fan_message"),
    "SP.CONTRACT": ("career", "meet_agent"),
    "SP.TRAINING": ("training", "extra_work"),
    "SP.HEALTH": ("training", "recovery_day"),
    "SP.PRIVATE": ("personal", "quiet_evening"),
    "SP.ROSTER": ("career", "talk_manager"),
    "SP.ROLE": ("career", "talk_manager"),
    "SP.MILESTONE": ("media", "record_pressure"),
    "SP.GAME.POST": ("media", "postgame_interview"),
    "SP.GAME.PRE": ("training", "study_video"),
    "SP.USER": ("personal", "share_honesty"),
    "SP.AWARD": ("media", "postgame_interview"),
    "SP.STANDINGS": ("media", "record_pressure"),
    "SP.LEGACY": ("career", "future_goal"),
    "SP.AGING": ("training", "change_routine"),
    "SP.ROMANCE": ("personal", "quiet_evening"),
    "SP.CALENDAR": ("personal", "quiet_evening"),
    "SP.IDENTITY": ("personal", "visit_memory"),
}

_ACT_DEFAULT_SELECTION = {
    "SP.USER.DECLARATION": ("career", "future_goal"),
    "SP.USER.APOLOGY": ("clubhouse", "address_tension"),
    "SP.USER.CONSOLE": ("clubhouse", "encourage_teammate"),
    "SP.USER.PRAISE": ("media", "praise_team"),
    "SP.USER.PRIVATE_CONVERSATION": ("personal", "share_honesty"),
    "SP.USER.PROP": ("personal", "share_honesty"),
}


def legacy_selection_for(event_type: str) -> dict:
    """Legacy category/situation pair used to store a committed chat event."""
    text = str(event_type or "")
    for key, row in LEGACY_SITUATION_MAP.items():
        if row.get("event_type") == text:
            category, situation = key.split(".", 1)
            return {"category": category, "situation": situation}
    if text in _ACT_DEFAULT_SELECTION:
        category, situation = _ACT_DEFAULT_SELECTION[text]
        return {"category": category, "situation": situation}
    for prefix in sorted(_DOMAIN_DEFAULT_SELECTION, key=len, reverse=True):
        if text.startswith(prefix):
            category, situation = _DOMAIN_DEFAULT_SELECTION[prefix]
            return {"category": category, "situation": situation}
    return {"category": "personal", "situation": "share_honesty"}
