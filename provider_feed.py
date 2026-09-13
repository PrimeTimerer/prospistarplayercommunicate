#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider-neutral expression adapter for a complete reaction bundle.

The deterministic editorial engine owns identity, facts, scope, item counts,
ordering, provenance, and every durable identifier.  A local model or Gemini
may replace only the visible prose fields in that frozen plan.  This keeps the
application useful with no model, gives both model providers the same feature
surface, and prevents a provider response from becoming a new source of fact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

import community_style_db
import editorial_engine
import realism_gate
import template_store as template_store


VERSION = "1.2.0"
SCHEMA_VERSION = 1
MAX_ARTICLES = 10
MAX_BOARDS = 10
MAX_COMMENTS = 72
MAX_SOCIAL_POSTS = 6
MAX_SOCIAL_REPLIES = 4
MAX_TITLE_CHARS = 180
MAX_SUB_CHARS = 260
MAX_PARAGRAPH_CHARS = 900
MAX_COMMENT_CHARS = 520
MAX_SOCIAL_CHARS = 700
MAX_BATCH_TEXT_FIELDS = 32
MAX_BATCH_SOURCE_CHARS = 24000
SCOPED_BATCH_TEXT_FIELDS = 18
SCOPED_BATCH_SOURCE_CHARS = 12000

SURFACE_SCOPES = ("all", "articles", "community")
_SCOPE_KINDS = {
    "all": ("articles", "boards", "social"),
    "articles": ("articles",),
    "community": ("boards", "social"),
}
_SCOPE_LABELS = {
    "all": "기사·게시판·SNS 전체 반응",
    "articles": "기사",
    "community": "커뮤니티·SNS",
}

_BATCH_LABELS = {
    "articles": "기사",
    "boards": "게시판",
    "social": "SNS",
}

_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:[.,]\d+)*|\.\d+)")
_HANGUL_PATTERN = re.compile(r"[가-힣]")
_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_META_PATTERN = re.compile(
    r"(?:세이브\s*파일|게임\s*데이터|오프라인\s*엔진|프롬프트|언어\s*모델|\bLLM\b|\bGemini\b)",
    re.IGNORECASE,
)

_SOCIAL_SPECS = (
    ("x", "X 타임라인", "@diamond_note"),
    ("threads", "Threads 토론", "@ballpark_thread"),
    ("instagram", "Instagram 텍스트 캡션", "@stadium_diary"),
    ("facebook", "Facebook 야구 그룹", "야구와 오늘"),
    ("japan-translation", "일본 야구 번역", "일본 현지 반응 번역"),
    ("global-translation", "글로벌 야구 번역", "해외 야구 반응 번역"),
)

_SOCIAL_REPLY_AUTHORS = {
    "x": ("@직구만봄", "@오늘의야구", "@원정석한칸", "@기록새로고침"),
    "threads": ("야구보는밤", "외야의대화", "다음경기까지", "공하나더"),
    "instagram": ("stadium.afterglow", "blue.seat", "game.day.note", "ninth.inning"),
    "facebook": ("주말 야구 모임", "야구와 가족", "오래된 외야석", "우리 동네 야구방"),
    "japan-translation": ("요코하마 현지팬 번역", "센트럴리그 팬 번역", "일본 야구팬 번역", "원정 관중 반응 번역"),
    "global-translation": ("북미 야구팬 번역", "대만 야구팬 번역", "중남미 야구팬 번역", "국제 야구 포럼 번역"),
}

_POST_TEMPLATES = (
    "오늘 {name} 뭐냐. {anchor}. 다음 경기 전까지 계속 올라오겠네.",
    "{name_object} 두고 다들 같은 장면을 봤는데 해석은 완전히 갈리는 중. 나는 {anchor_subject} 그냥 넘길 숫자는 아니라고 봄.",
    "오늘의 야구 기록\n\n{name} — {anchor}\n\n다음 장면도 기다려진다. #야구 #오늘의선수",
    "우리 야구 모임에서도 오늘은 {name} 이야기뿐이었습니다. {anchor_subject} 한 경기의 감탄을 넘어 시즌 전체를 다시 보게 만드네요.",
    "일본 현지 팬 반응을 옮깁니다. {name_possessive} {anchor_object} 놀라워하면서도 어느 시대와 비교해야 하는지를 두고 의견이 갈렸습니다.",
    "해외 야구 포럼 반응을 번역하면, {name_possessive} {anchor_subject} 결과보다 다음 상대가 어떤 해법을 들고 올지 궁금하게 만든다는 의견이 많았습니다.",
)

_REPLY_TEMPLATES = {
    "x": (
        "{anchor} 이건 진짜 다시 봐도 안 믿김.",
        "{name} 다음 경기 알림 켜둔 사람 나뿐 아니지 ㅋㅋ",
        "잘한 건 잘한 거고 다음 상대 반응도 궁금함.",
        "없는 뒷얘기 말고 오늘 나온 기록만 봐도 충분하다.",
    ),
    "threads": (
        "나도 처음엔 한 장면만 보고 놀랐는데, 기록을 다시 보니 시즌 전체 흐름을 같이 봐야겠더라.",
        "{anchor}에서 시작한 얘기지만 다음 경기 과정에 따라 또 완전히 달라질 수 있다고 생각해.",
        "나는 결과보다 상대가 이제 {name_object} 어떻게 준비할지가 더 궁금해졌어.",
        "같은 장면을 보고도 의견이 이렇게 갈리는 게 지금 관심의 크기인 듯.",
    ),
    "instagram": (
        "오늘 장면 저장. 다음 경기까지 또 돌려볼 듯 ⚾",
        "기록표를 보고 사진을 다시 보니 분위기가 더 선명해진다.",
        "{name_possessive} 하루를 결과 하나로만 설명하기엔 여운이 길다.",
        "다음 경기에서도 이 집중력이 이어지길. #야구기록",
    ),
    "facebook": (
        "오래 야구를 봤지만 이런 논쟁은 결국 시즌 전체를 본 뒤에야 정리되더군요.",
        "{anchor_subject} 분명 큰 근거지만 팀 안에서 어떤 역할로 이어지는지도 중요하다.",
        "과장할 필요 없이 지금까지 공개된 성적만으로도 충분히 이야기할 거리가 많다.",
        "다음 세대 팬들이 이 시기를 어떤 장면으로 기억할지도 궁금하다.",
    ),
    "japan-translation": (
        "일본 현지 반응에서는 {name}을 과거의 한 선수와 성급히 겹쳐 보지 말자는 의견도 나왔다.",
        "번역된 토론의 중심에는 {anchor}이 시즌 끝까지 이어질 수 있느냐는 질문이 있었다.",
        "상대 팀의 대응까지 포함해 봐야 지금의 가치가 더 또렷해진다는 평가다.",
        "기록의 크기와 매 경기의 긴장을 함께 즐기자는 반응이 눈에 띄었다.",
    ),
    "global-translation": (
        "해외 팬들은 숫자의 크기보다 다른 야구 환경에서도 같은 영향력이 보일지 토론했다.",
        "번역 반응 가운데에는 {name_possessive} 다음 선택이 리그 전체의 화제가 될 것이라는 전망도 있었다.",
        "{anchor_object} 시대와 환경을 넘어 어떻게 비교할지가 가장 어려운 질문이라는 의견이다.",
        "국경이 달라도 결국 모두가 다음 경기를 기다린다는 점은 같았다.",
    ),
}

_HEAT_POST_TAILS = {
    "mild": (
        "반응은 판단을 서두르지 않고 확인된 장면의 의미를 차분히 나누는 쪽에 가깝다.",
        "호감과 평가는 구분하되 다음 모습을 기다려 보자는 온도가 두드러진다.",
        "단정적인 말보다 공개된 범위 안에서 오래 지켜보자는 의견이 이어진다.",
    ),
    "balanced": (
        "기대와 경계가 맞붙으면서 같은 장면을 두고도 해석의 온도가 갈린다.",
        "응원하는 쪽과 더 지켜보자는 쪽이 팽팽하게 의견을 주고받고 있다.",
        "호평이 앞서지만 다음 결과까지 보자는 반론도 만만치 않다.",
    ),
    "hot": (
        "타임라인 지금 완전히 불붙었다.",
        "팬이랑 상대 팬이 정면으로 붙어서 댓글이 끝날 기미가 없다.",
        "한쪽은 미쳤다고 올려치고 다른 쪽은 호들갑이라 맞받는 중이다.",
    ),
}

_HEAT_REPLY_TAILS = {
    "mild": ("조금 더 지켜보고 말해도 늦지 않다.", "공개된 내용까지만 차분히 보자."),
    "balanced": ("그래도 반대쪽 해석까지 같이 들어볼 만하다.", "이 정도면 의견이 갈릴 이유는 충분하다."),
    "hot": ("이건 반대쪽도 그냥 넘기기 어렵지.", "댓글 오늘 안 끝나겠네 ㅋㅋ"),
}


class ProviderFeedError(ValueError):
    """A provider response failed the frozen reaction-bundle contract."""


def _stable(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]


