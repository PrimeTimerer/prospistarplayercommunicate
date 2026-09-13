#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM briefing exporter — the bridge to cinematic narrative.

Turns the auto-extracted save facts + world memory into a single paste-ready
prompt for ChatGPT/Claude, so the user gets the long-form simulation WITHOUT
re-typing screenshots. Offline: this only assembles text; no network calls.
"""
import stat_engine
import spotlight_engine

# Condensed narrator rules distilled from the PROSPI UNIVERSE V5 engines.
_RULES = """당신은 'PROSPI UNIVERSE SIMULATION ENGINE'이다. 아래 [사건]을 프로야구
스피리츠 세계의 사건으로 받아 야구계·언론·SNS·커뮤니티 여론을 현실적으로 시뮬레이션한다.

[출력 원칙]
- 재미는 과감하게, 사실은 보수적으로. 아래 제공된 수치/사실만 HARD FACT로 쓰고,
  없는 수치·인터뷰·목격담·비거리·투구수는 지어내지 않는다(연출용 추측은 추측으로 명시).
- 커뮤니티는 세 곳의 목소리가 뚜렷이 달라야 한다.
  · DCInside: 단문·속도·조롱·유동(ㅇㅇ(118.***))과 고닉 혼합·성지글 발굴·갈드컵. 매 댓글 ㅋㅋ 금지.
  · FMKorea: 포텐/추천/움짤 중심·타팀팬 유입·밈 재가공. 추천 높은 상반 댓글 공존.
  · MLBPARK: 장문·가치/계약/표본 논쟁·세이버 vs 전통·긴 재반박. 욕설을 존댓말로 바꾼 DC가 아님.
- 여론엔 비판/냉소/옹호/기록분석/타팀팬/분탕/태세전환/과거글발굴러가 공존하고 서로 싸운다.
- 외국어 게시물/기사/댓글은 원문 바로 아래 [한국어 번역]을 붙인다(일본어/English/繁體 등).
- 사건 크기에 따라 QUICK/STANDARD/EXPLOSION으로 분량을 조절한다. 대형 사건은 실시간→몇시간후→
  다음날 여론 변화, 사이트 간 전염, 기사 제목, SNS, 과거글 발굴까지 확장한다.
- 그날 사건이 작아도 선수의 누적 성적·수상·기록·커리어 위상이 크면 주변 세계는 스스로 반응한다.
  무명 루키는 개인·가까운 관계에 머물고, 리그 중심 이상은 구단→상대팀→전국→기록계→해외로 파동을 넓힌다.
- 이전 수상·마일스톤·별명·인상적인 문장을 기억 앵커로 재호출해 한 사건이 다음 사건에 메아리치게 한다.
  비공개 행동은 기사나 목격담으로 유출시키지 말고, 기존 공개 성적 때문에 계속되는 간접 관심과 분리한다.
- 닉네임은 AI식 역할명(팩폭전문가/기록충 등) 반복 금지. 자연스러운 실제 게시판형 닉네임.
- 금지: 보호특성 혐오, 현실 위해 촉구, 신상털기, 실존인물 중대범죄 날조. 방출론/먹튀론/조롱은 허용.

[중요·연출]
- 제공된 설정(이도류·비현실적 수치 포함)은 게임 세계의 확정 사실이다. "버그/오류/불가능/이름 같은 두 명"
  같은 메타 논평이나 자기검증을 본문에 쓰지 말고, 곧바로 세계 내 사실로 받아들여 몰입형 서사만 출력한다.
