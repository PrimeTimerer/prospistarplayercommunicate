#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Community + media content generators (offline, template-grammar based).

Three distinct board voices per engines 09/10/11:
  DC     — short, fast, banter, floating/fixed handles, dogpiling, chains
  FMK    — meme/potential, upvotes, gif culture, other-team fans
  MLBPARK— long-form value debates, sabermetrics vs traditional, samples
HEAT (1-10) scales aggression. Nicknames avoid AI-style role names.
This is deterministic template variety, not an LLM — rich but bounded.
"""
import hashlib
import random

import editorial_engine
import spotlight_engine

# ---------------------------------------------------------------- nicknames
_DC_FIXED = ["야구보는개", "직관충", "스탯딸깍", "불펜방화범", "9호선막차",
             "마무리기도메타", "어제산낙지", "홈런공장장", "선발captain"]
_FMK_HANDLES = ["오늘도출근", "라멘조아", "감자칩", "손흥민팬", "겜돌이99",
                "주말농부", "카페인중독", "야근각", "무지개떡", "롱볼러"]
_MLBPARK_HANDLES = ["Jamsil_1982", "SajikBleacher", "OPS_believer", "coldzone",
                    "가을야구원함", "smallball", "표본충분한가", "ERA_말고FIP",
                    "구장별파크팩터", "veteran_scout"]


def _dc_author(rng):
    if rng.random() < 0.55:
        return f"ㅇㅇ({rng.randint(1,223)}.{rng.choice(['xxx','***'])})"
    return rng.choice(_DC_FIXED)


def _fmk_author(rng):
    return rng.choice(_FMK_HANDLES)


def _mlb_author(rng):
    return rng.choice(_MLBPARK_HANDLES)


# ---------------------------------------------------------------- helpers
def _hook(event):
    """Best single narrative hook: milestone > big game > season tone."""
    if event["milestones"]:
        return event["milestones"][0][1]
    lines = event.get("_game_lines") or []
    if lines:
        return lines[0]
    a = event["assess"]
    return (a["facts"][0] if a["facts"] else "시즌 페이스 유지")


def _short(name):
    return name.split()[-1] if " " in name else name


# ---------------------------------------------------------------- DC
def gen_dc(event, heat, rng, mem):
    name = event["snapshot"]["player"]["name"]
    s = _short(name)
    a = event["assess"]
    big = bool(event["milestones"]) or event["kind"] == "NEW_GAME"
    tag = "[속보]" if big else "[일반]"
    if event["milestones"]:
        title = f"{tag} {s} {event['milestones'][0][1]} ㄷㄷ"
    elif a["tone"] == "폭발":
        title = f"{tag} {s} 이 새끼 핵 쓰는거 아니냐고 ㅋㅋ"
    else:
        title = f"{tag} {s} 오늘 어땠냐"
    pool = []
    if a["tone"] in ("폭발", "좋음"):
        pool += [
            "이건 그냥 다른 종목임", "내가 처음부터 알아봤다 ㅇㅇ",
            f"{s} 까던 애들 다 어디감?", "감독은 왜 진작 안 썼냐",
            "타팀인데 인정한다", f"{a['line_pit'] or a['line_bat'] or ''} 실화냐",
            "짤 만들어야됨 이거", "라스트보스 그자체",
        ]
    else:
        pool += ["2군 가야지", "방출각 재보자", "연봉 아깝다",
                 "원래 이정도임", "과대평가 오짐"]
    if heat >= 9:
        pool += [f"{s} 진짜 미친놈이네 ㅋㅋㅋ", "이거 보고 상대투수 은퇴함"]
    rng.shuffle(pool)
    comments = [{"author": _dc_author(rng), "text": t} for t in pool[:6 if big else 4]]
    # a short dogpile chain
    if big:
        comments += [
            {"author": _dc_author(rng), "text": f"얘 {s} 지난주에 까던 글 있었는데"},
            {"author": _dc_author(rng), "text": "링크 ㅇㅇ 성지된다"},
        ]
    return {"board": "DCInside", "code": "dc", "title": title, "comments": comments}


# ---------------------------------------------------------------- FMKorea
def gen_fmk(event, heat, rng, mem):
    name = event["snapshot"]["player"]["name"]
    s = _short(name)
    a = event["assess"]
    if event["milestones"]:
        title = f"(움짤) 방금 {s} {event['milestones'][0][1]}...jpg"
    elif event["kind"] == "NEW_GAME" and event.get("_game_lines"):
        title = f"오늘자 {s} 미쳤네요 (기록카드)"
    else:
        title = f"{s} 현재 시즌 페이스 정리.jpg"
    comments = [
        {"author": _fmk_author(rng), "text": "포텐 예약", "up": rng.randint(30, 240)},
        {"author": _fmk_author(rng), "text": f"{_hook(event)} 이게 사람이냐", "up": rng.randint(50, 400)},
        {"author": _fmk_author(rng), "text": "타팀팬인데 그냥 웃음벨", "up": rng.randint(10, 120)},
        {"author": _fmk_author(rng), "text": "이걸 왜 거른 팀들이 있었냐", "up": rng.randint(20, 160)},
    ]
    if a["tone"] == "폭발":
        comments.append({"author": _fmk_author(rng),
                         "text": "짤 재가공 각 나온다", "up": rng.randint(40, 300)})
    if heat >= 9:
        comments.append({"author": _fmk_author(rng),
                         "text": "리그 밸런스 붕괴수준 ㅋㅋ", "up": rng.randint(15, 200)})
    rng.shuffle(comments)
    comments.sort(key=lambda c: -c["up"])
    return {"board": "FMKorea", "code": "fmk", "title": title, "comments": comments}


# ---------------------------------------------------------------- MLBPARK
def gen_mlbpark(event, heat, rng, mem):
    name = event["snapshot"]["player"]["name"]
    a = event["assess"]
    pit, bat = a["line_pit"], a["line_bat"]
    role = event.get("role", "no_appearance")
    if role == "pitching":
        season_line = pit or "투수 기록 확인 중"
    elif role == "batting":
        season_line = bat or "타자 기록 확인 중"
    else:
        season_line = " / ".join(value for value in (pit, bat) if value) or "시즌 기록 확인 중"
    title = f"솔직히 {_short(name)} 이 정도면 리그 역사 다시 써야 하는 거 아닌가요"
    chain = [
        {"author": _mlb_author(rng), "text": f"이번 시즌 라인 정리하면 {season_line}인데, 표본이 쌓일수록 더 비현실적으로 갑니다."},
        {"author": _mlb_author(rng), "text": "월간 성적만 좋은 게 아니라 시즌 누적이라 반박이 안 되네요."},
        {"author": _mlb_author(rng), "text": "그래도 수비/주루 세부지표 표본은 더 봐야 한다고 봅니다. 숫자만으로 단정은 이릅니다."},
        {"author": _mlb_author(rng), "text": "WAR 정확한 값은 이 세계 데이터로는 못 박지만, 페이스 자체가 비상식적인 건 맞습니다."},
        {"author": _mlb_author(rng), "text": "FA 가치로 환산하면 사실상 산정 불가 구간이죠. 비교군이 없습니다."},
    ]
    if event["milestones"]:
        chain.insert(1, {"author": _mlb_author(rng),
                         "text": f"방금 {event['milestones'][0][1]} 찍었는데 이건 커리어 내러티브가 통째로 바뀌는 지점입니다."})
    if heat >= 9:
        chain.append({"author": _mlb_author(rng),
                      "text": "이쯤 되면 다른 11개 구단이 리그에 항의해도 할 말 없습니다."})
    return {"board": "MLBPARK", "code": "mlb", "title": title, "comments": chain}


# ---------------------------------------------------------- ambient world
_AMBIENT_BOARDS = (
    ("club-pulse", "구단 팬 포럼", "구단의 하루가 다시 이 선수를 중심으로 해석된다"),
    ("rival-watch", "상대팀 전력 토론", "다음 맞대결보다 먼저 대응법 논쟁이 시작됐다"),
    ("league-live", "리그 야구 라이브", "오늘 새 기록이 없어도 기록 시계는 멈추지 않는다"),
    ("record-room", "야구 기록 연구실", "현재 페이스를 어느 시대와 비교할 것인가"),
    ("broadcast-desk", "중계·해설 라운지", "한 장면보다 누적된 위상이 더 큰 화제다"),
    ("global-translation", "국제 야구 번역방", "국내 반응이 번역된 뒤 다시 돌아오기 시작했다"),
    ("culture-trend", "스포츠 문화 트렌드", "야구 밖에서도 이름 자체가 콘텐츠가 된다"),
    ("history-debate", "야구사 논쟁 게시판", "기록의 숫자보다 시대적 의미를 두고 갈린다"),
)

_AMBIENT_COMMENTS = (
    "특별한 장면이 없는데도 이름만 뜨면 사람들이 모이는 단계임",
    "이제 상대팀도 한 경기 결과보다 시즌 전체 대응부터 계산할 듯",
    "기록 하나를 더 세웠느냐보다 어디까지 갈지가 매일 기사거리다",
    "지난 반응이 다음 반응의 근거가 되면서 관심이 스스로 커지고 있음",
    "팬들은 평범한 하루도 다음 대기록 전의 복선처럼 읽는 분위기",
    "과장이라는 반론도 나오지만 누적 수치가 논쟁을 계속 되살린다",
    "구단 안에서는 휴식과 루틴마저 시즌 운영의 핵심 결정으로 보일 것",
    "국내 반응을 해외가 번역하고 그 번역본이 다시 들어오는 순환이 생김",
    "지금은 새로운 사건보다 이미 쌓인 위상 자체가 사건에 가깝다",
    "다음 경기에서 평범하면 오히려 그 평범함이 왜 뉴스냐는 논쟁이 붙을 단계",
    "역사 비교가 너무 빠르다는 쪽과 이미 늦었다는 쪽이 계속 충돌한다",
    "개인의 작은 선택과 공개된 경기 성적은 분리해서 봐야 한다",
)


def _ambient_board(event, spotlight, rng, spec, index):
    code, board, title = spec
    player_name = _short(event["snapshot"]["player"]["name"])
    anchors = spotlight.get("memory_anchors") or ["현재 시즌 누적 성적"]
    anchor = anchors[index % len(anchors)]
    pool = list(_AMBIENT_COMMENTS)
    rng.shuffle(pool)
    comments = []
    for offset, text in enumerate(pool[:4]):
        suffix = f" · 근거: {anchor}" if offset == 0 else ""
        comments.append(
            {
                "author": f"관전자{rng.randint(100, 9999)}",
                "text": f"{text}{suffix}",
                "up": rng.randint(4, 260),
            }
        )
    return {
        "board": board,
        "code": code,
        "title": f"{player_name} — {title}",
        "comments": comments,
        "provenance": "fictional_ambient_simulation",
    }


def _scale_comments(boards, target, event, spotlight, rng):
    """Fit the deterministic reaction budget while keeping every board alive."""
    target = max(len(boards), int(target or 0)) if boards else 0
    total = sum(len(board.get("comments") or []) for board in boards)
    while total > target:
        changed = False
        for board in reversed(boards):
            comments = board.get("comments") or []
            if len(comments) > 1 and total > target:
                comments.pop()
                total -= 1
                changed = True
        if not changed:
            break
    player_name = _short(event["snapshot"]["player"]["name"])
    anchors = spotlight.get("memory_anchors") or ["현재 시즌 누적 성적"]
    cursor = 0
    while boards and total < target:
        board = boards[cursor % len(boards)]
        template = _AMBIENT_COMMENTS[cursor % len(_AMBIENT_COMMENTS)]
        anchor = anchors[cursor % len(anchors)]
        board.setdefault("comments", []).append(
            {
                "author": f"기록독자{rng.randint(100, 9999)}",
                "text": f"{player_name}: {template} · {anchor} · 관점 {cursor + 1}",
                "up": rng.randint(2, 220),
            }
        )
        cursor += 1
        total += 1


# ---------------------------------------------------------------- media
def gen_media(event, rng, spotlight=None, limit=None):
    p = event["snapshot"]["player"]
    d = event["snapshot"]["date"]
    a = event["assess"]
    name, team = p["name"], p["team"]
    arts = []
    spotlight = spotlight if isinstance(spotlight, dict) else {}
    # domestic always; overseas/japan only for big events (media router, engine 23)
    big = bool(event["milestones"]) or a["tone"] == "폭발"
    role = event.get("role", "no_appearance")
    if role == "pitching":
        regular_title = f"{name}, 마운드에서 시즌 흐름 이어간다"
    elif role == "batting":
        regular_title = f"{name}, 타석에서 시즌 흐름 이어간다"
    else:
        regular_title = f"{name}, 꾸준한 시즌 이어간다"
    arts.append({
        "outlet": "국내 야구 매체", "flag": "📰", "lang": "ko",
        "title": f"“중계 화면이 고장 난 줄”… {name}의 하루" if big else regular_title,
        "sub": f"{team} · {d.get('year')}.{d.get('month')}.{d.get('day')} · {a['tone']}",
        "body": _media_body(event),
        "provenance": "fictional_press_simulation",
    })
    if big:
        arts.append({
            "outlet": "해외 스포츠 매체 (번역)", "flag": "🌐", "lang": "en",
            "title": f"“이게 야구가 맞나” — {name}, 스포츠의 상식을 다시 쓰다",
            "sub": f"Is This Even Baseball Anymore? {name} Rewrites the Sport",
            "body": _media_body(event),
            "provenance": "fictional_press_simulation",
        })
        arts.append({
            "outlet": "일본 스포츠 매체 (번역)", "flag": "🇯🇵", "lang": "ja",
            "title": f"“기록이 의미를 잃었다” — 일본 언론도 주목한 {name}",
            "sub": "「これは現実か」— 一人の選手がリーグを飲み込む",
            "body": _media_body(event),
            "provenance": "fictional_press_simulation",
        })
    anchors = spotlight.get("memory_anchors") or ["현재까지 검증된 시즌 누적"]
    tier_label = spotlight.get("label") or "현재 관심 단계"
    extra = (
        ("리그 전력 분석실", "📊", f"상대팀의 하루는 왜 {name}을 기준으로 돌아가나"),
        ("전국 기록 데스크", "🏟️", f"새 기록 없는 날에도 멈추지 않는 {name} 카운트다운"),
        ("야구사 리뷰", "📚", f"기록을 넘어 시대 비교로 옮겨간 {name} 논쟁"),
        ("국제 야구 브리핑 (번역)", "🌏", f"번역되고 역수입되는 {name} 현상"),
        ("구단 현장 노트", "📝", f"평범한 루틴도 팀 운영 뉴스가 되는 이유"),
        ("스포츠 문화 관찰", "📡", f"야구 밖까지 번지는 이름, {name}"),
        ("데이터 칼럼", "🔎", f"오늘보다 누적이 더 큰 뉴스가 된 시즌"),
    )
    for index, (outlet, flag, title) in enumerate(extra):
        anchor = anchors[index % len(anchors)]
        body = list(_media_body(event))
        body.append(
            f"이 반응의 서사 근거는 ‘{anchor}’이며, 현재 오프라인 세계 주목도는 {tier_label} 단계로 계산됐다."
        )
        body.append("실제 보도나 실존 인물 발언이 아닌 검증 기록 기반의 가상 세계선 반응이다.")
        arts.append(
            {
                "outlet": outlet,
                "flag": flag,
                "lang": "ko",
                "title": title,
                "sub": f"누적 위상 파장 · {team} · {d.get('year')}.{d.get('month')}.{d.get('day')}",
                "body": body,
                "provenance": "fictional_ambient_simulation",
            }
        )
    return arts[: max(0, int(limit))] if limit is not None else arts


def _media_body(event):
    p = event["snapshot"]["player"]
    a = event["assess"]
    lines = event.get("_game_lines") or []
    paras = []
    lead = f"{p['team']}의 {p['name']}가 만들어내는 기록이 다시 화제다."
    if lines:
        lead += " 이날 " + ", ".join(lines) + "."
    paras.append(lead)
    body2 = ""
    role = event.get("role", "no_appearance")
    show_pitching = role in ("pitching", "two_way", "no_appearance")
    show_batting = role in ("batting", "two_way", "no_appearance")
    if show_pitching and a["line_pit"]:
        body2 += f"투수로서 {a['line_pit']}. "
    if show_batting and a["line_bat"]:
        body2 += f"타자로서 {a['line_bat']}. "
    if role in ("two_way", "no_appearance") and a["facts"]:
        body2 += a["facts"][0] + "."
    if body2:
        paras.append(body2)
    if event["milestones"]:
        paras.append("특히 " + event["milestones"][0][1] + "은 시즌 서사를 바꾸는 장면으로 평가된다.")
    return paras


def _fresh_phrase(original, blocked, used, *, title=False):
    """Return a deterministic non-repeated rendering of one generated phrase."""
    if not original or (original not in blocked and original not in used):
        return original
    prefixes = (
        ("다시 보는", "기록 점검", "시즌 메모", "현 시점", "오늘의 쟁점")
        if title
        else (
            "이번 기록만 보면",
            "지금 수치 기준이면",
            "세이브 누적 기준으로는",
            "일단 기록 카드상",
            "오늘 확인분만 놓고 보면",
            "현재 페이스라면",
            "이 시점 기준으로는",
            "검증된 수치만 보면",
        )
    )
    suffixes = (
        ("다시 읽기", "기록으로 확인", "현재판", "다음 경기 전 점검")
        if title
        else ("라는 얘기", "라고 봄", "이 핵심", "부터 확인하자", "는 인정", "은 더 지켜보자")
    )
    candidates = [f"{prefix} {original}" for prefix in prefixes]
    candidates.extend(f"{original} · {suffix}" for suffix in suffixes)
    candidates.extend(
        f"{prefix} {original} · {suffix}"
        for prefix in prefixes
        for suffix in suffixes
    )
    for candidate in candidates:
        if candidate not in blocked and candidate not in used:
            return candidate
    # The recent-memory window is bounded, so this branch is only a defensive
    # fallback for manually edited ledgers with an unusually large phrase set.
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:6]
    return f"{original} · 기록 메모 {digest}"


def _suppress_recent_repeats(feed, mem):
    """Avoid exact title/comment reuse across recent generated feeds."""
    blocked = {
        str(value)
        for value in (mem or {}).get("recent_phrases", [])
        if isinstance(value, str) and value
    }
    used = set()
    for article in feed.get("media", []):
        article["title"] = _fresh_phrase(article.get("title", ""), blocked, used, title=True)
        used.add(article["title"])
    for board in feed.get("boards", []):
        board["title"] = _fresh_phrase(board.get("title", ""), blocked, used, title=True)
        used.add(board["title"])
        for comment in board.get("comments", []):
            comment["text"] = _fresh_phrase(comment.get("text", ""), blocked, used)
            used.add(comment["text"])
    return feed


# ---------------------------------------------------------------- orchestrate
def build_feed(event, config, mem, *, spotlight, universe_id="", previous=None, game_bundle=None):
    """Build the templated feed. ``spotlight`` is the caller's evaluated
    world-attention result; the service computes it once per request so the
    feed, briefing, and story turns never disagree about the player's stature."""
    if not isinstance(spotlight, dict):
        raise TypeError("build_feed requires the evaluated spotlight dict")
    heat = config.get("heat", 7)
    try:
        language_level = max(1, min(5, int(config.get("community_language_level", 2))))
    except (TypeError, ValueError):
        language_level = 2
    platforms = [code for code in config.get("platforms", ["dc", "fmk", "mlb"]) if code in ("dc", "fmk", "mlb")]
    if not platforms:
        platforms = ["dc"]
    mode = config.get("mode", "standard")

    event = dict(event)
    event["_game_lines"] = event.get("delta") and __import__("stat_engine").describe_game(event["delta"]) or []

    feed = {"event": {"kind": event["kind"], "date": event["snapshot"]["date"],
                      "milestones": event["milestones"], "game_lines": event["_game_lines"],
                      "delta": event.get("delta", {}),
                      "role": event.get("role", "no_appearance")},
            "player": event["snapshot"]["player"],
            "_stats": event["snapshot"]["stats"],
            "assess": event["assess"], "heat": heat,
            "community_language_level": language_level, "mode": mode,
            "persona": config.get("persona", ""),
            "spotlight": spotlight,
            "ambient_waves": spotlight.get("waves") or [],
            "media": [], "boards": []}

    budget = spotlight.get("reaction_budget") or {}
    board_target = max(0, int(budget.get("boards") or 0))
    media_target = max(0, int(budget.get("media") or 0))
    comment_target = max(0, int(budget.get("comments") or 0))
    # Retain the legacy public helpers and stored feed shape. New publication
    # uses evidence allocation and authored moves rather than suffix inflation.
    publication = editorial_engine.build(
        event, budget={"boards": board_target, "media": media_target if mode != "quick" else 0,
                       "comments": comment_target}, memory=mem, universe_id=universe_id,
        previous=previous, game_bundle=game_bundle, spotlight=spotlight, platforms=platforms,
        heat=heat, language_level=language_level,
    )
    feed.update(publication)
    return feed