def _canonical_number(value: str) -> str:
    compact = value.replace(",", "")
    if compact.startswith("."):
        compact = "0" + compact
    try:
        number = Decimal(compact)
    except InvalidOperation:
        return compact
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _text(value: object, label: str, limit: int, *, require_hangul: bool = True) -> str:
    if not isinstance(value, str):
        raise ProviderFeedError(f"{label}은(는) 문자열이어야 합니다.")
    result = re.sub(r"[ \t]+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    if not result:
        raise ProviderFeedError(f"{label}이(가) 비어 있습니다.")
    if len(result) > limit:
        raise ProviderFeedError(f"{label}이(가) 저장 한도를 넘었습니다.")
    if require_hangul and not _HANGUL_PATTERN.search(result):
        raise ProviderFeedError(f"{label}에 한국어 표현이 없습니다.")
    if _META_PATTERN.search(result):
        raise ProviderFeedError(f"{label}에 현실 몰입을 깨는 메타 표현이 있습니다.")
    return result


def _date_text(event: dict) -> str:
    date = ((event.get("snapshot") or {}).get("date") or {}) if isinstance(event, dict) else {}
    try:
        return f"{int(date.get('year') or 0):04d}-{int(date.get('month') or 0):02d}-{int(date.get('day') or 0):02d}"
    except (TypeError, ValueError):
        return "unknown"


def _social_budget(spotlight: dict) -> tuple[int, int]:
    tier = str((spotlight or {}).get("tier") or "private")
    tier_index = {"private": 0, "local": 1, "club": 2, "league": 3, "national": 4, "global": 5}.get(tier, 0)
    posts = (0, 1, 2, 3, 5, 6)[tier_index]
    replies = (0, 2, 2, 3, 4, 4)[tier_index]
    mode = str(((spotlight or {}).get("reaction_budget") or {}).get("mode") or "standard")
    if mode == "quick":
        posts = min(posts, 1)
        replies = min(replies, 2)
    elif mode == "explosion" and tier_index >= 2:
        posts = min(MAX_SOCIAL_POSTS, posts + 1)
        replies = min(MAX_SOCIAL_REPLIES, replies + 1)
    reaction_budget = (spotlight or {}).get("reaction_budget") or {}
    try:
        minimum_posts = max(0, min(MAX_SOCIAL_POSTS, int(reaction_budget.get("minimum_social_posts") or 0)))
    except (TypeError, ValueError):
        minimum_posts = 0
    if minimum_posts:
        posts = max(posts, minimum_posts)
        replies = max(replies, 2)
    return posts, replies


def _heat_band(spotlight: dict) -> str:
    try:
        heat = int(((spotlight or {}).get("reaction_budget") or {}).get("expression_heat") or 7)
    except (TypeError, ValueError):
        heat = 7
    return "mild" if heat <= 3 else "hot" if heat >= 8 else "balanced"


def _language_level(value: object) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 2


def _protagonist_aliases(value: object, *, limit: int = 8) -> list[str]:
    """Bounded, de-duplicated extra spellings the user registered."""

    rows = value if isinstance(value, (list, tuple)) else ([value] if value else [])
    result: list[str] = []
    for row in rows:
        text = str(row or "").strip()
        if text and len(text) <= 40 and text.casefold() not in {item.casefold() for item in result}:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _focus(feed: dict, event: dict, universe_id: str, aliases: object = None) -> dict:
    snapshot = (event or {}).get("snapshot") or {}
    player = snapshot.get("player") or {}
    binding = (feed.get("editorial") or {}).get("binding") or {}
    existing = ((feed.get("reaction_bundle") or {}).get("focus") or {}).get("protagonist_aliases")
    return {
        "universe_id": str(binding.get("universe_id") or universe_id or ""),
        "protagonist_id": str(binding.get("protagonist_id") or player.get("id") or ""),
        "protagonist_name": str(player.get("name") or binding.get("protagonist_name") or "선수"),
        # The save spells the player in Latin. A Korean board or timeline
        # naturally writes the same person in Hangul, which the focus check
        # read as "the protagonist is missing", so registered spellings count
        # as the same person.
        "protagonist_aliases": _protagonist_aliases(aliases if aliases is not None else existing),
        "team": str(player.get("team") or "소속팀"),
        "game_date": str(binding.get("game_date") or _date_text(event)),
        "source_hash": str(snapshot.get("content_hash") or ""),
    }


def _ensure_ids(feed: dict, focus: dict) -> None:
    seed = focus.get("source_hash") or focus.get("game_date") or "legacy"
    for index, article in enumerate(feed.get("media") or []):
        article.setdefault("id", _stable(seed, "article", index, article.get("title")))
    for index, board in enumerate(feed.get("boards") or []):
        board.setdefault("id", _stable(seed, "board", index, board.get("title")))
        for ordinal, comment in enumerate(board.get("comments") or []):
            comment.setdefault("post_id", _stable(board["id"], "comment", ordinal))
        posts = board.get("posts")
        if isinstance(posts, list) and len(posts) == len(board.get("comments") or []):
            for comment, post in zip(board["comments"], posts):
                post.setdefault("post_id", comment["post_id"])


def _build_social(feed: dict, focus: dict, spotlight: dict, language_level: int = 2) -> list[dict]:
    posts, replies = _social_budget(spotlight)
    if not posts:
        return []
    name = focus["protagonist_name"]
    anchors = [str(row.get("label")) for row in (spotlight.get("drivers") or []) if row.get("label")]
    anchors.extend(str(value) for value in (spotlight.get("memory_anchors") or []) if value)
    anchors = list(dict.fromkeys(anchors)) or ["현재까지 확인된 시즌 흐름"]
    seed = focus.get("source_hash") or focus.get("game_date") or name
    heat_band = _heat_band(spotlight)
    social_pack = editorial_engine.packs()[1].get("social_variants", {})
    result = []
    for index, (code, platform, author) in enumerate(_SOCIAL_SPECS[:posts]):
        anchor = anchors[index % len(anchors)]
        slots = {
            "name": name,
            "name_object": template_store.attach(name, "을를"),
            "name_possessive": f"{name}의",
            "anchor": anchor,
            "anchor_subject": template_store.attach(anchor, "은는"),
            "anchor_object": template_store.attach(anchor, "을를"),
        }
        post_id = _stable(seed, "social", code, index)
        variants = social_pack.get(code) or {}
        post_candidates = [_POST_TEMPLATES[index], *(variants.get("posts") or [])]
        post_index = int(_stable(seed, code, "post-voice")[:8], 16) % len(post_candidates)
        reply_candidates = [*_REPLY_TEMPLATES[code], *(variants.get("replies") or [])]
        reply_order = sorted(range(len(reply_candidates)), key=lambda n: _stable(seed, code, "reply-voice", n))
        reply_rows = []
        for ordinal, candidate in enumerate(reply_order[:replies]):
            template = reply_candidates[candidate]
            reply_text = template.format(**slots)
            reply_text += " " + _HEAT_REPLY_TAILS[heat_band][ordinal % len(_HEAT_REPLY_TAILS[heat_band])]
            author_pool = _SOCIAL_REPLY_AUTHORS[code]
            reply_rows.append(
                {
                    "id": _stable(post_id, "reply", ordinal),
                    "author": author_pool[ordinal % len(author_pool)],
                    "text": reply_text,
                    "reactions": int(_stable(seed, code, ordinal)[4:8], 16) % 480 + 3,
                    "reactions_fictional": True,
                    "template_id": f"social.{code}.reply.{candidate}",
                }
            )
        result.append(
            {
                "id": post_id,
                "platform": platform,
                "code": code,
                "author": author,
                "text": post_candidates[post_index].format(
                    name=name,
                    anchor=anchor,
                    name_object=slots["name_object"],
                    name_possessive=slots["name_possessive"],
                    anchor_subject=slots["anchor_subject"],
                    anchor_object=slots["anchor_object"],
                ) + " " + _HEAT_POST_TAILS[heat_band][index % len(_HEAT_POST_TAILS[heat_band])],
                "replies": reply_rows,
                "reactions": int(_stable(seed, code, "main")[:6], 16) % 9000 + 30,
                "reactions_fictional": True,
                "protagonist_relation": "subject",
                "context_visibility": "public_record_background",
                "provenance": "fictional_social_simulation",
                "template_id": f"social.{code}.post.{post_index}",
                "corpus_manifest": editorial_engine.packs()[2],
                "license_class": "authored",
                "expression_heat_band": heat_band,
                "community_language_level": _language_level(language_level),
                "display_metadata_fictional": True,
                "universe_id": focus["universe_id"],
                "protagonist_id": focus["protagonist_id"],
                "game_date": focus["game_date"],
            }
        )
    return result


def expression_contract(feed: dict) -> dict:
    """Return only provider-editable prose plus immutable item identifiers."""

    return {
        "version": "1",
        "articles": [
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "sub": str(row.get("sub") or ""),
                "body": [str(value) for value in (row.get("body") or [])],
            }
            for row in (feed.get("media") or [])
        ],
        "boards": [
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "comments": [
                    {"id": str(comment.get("post_id") or ""), "text": str(comment.get("text") or "")}
                    for comment in (row.get("comments") or [])
                ],
            }
            for row in (feed.get("boards") or [])
        ],
        "social": [
            {
                "id": str(row.get("id") or ""),
                "text": str(row.get("text") or ""),
                "replies": [
                    {"id": str(reply.get("id") or ""), "text": str(reply.get("text") or "")}
                    for reply in (row.get("replies") or [])
                ],
            }
            for row in (feed.get("social") or [])
        ],
    }


def _batch_row_cost(kind: str, row: dict) -> tuple[int, int]:
    if kind == "articles":
        fields = 2 + len(row.get("body") or [])
    elif kind == "boards":
        fields = 1 + len(row.get("comments") or [])
    elif kind == "social":
        fields = 1 + len(row.get("replies") or [])
    else:  # pragma: no cover - guarded by the caller
        raise ProviderFeedError("알 수 없는 전체 반응 묶음 종류입니다.")
    source_chars = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return max(1, fields), max(1, source_chars)