- 시스템 알림/데이터포인트 나열로 시작하지 말고, 장면·헤드라인·커뮤니티 반응으로 바로 들어간다.
"""


def _fmt_stats(st):
    lines = []
    if st.get("pit_IP"):
        lines.append(f"- 투수 누적: {st.get('pit_W')}승 · {st['pit_IP']}이닝 · "
                     f"{st.get('pit_K')}탈삼진 · 피안타 {st.get('pit_H')} · 상대타자 {st.get('pit_TBF')}")
    if st.get("bat_AB"):
        lines.append(f"- 타자 누적: 타율 {st.get('bat_AVG')} · {st.get('bat_HR')}홈런 · "
                     f"{st.get('bat_RBI')}타점 · {st.get('bat_R')}득점 · {st.get('bat_SB')}도루 · "
                     f"{st.get('bat_H')}안타(타수 {st.get('bat_AB')}) · 삼진 {st.get('bat_SO')}")
    return "\n".join(lines)


def build_briefing(ev, cfg, led, *, spotlight):
    if not isinstance(spotlight, dict):
        raise TypeError("build_briefing requires the evaluated spotlight dict")
    snap = ev["snapshot"]
    p = snap["player"]
    d = snap["date"]
    a = ev["assess"]
    mode = cfg.get("mode", "standard").upper()
    heat = cfg.get("heat", 7)
    persona = cfg.get("persona", "").strip()
    mem = led.state.get("community_memory", {})
    game_lines = stat_engine.describe_game(ev.get("delta", {}))
    career = led.career_view(snap)

    kind_ko = {"NEW_GAME": "새 경기", "SEASON_UPDATE": "기록 갱신",
               "WORLD_INIT": "세계 시작", "CORRECTION": "정정",
               "REST_DAY": "휴식일"}.get(ev["kind"], ev["kind"])

    out = []
    out.append("# PROSPI UNIVERSE — 시뮬레이션 브리핑 (세이브에서 자동 생성)")
    out.append("아래 [사건]과 [세계 상태]를 근거로, [엔진 규칙]에 따라 시뮬레이션해줘.\n")

    out.append("## [사건]")
    out.append(f"- 유형: {kind_ko}")
    out.append(f"- 날짜: {d.get('year')}년 {d.get('month')}월 {d.get('day')}일 ({d.get('career_year')}년차)")
    if game_lines:
        for g in game_lines:
            out.append(f"- 직전 세이브 확인 이후 변화분(단일 경기로 단정하지 않음): {g}")
    if ev.get("milestones"):
        for _, txt in ev["milestones"]:
            out.append(f"- 대기록: 🏆 {txt}")
    out.append(f"- 종합 평가(성적톤): {a['tone']}")
    out.append("")

    two_way = bool(snap["stats"].get("pit_IP")) and bool(snap["stats"].get("bat_AB"))
    out.append("## [세계 상태]")
    tw = " · 이도류(투타 겸업) 스타플레이어 — 스타모드의 정상 설정" if two_way else ""
    out.append(f"- 선수: {p['name']} ({p.get('pos','')}){tw}")
    out.append(f"- 팀: {p['team']}")
    out.append(_fmt_stats(snap["stats"]))
    if a.get("facts"):
        out.append("- 특기사항: " + "; ".join(a["facts"][:3]))
    if persona:
        out.append(f"- 선수 성향(페르소나): {persona}")
    out.append("")

    if career:
        totals = career.get("totals", {})
        tracking = career.get("tracking", {})
        grade = career.get("player_grade", {})
        out.append("## [통산·지난 시즌 기억]")
        out.append(
            "- 통산 누계(과거 시즌 + 현재 세이브, 중복 제거): "
            f"{totals.get('bat_H', 0)}안타 · {totals.get('bat_HR', 0)}홈런 · "
            f"{totals.get('bat_RBI', 0)}타점 · {totals.get('pit_W', 0)}승 · "
            f"{totals.get('pit_K', 0)}탈삼진 · {totals.get('pit_IP', '0')}이닝"
        )
        out.append(
            f"- 과거 시즌: 세이브 자동 복원 {tracking.get('previous_seasons_save_verified', 0)}개 · "
            f"사용자 확인 {tracking.get('previous_seasons_manual', 0)}개 · "
            f"출처 상태: {'일부 시즌 미확인' if tracking.get('history_incomplete') else '현재 커리어 범위 완전'}"
        )
        out.append(
            f"- 오프라인 커리어 등급: {grade.get('code', '—')} · {grade.get('label', '계산 대기')} "
            f"({grade.get('score', 0)}/100) — {grade.get('description', '')}"
        )
        for season in (career.get("seasons") or [])[:3]:
            stats = season.get("stats", {})
            out.append(
                f"- {season.get('season_year')} 시즌({season.get('provenance')}): "
                f"{stats.get('bat_HR', 0)}홈런 · {stats.get('bat_H', 0)}안타 · "
                f"{stats.get('pit_W', 0)}승 · {stats.get('pit_K', 0)}탈삼진"
            )
        for honor in (career.get("honors") or [])[:5]:
            when = honor.get("occurred_on") or honor.get("season_year") or "날짜 미입력"
            out.append(f"- 수상·마일스톤({honor.get('provenance')}): {when} · {honor.get('title')}")
        compared = [
            row
            for scope in (career.get("records") or {}).values()
            for row in scope
            if row.get("status") in ("tied", "broken")
        ]
        for row in compared[:8]:
            relation = "동률" if row.get("status") == "tied" else "수치 초과"
            out.append(
                f"- NPB 공식 참고 대조: {row.get('scope')} {row.get('label')} "
                f"{row.get('current_display')} / 기준 {row.get('record_display')} ({relation})"
            )
        out.append("- 세이브 시즌 요약에 없는 정확한 달성일·당시 소속팀·수상명은 추정하지 않는다.")
        out.append("- 위 커리어 등급은 게임 공식 능력치가 아니라 서사 반응 범위를 위한 투명한 오프라인 계산이다.")
        out.append("")

    budget = spotlight.get("reaction_budget") or {}
    out.append("## [세계 주목도 · 오프라인 서사 라우팅]")
    out.append(
        f"- 단계: {spotlight.get('label')} · {spotlight.get('score')}/100 "
        f"(게시판 {budget.get('boards', 0)} · 기사 {budget.get('media', 0)} · 반응 파동 {budget.get('waves', 0)})"
    )
    out.append(f"- 의미: {spotlight.get('description')}")
    if spotlight.get("same_day_echo_active"):
        out.append("- 오늘 마지막 검증 사건의 파장을 같은 게임 날짜의 지속 반응으로 유지한다.")
    elif spotlight.get("quiet_current_event") and spotlight.get("ambient_active"):
        out.append("- 오늘 사건은 작지만 누적 위상 때문에 주변 세계의 간접 반응은 계속된다.")
    for driver in (spotlight.get("drivers") or [])[:6]:
        out.append(
            f"- 반응 근거({driver.get('provenance')}): {driver.get('label')} · +{driver.get('points')}"
        )
    out.append("- 이 지수는 공식 인기·시장가치가 아니라 반응의 범위와 분량만 정하는 투명한 서사 계산이다.")
    out.append("")

    hist = led.recent_events(6)
    if hist:
        out.append("## [최근 히스토리]")
        for e in hist:
            ed = e["date"]
            gl = " / ".join(e.get("game_lines") or []) or e["kind"]
            ms = ("  🏆" + e["milestones"][0][1]) if e.get("milestones") else ""
            out.append(f"- {ed.get('month')}/{ed.get('day')} {gl}{ms}")
        out.append("")

    reusable = []
    for k in ("nicknames", "memes", "open_loops"):
        if mem.get(k):
            reusable.append(f"- {k}: " + ", ".join(mem[k][-8:]))
    if reusable:
        out.append("## [재사용할 커뮤니티 기억]")
        out.extend(reusable)
        out.append("(이 별명/밈/떡밥만 재활용하고, 없던 과거글은 지어내지 마.)")
        out.append("")

    out.append("## [요청]")
    req = f"위 사건을 {mode} 강도, HEAT {heat}/10로 시뮬레이션해줘. "
    req += "요청 커뮤니티: " + ", ".join(
        {"dc": "DCInside", "fmk": "FMKorea", "mlb": "MLBPARK"}.get(x, x)
        for x in cfg.get("platforms", ["dc", "fmk", "mlb"]))
    req += " + 언론/SNS. 필요한 곳에 다국어와 번역을 넣어줘."
    out.append(req)
    big = (
        bool(ev.get("milestones"))
        or a["tone"] == "폭발"
        or mode.upper() == "EXPLOSION"
        or int(spotlight.get("tier_index") or 0) >= 3
    )
    if big:
        out.append("- 대형 사건이다. 시간대별 여론 변화로 전개해줘: "
                   "실시간(T+0~10분) → T+30분 → T+2시간 → 다음날 아침.")
        out.append("- 사이트 간 전염을 보여줘: DC 밈 생성 → FMKorea 움짤/포텐 → MLBPARK 비평 → "
                   "그 캡처가 DC로 역수입. 단, 같은 주장·욕설을 복붙하지 말 것.")
    out.append("")

    out.append("## [엔진 규칙]")
    out.append(_RULES)
    return "\n".join(out)