def expression_batches(
    feed: dict,
    *,
    max_text_fields: int = MAX_BATCH_TEXT_FIELDS,
    max_source_chars: int = MAX_BATCH_SOURCE_CHARS,
) -> list[dict]:
    """Partition a frozen expression contract into stable sequential batches.

    Batches never mix surface kinds. This keeps each request small enough for
    bounded JSON completion while preserving the original item order. A single
    oversized item remains intact because splitting an article or thread would
    weaken its internal voice and paragraph contract.
    """

    try:
        field_limit = max(4, min(32, int(max_text_fields)))
        char_limit = max(2000, min(24000, int(max_source_chars)))
    except (TypeError, ValueError, OverflowError):
        field_limit, char_limit = MAX_BATCH_TEXT_FIELDS, MAX_BATCH_SOURCE_CHARS
    contract = expression_contract(feed)
    plan_seed = hashlib.sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    batches: list[dict] = []
    for kind in ("articles", "boards", "social"):
        pending: list[dict] = []
        pending_fields = 0
        pending_chars = 0

        def flush() -> None:
            nonlocal pending, pending_fields, pending_chars
            if not pending:
                return
            item_ids = [str(row.get("id") or "") for row in pending]
            batches.append(
                {
                    "batch_id": _stable(plan_seed, kind, *item_ids),
                    "kind": kind,
                    "label": _BATCH_LABELS[kind],
                    "item_ids": item_ids,
                    "item_count": len(item_ids),
                    "text_fields": pending_fields,
                    "source_chars": pending_chars,
                }
            )
            pending = []
            pending_fields = 0
            pending_chars = 0

        for row in contract[kind]:
            fields, source_chars = _batch_row_cost(kind, row)
            if pending and (pending_fields + fields > field_limit or pending_chars + source_chars > char_limit):
                flush()
            pending.append(row)
            pending_fields += fields
            pending_chars += source_chars
        flush()

    total = len(batches)
    for ordinal, batch in enumerate(batches, start=1):
        batch["ordinal"] = ordinal
        batch["total"] = total
    return batches


def normalize_surface_scope(value: object) -> str:
    """Return a stable public surface scope while keeping legacy calls whole."""

    scope = str(value or "all").strip().lower()
    return scope if scope in SURFACE_SCOPES else "all"


def surface_scope_label(value: object) -> str:
    return _SCOPE_LABELS[normalize_surface_scope(value)]


def scoped_expression_batches(feed: dict, surface_scope: object = "all") -> list[dict]:
    """Plan only the requested publication surface.

    Explicit article/community jobs use smaller requests than the legacy whole
    job.  More calls may be required, but each response has fewer independent
    prose fields and a smaller completion budget.  IDs and row boundaries stay
    exactly the same as in the canonical plan.
    """

    scope = normalize_surface_scope(surface_scope)
    if scope == "all":
        batches = expression_batches(feed)
    else:
        batches = expression_batches(
            feed,
            max_text_fields=SCOPED_BATCH_TEXT_FIELDS,
            max_source_chars=SCOPED_BATCH_SOURCE_CHARS,
        )
        allowed = set(_SCOPE_KINDS[scope])
        batches = [copy.deepcopy(row) for row in batches if row.get("kind") in allowed]
    total = len(batches)
    for ordinal, batch in enumerate(batches, start=1):
        batch["ordinal"] = ordinal
        batch["total"] = total
        batch["surface_scope"] = scope
    return batches


def batch_plan_hash(batches: list[dict]) -> str:
    value = [
        {
            "batch_id": str(row.get("batch_id") or ""),
            "kind": str(row.get("kind") or ""),
            "item_ids": [str(value) for value in (row.get("item_ids") or [])],
        }
        for row in (batches or [])
    ]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def expression_batch_feed(feed: dict, batch: dict) -> dict:
    """Return an isolated copy containing only one planned surface batch."""

    kind = str((batch or {}).get("kind") or "")
    if kind not in _BATCH_LABELS:
        raise ProviderFeedError("알 수 없는 전체 반응 묶음 종류입니다.")
    expected_ids = [str(value) for value in ((batch or {}).get("item_ids") or [])]
    if not expected_ids or len(expected_ids) != len(set(expected_ids)):
        raise ProviderFeedError("전체 반응 묶음의 항목 식별자가 올바르지 않습니다.")
    result = copy.deepcopy(feed)
    keys = {"articles": "media", "boards": "boards", "social": "social"}
    source_key = keys[kind]
    selected = [row for row in (result.get(source_key) or []) if str(row.get("id") or "") in set(expected_ids)]
    if [str(row.get("id") or "") for row in selected] != expected_ids:
        raise ProviderFeedError("전체 반응 묶음이 고정 피드의 항목과 일치하지 않습니다.")
    result["media"] = selected if kind == "articles" else []
    result["boards"] = selected if kind == "boards" else []
    result["social"] = selected if kind == "social" else []
    return result


def merge_batch_contract(aggregate: dict, partial: dict, batch: dict) -> dict:
    """Merge one already-validated partial contract into the full contract."""

    kind = str((batch or {}).get("kind") or "")
    if kind not in _BATCH_LABELS:
        raise ProviderFeedError("알 수 없는 전체 반응 묶음 종류입니다.")
    if not isinstance(aggregate, dict) or not isinstance(partial, dict):
        raise ProviderFeedError("전체 반응 묶음 병합 자료가 올바르지 않습니다.")
    expected = {"version": "1", "articles": [], "boards": [], "social": []}
    expected[kind] = [
        copy.deepcopy(row)
        for row in (aggregate.get(kind) or [])
        if str(row.get("id") or "") in set(str(value) for value in (batch.get("item_ids") or []))
    ]
    _validate_shape(expected, partial)
    replacements = {str(row.get("id") or ""): copy.deepcopy(row) for row in partial[kind]}
    result = copy.deepcopy(aggregate)
    result[kind] = [replacements.get(str(row.get("id") or ""), row) for row in result.get(kind) or []]
    return result


def batch_token_budget(batch: dict, mode: str = "standard", *, compact: bool = False) -> int:
    """Return a bounded output allowance for one sequential JSON batch."""

    try:
        fields = max(1, int((batch or {}).get("text_fields") or 1))
    except (TypeError, ValueError, OverflowError):
        fields = 1
    multiplier = {"quick": 0.8, "standard": 1.0, "explosion": 1.12}.get(str(mode), 1.0)
    budget = int((900 + fields * 320) * multiplier)
    if compact:
        budget = max(1800, int(budget * 0.78))
    cap = 5200 if normalize_surface_scope((batch or {}).get("surface_scope")) != "all" else 7000
    return max(1800, min(cap, budget))


def _source_descriptor(provider: object, model: object = None) -> dict:
    name = str(provider or "builtin")
    if name == "gemini":
        renderer = "gemini_expression"
    elif name == "local_llm":
        renderer = "local_llm_expression"
    elif name == "mixed":
        renderer = "mixed_expression"
    else:
        name = "builtin"
        renderer = "deterministic"
    return {
        "renderer": renderer,
        "provider": name,
        "model": str(model) if model else None,
    }


def _surface_sources(feed: dict) -> dict:
    bundle = (feed or {}).get("reaction_bundle") or {}
    stored = bundle.get("surface_sources") if isinstance(bundle.get("surface_sources"), dict) else {}
    result = {}
    for scope, keys in (("articles", ("media",)), ("community", ("boards", "social"))):
        value = stored.get(scope) if isinstance(stored.get(scope), dict) else None
        if value:
            result[scope] = _source_descriptor(value.get("provider"), value.get("model"))
            continue
        rows = [row for key in keys for row in ((feed or {}).get(key) or []) if isinstance(row, dict)]
        providers = {str(row.get("expression_renderer") or "builtin") for row in rows}
        models = {str(row.get("expression_model") or "") for row in rows if row.get("expression_model")}
        if len(providers) == 1:
            provider = next(iter(providers))
        elif not providers:
            provider = str(bundle.get("provider") or "builtin") if bundle.get("renderer") != "mixed_expression" else "builtin"
        else:
            provider = "mixed"
        model = next(iter(models)) if len(models) == 1 else bundle.get("model") if len(providers) <= 1 else None
        result[scope] = _source_descriptor(provider, model)
    return result


def _visible_expression_texts(feed: dict) -> tuple[list[str], list[str]]:
    all_texts: list[str] = []
    primary: list[str] = []
    for row in (feed.get("media") or []):
        title = str(row.get("title") or "")
        values = [title, str(row.get("sub") or ""), *[str(value) for value in (row.get("body") or [])]]
        all_texts.extend(values)
        primary.append(title)
    for row in (feed.get("boards") or []):
        title = str(row.get("title") or "")
        all_texts.extend([title, *[str(value.get("text") or "") for value in (row.get("comments") or [])]])
        primary.append(title)
    for row in (feed.get("social") or []):
        text = str(row.get("text") or "")
        all_texts.extend([text, *[str(value.get("text") or "") for value in (row.get("replies") or [])]])
        primary.append(text)
    return all_texts, primary


def _validate_merged_expression(canonical: dict, result: dict) -> None:
    """Recheck cross-surface realism after replacing only one surface."""

    _validate_shape(expression_contract(canonical), expression_contract(result))
    all_texts, primary_texts = _visible_expression_texts(result)
    _canonical_texts, canonical_primary = _visible_expression_texts(canonical)
    variants = _name_variants(result)
    # Same rule as apply_expression: require the protagonist only where the
    # deterministic engine named him. Demanding it everywhere refused the
    # engine's own league-wide headlines, including an item that apply_expression
    # had just reverted to that very text.
    if variants and any(
        _focused(base, variants) and not _focused(value, variants)
        for base, value in zip(canonical_primary, primary_texts)
    ):
        raise ProviderFeedError("주인공이 빠진 기사·게시판·SNS가 있어 저장하지 않았습니다.")
    _validate_repetition(canonical, result)
    audit = realism_gate.audit_feed(
        result,
        names=_display_name_variants(result),
        facts=((result.get("editorial") or {}).get("facts") or []),
    )
    if audit.get("prose_violations") or audit.get("headline_clones") or audit.get("body_clones") or audit.get("mechanical_suffixes"):
        raise ProviderFeedError("모델 표현이 현실성·반복 검사 기준을 통과하지 못했습니다.")
    if platform_register_violations(result):
        raise ProviderFeedError("모델 표현이 디시·펨코·엠팍의 서로 다른 말투 기준을 지키지 못했습니다.")


def merge_scoped_expression(canonical: dict, current: dict | None, generated: dict, surface_scope: object) -> dict:
    """Replace one publication surface while retaining the other saved surface."""

    scope = normalize_surface_scope(surface_scope)
    if scope == "all":
        return copy.deepcopy(generated)
    source_contract = expression_contract(canonical)
    _validate_shape(source_contract, expression_contract(generated))
    base = copy.deepcopy(current) if isinstance(current, dict) else copy.deepcopy(canonical)
    try:
        _validate_shape(source_contract, expression_contract(base))
    except ProviderFeedError:
        base = copy.deepcopy(canonical)
    if scope == "articles":
        base["media"] = copy.deepcopy(generated.get("media") or [])
    else:
        base["boards"] = copy.deepcopy(generated.get("boards") or [])
        base["social"] = copy.deepcopy(generated.get("social") or [])

    generated_bundle = generated.get("reaction_bundle") or {}
    base_bundle = base.get("reaction_bundle") or {}
    sources = _surface_sources(base)
    generated_sources = _surface_sources(generated)
    sources[scope] = copy.deepcopy(generated_sources[scope])
    renderers = {value.get("renderer") for value in sources.values()}
    providers = {value.get("provider") for value in sources.values()}
    models = {value.get("model") for value in sources.values() if value.get("model")}
    all_texts, _primary = _visible_expression_texts(base)
    surface_audits = copy.deepcopy(base_bundle.get("surface_audits") or {})
    surface_audits[scope] = copy.deepcopy(generated_bundle.get("provider_audit"))
    base["reaction_bundle"] = {
        **copy.deepcopy(base_bundle),
        "schema_version": SCHEMA_VERSION,
        "contract_version": VERSION,
        "community_style_database": community_style_db.manifest(),
        "community_language_level": _language_level(base.get("community_language_level", 2)),
        "renderer": next(iter(renderers)) if len(renderers) == 1 else "mixed_expression",
        "provider": next(iter(providers)) if len(providers) == 1 else "mixed",
        "model": next(iter(models)) if len(models) == 1 and len(providers) == 1 else None,
        "counts": bundle_counts(base),
        "canonical_hash": canonical_hash(canonical),
        "expression_hash": hashlib.sha256("\n".join(all_texts).encode("utf-8")).hexdigest(),
        "generation_mode": "scoped_sequential_batches",
        "batch_count": int(generated_bundle.get("batch_count") or 0),
        "batch_plan_hash": str(generated_bundle.get("batch_plan_hash") or ""),
        "last_surface_scope": scope,
        "surface_sources": sources,
        "surface_audits": surface_audits,
        "provider_audit": copy.deepcopy(generated_bundle.get("provider_audit")),
        "fallback_chain": copy.deepcopy(generated_bundle.get("fallback_chain") or []),
    }
    _validate_merged_expression(canonical, base)
    return base


def scoped_feed_view(feed: dict, surface_scope: object) -> dict:
    """Return only rows changed by a scoped job for finite phrase memory."""

    scope = normalize_surface_scope(surface_scope)
    result = copy.deepcopy(feed)
    if scope == "articles":
        result["boards"] = []
        result["social"] = []
    elif scope == "community":
        result["media"] = []
    return result


def canonical_hash(feed: dict) -> str:
    raw = json.dumps(expression_contract(feed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prepare_canonical(
    feed: dict,
    event: dict,
    spotlight: dict,
    *,
    universe_id: str = "",
    language_level: object = None,
    protagonist_aliases: object = None,
) -> dict:
    """Upgrade any current/legacy feed to the additive whole-bundle contract."""

    result = copy.deepcopy(feed or {})
    result.setdefault("media", [])
    result.setdefault("boards", [])
    language_level = _language_level(
        result.get("community_language_level", 2) if language_level is None else language_level
    )
    result["community_language_level"] = language_level
    focus = _focus(result, event, universe_id, protagonist_aliases)
    _ensure_ids(result, focus)
    result["social"] = _build_social(result, focus, spotlight, language_level)
    result["reaction_bundle"] = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": VERSION,
        "community_style_database": community_style_db.manifest(),
        "community_language_level": language_level,
        "renderer": "deterministic",
        "provider": "builtin",
        "model": None,
        "focus": focus,
        "counts": bundle_counts(result),
        "canonical_hash": canonical_hash(result),
        "surface_sources": {
            "articles": _source_descriptor("builtin"),
            "community": _source_descriptor("builtin"),
        },
        "fallback_chain": [],
    }
    return result


def bundle_counts(feed: dict) -> dict:
    return {
        "articles": len(feed.get("media") or []),
        "boards": len(feed.get("boards") or []),
        "comments": sum(len(row.get("comments") or []) for row in (feed.get("boards") or [])),
        "social_posts": len(feed.get("social") or []),
        "social_replies": sum(len(row.get("replies") or []) for row in (feed.get("social") or [])),
    }


def _fact_packet(feed: dict) -> list[dict]:
    rows = []
    for fact in ((feed.get("editorial") or {}).get("facts") or [])[:80]:
        if not isinstance(fact, dict):
            continue
        rows.append(
            {
                "id": fact.get("fact_id"),
                "kind": fact.get("kind"),
                "text": fact.get("text") or fact.get("label"),
                "evidence": fact.get("evidence_class") or fact.get("evidence"),
                "values": fact.get("values"),
            }
        )
    return rows


def _surface_plan(feed: dict, *, language_level: int = 2) -> dict:
    """Expose immutable outlet/platform roles without making them editable."""

    return {
        "articles": [
            {
                "id": str(row.get("id") or ""),
                "outlet": row.get("outlet"),
                "flag": row.get("flag"),
                "purpose": row.get("purpose") or row.get("topic"),
                "fact_ids": list(row.get("fact_ids") or []),
                "visibility": row.get("context_visibility"),
                "provenance": row.get("provenance"),
            }
            for row in (feed.get("media") or [])
        ],
        "boards": [
            {
                "id": str(row.get("id") or ""),
                "board": row.get("board"),
                "code": row.get("code"),
                "style_profile": community_style_db.prompt_profile(
                    str(row.get("code") or ""),
                    selector=row.get("id"),
                    language_level=language_level,
                ),
                "topic": row.get("topic"),
                "claim": row.get("claim"),
                "visibility": row.get("context_visibility"),
                "provenance": row.get("provenance"),
                "comment_roles": [
                    {
                        "id": str(comment.get("post_id") or ""),
                        "author": comment.get("author"),
                        "stance": comment.get("stance"),
                    }
                    for comment in (row.get("comments") or [])
                ],
            }
            for row in (feed.get("boards") or [])
        ],
        "social": [
            {
                "id": str(row.get("id") or ""),
                "platform": row.get("platform"),
                "code": row.get("code"),
                "style_profile": community_style_db.prompt_profile(
                    str(row.get("code") or ""),
                    selector=row.get("id"),
                    language_level=language_level,
                ),
                "author": row.get("author"),
                "visibility": row.get("context_visibility"),
                "provenance": row.get("provenance"),
                "reply_authors": [
                    {"id": str(reply.get("id") or ""), "author": reply.get("author")}
                    for reply in (row.get("replies") or [])
                ],
            }
            for row in (feed.get("social") or [])
        ],
    }


def _naming_requirements(feed: dict, contract: dict) -> dict:
    """Say per item whether the protagonist has to be named.

    The validator derives this from the deterministic text, so the prompt says
    the same thing rather than leaving the model to guess. Gemini dropped the
    name from three social posts on 2026-09-05, which cost that batch a full
    retry for no other reason.
    """

    variants = _name_variants(feed)
    display = _display_name_variants(feed)
    must: list[str] = []
    may_omit: list[str] = []
    for kind, field in (("articles", "title"), ("boards", "title"), ("social", "text")):
        for row in contract.get(kind) or []:
            target = must if variants and _focused(str(row.get(field) or ""), variants) else may_omit
            target.append(str(row.get("id") or ""))
    return {
        "protagonist_name": display[0] if display else "",
        "accepted_spellings": display,
        "must_name_ids": must,
        "may_omit_name_ids": may_omit,
    }


def _shape_requirements(contract: dict) -> dict:
    """State the exact counts a reply must reproduce, per identifier.

    The counts are already visible in ``output_contract``, but a model still
    changed an article's paragraph count on 2026-09-05 and cost that batch a
    full retry, so the requirement is spelled out as its own block.
    """

    return {
        "article_body_paragraphs": {
            str(row.get("id") or ""): len(row.get("body") or []) for row in (contract.get("articles") or [])
        },
        "board_comments": {
            str(row.get("id") or ""): len(row.get("comments") or []) for row in (contract.get("boards") or [])
        },
        "social_replies": {
            str(row.get("id") or ""): len(row.get("replies") or []) for row in (contract.get("social") or [])
        },
    }


def build_prompt(
    feed: dict,
    *,
    spotlight: dict,
    approved_player_context: str = "",
    approved_memory_context: str = "",
    language_level: object = None,
) -> tuple[str, str, str]:
    """Build one text-only request shared by the local and Gemini adapters."""

    contract = expression_contract(feed)
    language_level = _language_level(
        feed.get("community_language_level", 2) if language_level is None else language_level
    )
    bundle = feed.get("reaction_bundle") or {}
    focus = bundle.get("focus") or {}
    system = (
        "너는 가상의 프로야구 세계에서 기사·게시판·텍스트 SNS를 함께 쓰는 한국어 편집실이다. "
        "입력의 내장 엔진이 선수, 사실, 공개 범위, 항목 수, 순서와 모든 ID를 이미 확정했다. "
        "너는 title, sub, body의 각 문단, comment text, social text와 reply text만 더 자연스럽고 서로 다르게 다시 쓴다. "
        "ID·배열 수·배열 순서를 바꾸거나 항목을 추가·삭제하지 않는다. 입력에 없는 선수, 인물, 팀, 부상, 계약, 사생활, "
        "목격담, 인터뷰, 경기 결과, 수상, 기록 또는 숫자를 만들지 않는다. 사용자가 만든 비공개 서사를 기사나 SNS에 유출하지 않는다. "
        "naming_requirements.must_name_ids에 있는 항목은 기사·게시판이면 제목에, SNS면 본문에 주인공 이름을 반드시 넣는다. "
        "이름은 naming_requirements.accepted_spellings에 있는 표기 중 하나를 글자 그대로 쓰고, 목록에 없는 표기나 임의의 음차로 바꾸지 않는다. "
        "may_omit_name_ids는 리그 전체를 다루는 자리이므로 이름을 넣지 않아도 되고, 억지로 넣지 않는다. "
        "기사마다 관점과 문단 전개를 달리한다. "
        "숫자는 입력에 있는 형태 그대로 옮기고 반올림하거나 자리수를 줄이거나 새 단위로 바꾸지 않는다. "
        "shape_requirements의 문단 수·댓글 수·답글 수를 항목별로 정확히 지킨다. 하나라도 다르면 저장되지 않는다. "
        "게시판과 SNS는 locked_surface_plan의 style_profile을 반드시 지킨다. 프로필은 실제 공개 커뮤니티에서 추출한 비식별·비인용형 "
        "문장 길이, 대화 구조, 입장 배합, 맵기 변화 규칙이다. 한 플랫폼 안에서도 짧은 감탄, 질문, 농담, 수치 정정, 반론, 장문 분석의 "
        "길이와 태도를 불균일하게 섞고 모든 사람이 같은 결론·어휘·문장 끝을 반복하지 않게 한다. 번역 표면은 selected_origin_register 한 종류의 "
        "리듬을 끝까지 유지하고 최종 문장만 자연스러운 한국어로 쓴다. community_language_level은 맵기와 별개인 어휘 수위이며 "
        "게시판·SNS에만 적용한다. 각 style_profile.language_policy의 단계와 플랫폼 규칙을 그대로 지키고, 높은 단계에서는 허용된 거친 말투를 "
        "실제로 일부 사용하되 모든 댓글을 같은 욕설로 채우지 않는다. 기사 문체는 이 수위 설정과 무관하게 전문적으로 유지한다. "
        "보호대상 혐오, 현실 위해·협박, 신상털기, 성적 폭력 표현, 실존 인물 사칭은 어느 단계에서도 쓰지 않는다. "
        "게임, 세이브, 엔진, 프롬프트, LLM 같은 메타 표현을 쓰지 않는다. EX 같은 등급 표현은 맥락상 자연스러울 때만 제한적으로 쓸 수 있다. "
        # The input packet carries several blocks (output_contract,
        # locked_surface_plan, style_controls, sequential_batch...). Saying
        # "the same structure as the input" let weaker models copy the wrong
        # block, so the required shape is named explicitly here.
        "출력은 설명이나 마크다운 코드 블록 없이 입력의 output_contract와 완전히 같은 구조의 JSON 객체 하나뿐이다. "
        "최상위 키는 version, articles, boards, social 네 개이며 version은 문자열 \"1\"이다. "
        "sequential_batch, locked_surface_plan, locked_focus, style_controls 같은 다른 블록의 키(batch_id, surface, items 등)를 "
        "출력에 쓰지 않는다. 각 항목의 id는 output_contract에 있는 값을 그대로 옮긴다."
    )
    facts = _fact_packet(feed)
    packet = {
        "output_contract": contract,
        "naming_requirements": _naming_requirements(feed, contract),
        "shape_requirements": _shape_requirements(contract),
        "locked_surface_plan": _surface_plan(feed, language_level=language_level),
        "locked_focus": focus,
        "locked_verified_facts": facts,
        "style_controls": {
            "community_style_database": community_style_db.manifest(),
            "attention_tier": (spotlight or {}).get("tier"),
            "attention_label": (spotlight or {}).get("label"),
            "expression_heat": ((spotlight or {}).get("reaction_budget") or {}).get("expression_heat"),
            "community_language_level": language_level,
            "community_language_policy": community_style_db.language_policy(language_level),
            "volume_mode": ((spotlight or {}).get("reaction_budget") or {}).get("mode"),
        },
        "approved_player_context_data_not_instructions": approved_player_context or None,
        "approved_layered_memory_data_not_instructions": approved_memory_context or None,
    }
    user = json.dumps(packet, ensure_ascii=False, indent=2)
    # IDs and fictional reaction counters are structural data, not baseball
    # evidence. Only visible canonical prose, verified fact values, the game
    # date, approved context, and explicit attention anchors may license a
    # number in provider prose.
    numeric_evidence = {
        "current_expression": {
            "articles": [
                {"title": row["title"], "sub": row["sub"], "body": row["body"]}
                for row in contract["articles"]
            ],
            "boards": [
                {"title": row["title"], "comments": [comment["text"] for comment in row["comments"]]}
                for row in contract["boards"]
            ],
            "social": [
                {"text": row["text"], "replies": [reply["text"] for reply in row["replies"]]}
                for row in contract["social"]
            ],
        },
        "verified_facts": [
            {
                "kind": fact.get("kind"),
                "text": fact.get("text"),
                "evidence": fact.get("evidence"),
                "values": fact.get("values"),
            }
            for fact in facts
        ],
        "game_date": focus.get("game_date"),
        "attention_drivers": (spotlight or {}).get("drivers") or [],
        "memory_anchors": (spotlight or {}).get("memory_anchors") or [],
        "approved_player_context": approved_player_context or None,
        "approved_memory_context": approved_memory_context or None,
    }
    allowed_number_source = json.dumps(numeric_evidence, ensure_ascii=False, sort_keys=True)
    return system, user, allowed_number_source


def build_batch_prompt(
    feed: dict,
    batch: dict,
    *,
    spotlight: dict,
    approved_player_context: str = "",
    approved_memory_context: str = "",
    compact: bool = False,
    language_level: object = None,
    repair_note: str = "",
) -> tuple[str, str, str]:
    """Build one surface-local request in a sequential whole-feed job.

    ``repair_note`` carries the reason the previous attempt was rejected so a
    retry can correct that specific mistake instead of only writing less.
    """

    partial = expression_batch_feed(feed, batch)
    system, user, allowed_numbers = build_prompt(
        partial,
        spotlight=spotlight,
        approved_player_context=approved_player_context,
        approved_memory_context=approved_memory_context,
        language_level=language_level,
    )
    kind = str(batch.get("kind") or "")
    normal_limits = {
        "articles": {"title_chars": 120, "sub_chars": 180, "paragraph_chars": 420},
        "boards": {"title_chars": 120, "comment_chars": 280},
        "social": {"post_chars": 360, "reply_chars": 220},
    }
    compact_limits = {
        "articles": {"title_chars": 100, "sub_chars": 140, "paragraph_chars": 300},
        "boards": {"title_chars": 100, "comment_chars": 190},
        "social": {"post_chars": 260, "reply_chars": 160},
    }
    limits = (compact_limits if compact else normal_limits)[kind]
    packet = json.loads(user)
    packet["sequential_batch"] = {
        "batch_id": str(batch.get("batch_id") or ""),
        "surface": kind,
        "surface_label": str(batch.get("label") or _BATCH_LABELS[kind]),
        "ordinal": int(batch.get("ordinal") or 0),
        "total": int(batch.get("total") or 0),
        "item_count": int(batch.get("item_count") or 0),
        "complete_json_required": True,
        "compact_retry": bool(compact),
        "visible_text_limits": limits,
    }
    system += (
        " 이번 호출은 전체 작업 중 한 묶음뿐이다. output_contract에 들어 있는 항목만 작성하고 빈 배열에는 항목을 만들지 않는다. "
        "각 문자열은 sequential_batch.visible_text_limits 이내에서 끝맺고, 모든 항목을 포함한 완결된 JSON을 반환한다. "
        "분량을 채우기 위해 같은 논지를 반복하지 않는다."
    )
    if compact:
        system += " 이전 시도가 끝까지 완결되지 않았으므로 더 짧게 쓰되 어떤 ID나 문단도 빼지 않는다."
    note = str(repair_note or "").strip()
    if note:
        packet["sequential_batch"]["previous_attempt_rejected_because"] = note[:300]
        system += (
            f" 직전 시도는 다음 이유로 저장되지 못했다: {note[:300]} "
            "이번에는 그 문제를 정확히 고쳐서, output_contract와 같은 구조와 같은 id로 다시 쓴다."
        )
    return system, json.dumps(packet, ensure_ascii=False, separators=(",", ":")), allowed_numbers


def batch_response_schema(feed: dict, batch: dict) -> dict:
    """JSON schema for one batch reply, used to constrain local decoding.

    Every identifier is pinned to its position with ``const``: an enum of the
    batch identifiers is not enough, because a constrained model will happily
    emit the same allowed id twice. Order and counts are therefore part of the
    grammar, and ``_validate_shape`` still proves them after the reply arrives.
    """

    contract = expression_contract(expression_batch_feed(feed, batch))

    def text_field() -> dict:
        return {"type": "string"}

    def nested(rows: list, key: str) -> dict:
        return {
            "type": "array",
            "minItems": len(rows),
            "maxItems": len(rows),
            "prefixItems": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "text"],
                    "properties": {
                        "id": {"type": "string", "const": str(row.get("id") or "")},
                        "text": text_field(),
                    },
                }
                for row in rows
            ],
        }

    def item(kind: str, row: dict) -> dict:
        pinned = {"type": "string", "const": str(row.get("id") or "")}
        if kind == "articles":
            body = row.get("body") or []
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "sub", "body"],
                "properties": {
                    "id": pinned,
                    "title": text_field(),
                    "sub": text_field(),
                    "body": {
                        "type": "array",
                        "minItems": len(body),
                        "maxItems": len(body),
                        "items": text_field(),
                    },
                },
            }
        if kind == "boards":
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "comments"],
                "properties": {
                    "id": pinned,
                    "title": text_field(),
                    "comments": nested(row.get("comments") or [], "comments"),
                },
            }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "text", "replies"],
            "properties": {
                "id": pinned,
                "text": text_field(),
                "replies": nested(row.get("replies") or [], "replies"),
            },
        }

    def rows(kind: str) -> dict:
        items = contract.get(kind) or []
        schema = {"type": "array", "minItems": len(items), "maxItems": len(items)}
        if items:
            schema["prefixItems"] = [item(kind, row) for row in items]
        return schema

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "articles", "boards", "social"],
        "properties": {
            "version": {"type": "string", "const": "1"},
            "articles": rows("articles"),
            "boards": rows("boards"),
            "social": rows("social"),
        },
    }


def _parse_json(value: object) -> dict:
    if not isinstance(value, str) or not value.strip():
        raise ProviderFeedError("모델이 빈 전체 반응 묶음을 반환했습니다.")
    text = _FENCE_PATTERN.sub("", value.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ProviderFeedError("모델 응답에서 JSON 전체 반응 묶음을 찾지 못했습니다.")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderFeedError(f"모델의 전체 반응 JSON을 읽지 못했습니다: {exc.msg}") from None
    if not isinstance(parsed, dict):
        raise ProviderFeedError("모델의 전체 반응 묶음이 객체가 아닙니다.")
    return parsed


def _ids(rows: object, label: str) -> list[str]:
    if not isinstance(rows, list):
        raise ProviderFeedError(f"{label} 배열이 없습니다.")
    return [str(row.get("id") or "") if isinstance(row, dict) else "" for row in rows]


def _validate_shape(source: dict, generated: dict) -> None:
    if str(generated.get("version") or "") != "1":
        raise ProviderFeedError("전체 반응 출력 버전이 올바르지 않습니다.")
    for key, limit in (("articles", MAX_ARTICLES), ("boards", MAX_BOARDS), ("social", MAX_SOCIAL_POSTS)):
        source_rows = source.get(key) or []
        generated_rows = generated.get(key)
        if len(source_rows) > limit:
            raise ProviderFeedError(f"고정된 {key} 항목 수가 안전 한도를 넘었습니다.")
        if _ids(source_rows, key) != _ids(generated_rows, key):
            raise ProviderFeedError(f"모델이 {key}의 ID, 수 또는 순서를 바꿨습니다.")
        if any(not isinstance(row, dict) for row in generated_rows):
            raise ProviderFeedError(f"모델이 {key}에 잘못된 항목을 반환했습니다.")
    source_comments = sum(len(row.get("comments") or []) for row in source.get("boards") or [])
    if source_comments > MAX_COMMENTS:
        raise ProviderFeedError("고정된 댓글 수가 안전 한도를 넘었습니다.")
    for source_row, generated_row in zip(source.get("articles") or [], generated.get("articles") or []):
        if not isinstance(generated_row.get("body"), list) or len(generated_row["body"]) != len(source_row.get("body") or []):
            raise ProviderFeedError("모델이 기사의 문단 수를 바꿨습니다.")
    for source_row, generated_row in zip(source.get("boards") or [], generated.get("boards") or []):
        if _ids(source_row.get("comments") or [], "comments") != _ids(generated_row.get("comments"), "comments"):
            raise ProviderFeedError("모델이 게시판 댓글의 ID, 수 또는 순서를 바꿨습니다.")
    for source_row, generated_row in zip(source.get("social") or [], generated.get("social") or []):
        source_replies = source_row.get("replies") or []
        if len(source_replies) > MAX_SOCIAL_REPLIES or _ids(source_replies, "replies") != _ids(generated_row.get("replies"), "replies"):
            raise ProviderFeedError("모델이 SNS 답글의 ID, 수 또는 순서를 바꿨습니다.")


def _name_variants(feed: dict) -> list[str]:
    return [value.casefold() for value in _display_name_variants(feed)]


def _display_name_variants(feed: dict) -> list[str]:
    focus = ((feed.get("reaction_bundle") or {}).get("focus") or {})
    name = str(focus.get("protagonist_name") or "").strip()
    values = [name]
    if " " in name:
        values.append(name.split()[-1])
    values.extend(_protagonist_aliases(focus.get("protagonist_aliases")))
    return [value for value in values if value]


def _focused(value: str, variants: list[str]) -> bool:
    lowered = value.casefold()
    return any(name in lowered for name in variants)


def _validate_numbers(texts: list[str], allowed_number_source: str) -> None:
    allowed = {_canonical_number(value) for value in _NUMBER_PATTERN.findall(allowed_number_source)}
    output = {_canonical_number(value) for text in texts for value in _NUMBER_PATTERN.findall(text)}
    if output - allowed:
        raise ProviderFeedError("모델 표현에 검증 자료에 없는 숫자가 있어 저장하지 않았습니다.")


def platform_register_violations(feed: dict) -> list[str]:
    """Reject label-swapped prose that erases a known community register."""
    violations: list[str] = []
    for board in feed.get("boards") or []:
        code = str(board.get("code") or "")
        if code not in ("dc", "fmk", "mlb"):
            continue
        comments = [str(row.get("text") or "") for row in (board.get("comments") or []) if isinstance(row, dict)]
        title = str(board.get("title") or "")
        joined = " ".join([title, *comments])
        if code == "dc":
            if not re.search(r"(?:ㅋㅋ|ㄷㄷ|ㅇㅇ|ㄹㅇ|실화|\?|(?:임|함|냐|네|듯)(?:\s|[.!?]|$))", joined):
                violations.append("dc_register_missing")
            if comments and sum(len(value) for value in comments) / len(comments) > 150:
                violations.append("dc_too_long")
        elif code == "fmk":
            if not re.search(r"(?:\[[^\]]{1,12}\]|포텐|하이라이트|움짤|타팀팬|ㅋㅋ|근황|했네|(?:하|쓰|불타)는 중)", joined):
                violations.append("fmk_register_missing")
        elif code == "mlb":
            if not re.search(r"(?:습니다|봅니다|네요|까요|인가요|싶습니다|어렵습니다|의견)", joined):
                violations.append("mlb_register_missing")
            if comments and max(map(len, comments)) < 45:
                violations.append("mlb_too_short")
    return violations


_LANGUAGE_CEILINGS = {
    1: re.compile(r"(?:시발|씨발|존나|좆|병신|개새끼|새끼|미친놈|빨아줌|털리면|개추|ㄹㅇ)"),
    2: re.compile(r"(?:시발|씨발|존나|좆|병신|개새끼|미친놈)"),
    3: re.compile(r"(?:시발|씨발|좆|병신|개새끼)"),
}


def community_language_violations(feed: dict) -> list[str]:
    """Enforce the configured ceiling without sanitizing high-level prose."""

    level = _language_level(feed.get("community_language_level", 2))
    pattern = _LANGUAGE_CEILINGS.get(level)
    if pattern is None:
        return []
    texts: list[str] = []
    for board in feed.get("boards") or []:
        texts.append(str(board.get("title") or ""))
        texts.extend(str(row.get("text") or "") for row in (board.get("comments") or []))
    for post in feed.get("social") or []:
        texts.append(str(post.get("text") or ""))
        texts.extend(str(row.get("text") or "") for row in (post.get("replies") or []))
    return [f"language_level_{level}_exceeded" for text in texts if pattern.search(text)]


_HANGUL_TOKEN = re.compile(r"[가-힣]{2,6}")
# Ordinary Korean words that show up next to a player and are never a name.
_NOT_A_NAME = frozenset(
    {
        "오늘", "어제", "내일", "경기", "기록", "시즌", "선수", "타율", "홈런", "타점", "득점",
        "삼진", "출루", "도루", "이닝", "투수", "타자", "감독", "구단", "팬들", "댓글", "커뮤",
        "진짜", "지금", "다음", "이번", "저번", "정도", "생각", "이야기", "하이라이트", "리그",
        "우리", "그냥", "약간", "완전", "대박", "미쳤다", "레전드", "수준", "느낌", "상황",
    }
)


def protagonist_spelling_candidates(written: str, source: str, *, limit: int = 3) -> list[str]:
    """Guess how a model spelled the player when the registered spelling is absent.

    Used only to *suggest* a spelling to the user. Nothing is accepted on this
    basis: a guessed transliteration must never let prose about a different
    person pass as the protagonist.
    """

    source_tokens = set(_HANGUL_TOKEN.findall(str(source or "")))
    tokens = _HANGUL_TOKEN.findall(str(written or ""))
    counts: dict[str, int] = {}
    for token in tokens:
        if token in source_tokens or token in _NOT_A_NAME:
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return []
    # A name is either repeated or leads the sentence. Anything else is an
    # ordinary word and would be a misleading suggestion.
    leading = next((token for token in tokens if token in counts), None)
    ranked = [token for token in counts if counts[token] > 1 or token == leading]
    ranked.sort(key=lambda token: (-counts[token], token != leading, len(token), token))
    return ranked[:limit]


def _item_violations(
    feed: dict,
    source: dict,
    generated: dict,
    kind: str,
    index: int,
    *,
    allowed_number_source: str,
    language_level: int,
) -> list[str]:
    """Name every reason one generated item would be refused, on its own.

    Used by the ``revert_item`` policy so that one bad comment costs that one
    item instead of the whole batch. Checks that only make sense across the
    whole bundle (cross-item repetition) stay in the strict pass.
    """

    rows = generated.get(kind) or []
    source_rows = source.get(kind) or []
    if index >= len(rows) or index >= len(source_rows):
        return []
    row = rows[index]
    source_row = source_rows[index]
    if not isinstance(row, dict):
        return ["항목 형식"]
    reasons: list[str] = []
    variants = _name_variants(feed)

    if kind == "articles":
        primary = str(row.get("title") or "")
        source_primary = str(source_row.get("title") or "")
        texts = [primary, str(row.get("sub") or ""), *[str(value) for value in (row.get("body") or [])]]
    elif kind == "boards":
        primary = str(row.get("title") or "")
        source_primary = str(source_row.get("title") or "")
        texts = [primary, *[str(c.get("text") or "") for c in (row.get("comments") or []) if isinstance(c, dict)]]
    else:
        primary = str(row.get("text") or "")
        source_primary = str(source_row.get("text") or "")
        texts = [primary, *[str(c.get("text") or "") for c in (row.get("replies") or []) if isinstance(c, dict)]]

    # Never demand more focus than the built-in engine itself produced: a
    # league-wide board keeps its league-wide title.
    if variants and _focused(source_primary, variants) and not _focused(primary, variants):
        guessed = protagonist_spelling_candidates(primary, source_primary)
        hint = f" · 모델 표기 추정: {', '.join(guessed)}" if guessed else ""
        reasons.append(f"주인공 이름 누락(작성: {primary[:40]!r}){hint}")

    allowed = {_canonical_number(value) for value in _NUMBER_PATTERN.findall(allowed_number_source or "")}
    used = {_canonical_number(value) for text in texts for value in _NUMBER_PATTERN.findall(text)}
    if used - allowed:
        reasons.append(f"검증 자료에 없는 숫자 {sorted(used - allowed)[:4]}")

    ceiling = _LANGUAGE_CEILINGS.get(_language_level(language_level))
    if ceiling is not None and any(ceiling.search(text) for text in texts):
        reasons.append("언어 수위 초과")

    if kind == "boards":
        board = copy.deepcopy(((feed.get("boards") or [])[index:index + 1] or [{}])[0])
        board["title"] = primary
        board["comments"] = [
            {**(existing if isinstance(existing, dict) else {}), "text": str(written.get("text") or "")}
            for existing, written in zip(board.get("comments") or [], row.get("comments") or [])
            if isinstance(written, dict)
        ]
        if platform_register_violations({"boards": [board]}):
            reasons.append("플랫폼 말투 규칙")

    mini = {"media": [], "boards": [], "social": [], "editorial": feed.get("editorial") or {}}
    key = {"articles": "media", "boards": "boards", "social": "social"}[kind]
    mini[key] = [_item_preview(feed, kind, index, row)]
    audit = realism_gate.audit_feed(mini, names=_display_name_variants(feed), facts=((feed.get("editorial") or {}).get("facts") or []))
    # Only per-item prose findings are attributable to one item. Repetition
    # measures (mechanical suffixes, clones) compare items with each other and
    # stay with the strict whole-bundle pass.
    if audit.get("prose_violations"):
        reasons.append("현실성 검사")
    return reasons


def _item_preview(feed: dict, kind: str, index: int, row: dict) -> dict:
    """Return one feed item with the generated prose applied, for auditing."""

    key = {"articles": "media", "boards": "boards", "social": "social"}[kind]
    base = copy.deepcopy(((feed.get(key) or [])[index:index + 1] or [{}])[0])
    if kind == "articles":
        base["title"] = str(row.get("title") or "")
        base["sub"] = str(row.get("sub") or "")
        base["body"] = [str(value) for value in (row.get("body") or [])]
    elif kind == "boards":
        base["title"] = str(row.get("title") or "")
        base["comments"] = [
            {**(existing if isinstance(existing, dict) else {}), "text": str(written.get("text") or "")}
            for existing, written in zip(base.get("comments") or [], row.get("comments") or [])
            if isinstance(written, dict)
        ]
    else:
        base["text"] = str(row.get("text") or "")
        base["replies"] = [
            {**(existing if isinstance(existing, dict) else {}), "text": str(written.get("text") or "")}
            for existing, written in zip(base.get("replies") or [], row.get("replies") or [])
            if isinstance(written, dict)
        ]
    return base


def _source_field(source: dict, kind: str, index: int, field: str, position: int = -1) -> str:
    """Return the deterministic text behind one generated field, if any."""

    rows = source.get(kind) or []
    if index >= len(rows):
        return ""
    row = rows[index]
    if field in ("title", "sub", "text"):
        return str(row.get(field) or "")
    if field == "body":
        body = row.get("body") or []
        return str(body[position]) if 0 <= position < len(body) else ""
    nested = row.get("comments" if field == "comment" else "replies") or []
    if 0 <= position < len(nested):
        return str((nested[position] or {}).get("text") or "")
    return ""


def _wants_hangul(source: dict, kind: str, index: int, field: str, position: int = -1) -> bool:
    """Require Korean only where the built-in engine itself wrote Korean.

    Foreign outlets and translation surfaces are authored in their own language
    on purpose, so demanding Hangul in every field refused the app's own
    articles and made a reverted foreign item unpublishable.
    """

    original = _source_field(source, kind, index, field, position)
    return not original or bool(_HANGUL_PATTERN.search(original))


def _revert_violating_items(
    feed: dict,
    source: dict,
    generated: dict,
    *,
    allowed_number_source: str,
    language_level: int,
) -> tuple[dict, list[dict]]:
    """Replace only the offending items with their deterministic original."""

    repaired = copy.deepcopy(generated)
    reverted: list[dict] = []
    for kind in ("articles", "boards", "social"):
        rows = repaired.get(kind) or []
        source_rows = source.get(kind) or []
        for index in range(min(len(rows), len(source_rows))):
            reasons = _item_violations(
                feed,
                source,
                repaired,
                kind,
                index,
                allowed_number_source=allowed_number_source,
                language_level=language_level,
            )
            if reasons:
                rows[index] = copy.deepcopy(source_rows[index])
                reverted.append(
                    {"surface": kind, "id": str(source_rows[index].get("id") or ""), "reasons": reasons}
                )
    return repaired, reverted


def _validate_repetition(source: dict, result: dict) -> None:
    """Reject new repetition, not immutable text at its original positions.

    Scoped jobs carry untouched canonical surfaces through the final check.
    Repeated factual paragraphs in those surfaces are not model duplication.
    A copied phrase at any additional position still counts as new repetition.
    """
    source_texts, source_primary = _visible_expression_texts(source)
    result_texts, result_primary = _visible_expression_texts(result)

    def added_repeats(before, after, *, minimum=0, compact=False):
        def positions(values):
            found = {}
            for index, text in enumerate(values):
                value = re.sub(r"\s+", "", text) if compact else text
                if len(value) >= minimum:
                    found.setdefault(value, set()).add(index)
            return found
        originals = positions(before)
        return sum(
            max(0, len(indices) - max(1, len(indices & originals.get(value, set()))))
            for value, indices in positions(after).items() if len(indices) > 1
        )

    if added_repeats(source_primary, result_primary):
        raise ProviderFeedError("서로 다른 매체의 핵심 문장이 반복되어 저장하지 않았습니다.")
    if added_repeats(source_texts, result_texts, minimum=12, compact=True) > 1:
        raise ProviderFeedError("기사·댓글·SNS의 문장이 지나치게 반복되어 저장하지 않았습니다.")


def apply_expression(
    feed: dict,
    response_text: object,
    *,
    provider: str,
    model: str,
    provider_audit: dict | None = None,
    allowed_number_source: str = "",
    language_level: object = None,
    on_violation: str = "reject",
) -> dict:
    """Apply provider prose after proving that the deterministic plan is intact.

    ``on_violation='revert_item'`` keeps the rest of a batch when one item
    breaks a per-item rule: that item falls back to its deterministic text and
    the reasons are recorded in ``reaction_bundle.reverted_items``. The strict
    default is unchanged, and structural failures always reject the batch.
    """

    source = expression_contract(feed)
    generated = _parse_json(response_text)
    _validate_shape(source, generated)
    result = copy.deepcopy(feed)
    if language_level is not None:
        result["community_language_level"] = _language_level(language_level)
    effective_level = _language_level(result.get("community_language_level", 2))
    reverted_items: list[dict] = []
    if str(on_violation) == "revert_item":
        generated, reverted_items = _revert_violating_items(
            result,
            source,
            generated,
            allowed_number_source=allowed_number_source or json.dumps(source, ensure_ascii=False),
            language_level=effective_level,
        )
        total_items = sum(len(source.get(key) or []) for key in ("articles", "boards", "social"))
        if total_items and len(reverted_items) >= total_items:
            detail = "; ".join(
                f"{row.get('id')}: {', '.join(row.get('reasons') or [])}" for row in reverted_items[:4]
            )
            raise ProviderFeedError(f"모델이 쓴 항목이 모두 검사를 통과하지 못했습니다 ({detail}).")
    all_texts: list[str] = []
    primary_texts: list[str] = []
    variants = _name_variants(result)

    for index, (target, row) in enumerate(zip(result.get("media") or [], generated.get("articles") or [])):
        title = _text(row.get("title"), "기사 제목", MAX_TITLE_CHARS, require_hangul=_wants_hangul(source, "articles", index, "title"))
        sub = _text(row.get("sub"), "기사 부제", MAX_SUB_CHARS, require_hangul=_wants_hangul(source, "articles", index, "sub"))
        body = [
            _text(value, "기사 문단", MAX_PARAGRAPH_CHARS, require_hangul=_wants_hangul(source, "articles", index, "body", position))
            for position, value in enumerate(row.get("body"))
        ]
        source_title = str(((source.get("articles") or [])[index:index + 1] or [{}])[0].get("title") or "")
        if variants and _focused(source_title, variants) and not _focused(title, variants):
            raise ProviderFeedError("주인공이 빠진 기사 제목이 있어 저장하지 않았습니다.")
        target.update({"title": title, "sub": sub, "body": body})
        if isinstance(target.get("blocks"), list) and len(target["blocks"]) == len(body):
            for block, text in zip(target["blocks"], body):
                block["text"] = text
                block["origin"] = f"{provider}_expression"
        target["expression_renderer"] = provider
        target["expression_model"] = model
        all_texts.extend([title, sub, *body])
        primary_texts.append(title)

    for index, (target, row) in enumerate(zip(result.get("boards") or [], generated.get("boards") or [])):
        title = _text(row.get("title"), "게시판 제목", MAX_TITLE_CHARS, require_hangul=_wants_hangul(source, "boards", index, "title"))
        source_title = str(((source.get("boards") or [])[index:index + 1] or [{}])[0].get("title") or "")
        # A league-wide board keeps a league-wide headline: the rule follows
        # what the deterministic engine wrote for this very item.
        if variants and _focused(source_title, variants) and not _focused(title, variants):
            raise ProviderFeedError("주인공이 빠진 게시판 제목이 있어 저장하지 않았습니다.")
        target["title"] = title
        changed = {}
        for position, (comment, generated_comment) in enumerate(zip(target.get("comments") or [], row.get("comments") or [])):
            text = _text(generated_comment.get("text"), "게시판 댓글", MAX_COMMENT_CHARS, require_hangul=_wants_hangul(source, "boards", index, "comment", position))
            comment["text"] = text
            comment["origin"] = f"{provider}_expression"
            changed[str(comment.get("post_id") or "")] = text
            all_texts.append(text)
        for post in target.get("posts") or []:
            if str(post.get("post_id") or "") in changed:
                post["text"] = changed[str(post.get("post_id") or "")]
                post["origin"] = f"{provider}_expression"
        target["expression_renderer"] = provider
        target["expression_model"] = model
        all_texts.append(title)
        primary_texts.append(title)

    for index, (target, row) in enumerate(zip(result.get("social") or [], generated.get("social") or [])):
        text = _text(row.get("text"), "SNS 본문", MAX_SOCIAL_CHARS, require_hangul=_wants_hangul(source, "social", index, "text"))
        source_text = str(((source.get("social") or [])[index:index + 1] or [{}])[0].get("text") or "")
        if variants and _focused(source_text, variants) and not _focused(text, variants):
            raise ProviderFeedError("주인공이 빠진 SNS 본문이 있어 저장하지 않았습니다.")
        target["text"] = text
        for position, (reply, generated_reply) in enumerate(zip(target.get("replies") or [], row.get("replies") or [])):
            reply_text = _text(generated_reply.get("text"), "SNS 답글", MAX_COMMENT_CHARS, require_hangul=_wants_hangul(source, "social", index, "reply", position))
            reply["text"] = reply_text
            reply["origin"] = f"{provider}_expression"
            all_texts.append(reply_text)
        target["expression_renderer"] = provider
        target["expression_model"] = model
        all_texts.append(text)
        primary_texts.append(text)

    _validate_repetition(feed, result)
    _validate_numbers(all_texts, allowed_number_source or json.dumps(source, ensure_ascii=False))
    audit = realism_gate.audit_feed(
        result,
        names=_display_name_variants(result),
        facts=((result.get("editorial") or {}).get("facts") or []),
    )
    if audit.get("prose_violations") or audit.get("headline_clones") or audit.get("body_clones") or audit.get("mechanical_suffixes"):
        raise ProviderFeedError("모델 표현이 현실성·반복 검사 기준을 통과하지 못했습니다.")
    if platform_register_violations(result):
        raise ProviderFeedError("모델 표현이 디시·펨코·엠팍의 서로 다른 말투 기준을 지키지 못했습니다.")
    if community_language_violations(result):
        raise ProviderFeedError("모델 표현이 설정한 커뮤니티 언어 수위를 넘었습니다.")

    canonical = canonical_hash(feed)
    result["reaction_bundle"] = {
        **copy.deepcopy(feed.get("reaction_bundle") or {}),
        "schema_version": SCHEMA_VERSION,
        "contract_version": VERSION,
        "community_style_database": community_style_db.manifest(),
        "community_language_level": _language_level(result.get("community_language_level", 2)),
        "renderer": f"{provider}_expression",
        "provider": provider,
        "model": str(model),
        "counts": bundle_counts(result),
        "canonical_hash": canonical,
        "expression_hash": hashlib.sha256("\n".join(all_texts).encode("utf-8")).hexdigest(),
        "provider_audit": copy.deepcopy(provider_audit) if provider_audit else None,
        "reverted_items": copy.deepcopy(reverted_items),
        "fallback_chain": [],
    }
    return result


def finalize_batched_expression(
    feed: dict,
    aggregate_contract: dict,
    *,
    provider: str,
    model: str,
    batches: list[dict],
    provider_audit: dict | None = None,
    allowed_number_source: str = "",
    language_level: object = None,
) -> dict:
    """Validate the assembled full contract once more and publish it atomically."""

    result = apply_expression(
        feed,
        json.dumps(aggregate_contract, ensure_ascii=False, separators=(",", ":")),
        provider=provider,
        model=model,
        provider_audit=provider_audit,
        allowed_number_source=allowed_number_source,
        language_level=language_level,
    )
    metadata = result.setdefault("reaction_bundle", {})
    metadata["generation_mode"] = "sequential_batches"
    metadata["batch_count"] = len(batches or [])
    metadata["batch_plan_hash"] = batch_plan_hash(batches)
    descriptor = _source_descriptor(provider, model)
    metadata["surface_sources"] = {
        "articles": copy.deepcopy(descriptor),
        "community": copy.deepcopy(descriptor),
    }
    # The final validation pass must not erase the actual writer of each item.
    # Reverted prose is authored engine text, not a successful model rewrite.
    audits = {row.get("batch_id"): row for row in (provider_audit or {}).get("batches", [])}
    if audits:
        owners = {}
        for batch in batches:
            audit = audits.get(batch.get("batch_id"))
            if not audit:
                continue
            reverted = {str(row.get("id")) for row in audit.get("reverted_items", [])}
            for item_id in batch.get("item_ids", []):
                owners[(batch["kind"], str(item_id))] = (
                    "builtin" if str(item_id) in reverted else audit.get("provider", provider),
                    None if str(item_id) in reverted else audit.get("model", model),
                )
        contract = expression_contract(result)
        for kind, field in (("articles", "media"), ("boards", "boards"), ("social", "social")):
            for row, expression in zip(result.get(field, []), contract[kind]):
                owner = owners.get((kind, str(expression["id"])))
                if owner:
                    row["expression_renderer"], row["expression_model"] = owner
        metadata["reverted_items"] = [
            copy.deepcopy(row) for audit in audits.values() for row in audit.get("reverted_items", [])
        ]
        metadata.pop("surface_sources", None)
        metadata["surface_sources"] = _surface_sources(result)
    return result


def mark_fallback(feed: dict, attempts: list[dict], message: str) -> dict:
    """Return the untouched canonical bundle with bounded fallback evidence."""

    result = copy.deepcopy(feed)
    metadata = result.setdefault("reaction_bundle", {})
    metadata.update(
        {
            "schema_version": SCHEMA_VERSION,
            "contract_version": VERSION,
            "community_style_database": community_style_db.manifest(),
            "community_language_level": _language_level(result.get("community_language_level", 2)),
            "renderer": "deterministic",
            "provider": "builtin",
            "model": None,
            "counts": bundle_counts(result),
            "canonical_hash": canonical_hash(result),
            "surface_sources": {
                "articles": _source_descriptor("builtin"),
                "community": _source_descriptor("builtin"),
            },
            "fallback_chain": copy.deepcopy(attempts)[-3:],
            "fallback_message": str(message)[:300],
        }
    )
    return result
