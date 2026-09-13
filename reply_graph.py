#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Community reply graphs and press/foreign reaction bundles (8.10, 14.1, 15.3).

Every post declares a thread claim, its parent, the claim it addresses, a
stance, fact ids, and a protagonist relation. A reply must answer,
question, qualify, joke about, correct, or explicitly redirect a specific
parent claim; three unrelated stock lines are not a conversation. Foreign
lines keep the original language and carry a Korean translation.
"""

from __future__ import annotations

import hashlib
import random

import dominant_event
import personas
import realism_gate
import template_store as ts

STANCES = ("support", "oppose", "qualify", "ask", "joke", "correct", "redirect")

# Which reply stances may follow a given parent stance (15.3 realistic disagreement).
FOLLOW = {
    "support": ["qualify", "oppose", "ask", "joke", "redirect", "support"],
    "qualify": ["support", "oppose", "ask", "correct", "redirect"],
    "oppose": ["qualify", "support", "correct", "joke"],
    "ask": ["support", "qualify", "correct"],
    "joke": ["support", "joke", "redirect", "qualify"],
    "correct": ["qualify", "support", "ask"],
    "redirect": ["support", "qualify", "ask"],
}

CIRCLE_BY_VISIBILITY = {
    "private": [],
    "clubhouse": [],
    "club": ["club", "local"],
    "local": ["local", "club"],
    "national": ["local", "national", "historical"],
    "international": ["local", "national", "historical", "international"],
}


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16))


def _stable(*parts) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]


def _context_for(base: dict, persona_id: str | None = None) -> dict:
    context = dict(base)
    context["preferred_personas"] = [persona_id] if persona_id else []
    return context


def _matches_domain(row: dict, event_type: str | None) -> bool:
    targets = row.get("responds_to") or ["*"]
    if "*" in targets:
        return True
    if not event_type:
        return False
    return any(event_type.startswith(target) or target.startswith(event_type) for target in targets)


def _pool(store: ts.TemplateStore, *, locale: str, role: str, event_type: str | None, context: dict, circles: list[str], stance: str | None = None) -> list[dict]:
    rows = []
    for row in store.candidates(kind="reaction", locale=locale, context=context, limit=500):
        if row.get("role") != role:
            continue
        if stance and row.get("stance") != stance:
            continue
        if not _matches_domain(row, event_type):
            continue
        persona = personas.PERSONAS.get(str(row.get("persona")), {})
        if circles and persona.get("circle") not in circles and persona.get("circle") != "international":
            continue
        rows.append(row)
    return rows


def build_thread(
    store: ts.TemplateStore,
    *,
    seed: str,
    event_type: str | None,
    headline_fact: str,
    visibility: str,
    context: dict,
    locale: str = "ko",
    reply_count: int = 4,
    guards: list[dict] | None = None,
    names=(),
    protagonist_id: str = "",
) -> dict | None:
    """One community thread as a reply graph. Returns None when nothing is eligible."""
    circles = CIRCLE_BY_VISIBILITY.get(visibility, ["local"])
    if not circles and visibility in ("private", "clubhouse"):
        return None
    rng = _rng(seed)
    base = dict(context)
    base.setdefault("slots", {})
    base["slots"] = dict(base["slots"], headline_fact=headline_fact)
    base["visibility"] = visibility
    openers = _pool(store, locale=locale, role="opener", event_type=event_type, context=base, circles=circles)
    if not openers:
        return None
    opener_choice = store.choose(openers, _context_for(base), seed=f"{seed}:opener")
    opener = store.realize(opener_choice, base)
    posts: list[dict] = []
    used_personas = {opener["persona"]}
    thread_id = _stable(seed, "thread")
    opener_id = _stable(seed, "post", 0)
    posts.append(
        {
            "post_id": opener_id,
            "thread_claim_id": _stable(seed, "claim", 0),
            "parent_post_id": None,
            "addressed_claim_id": None,
            "stance": opener_choice["template"].get("stance") or "support",
            "persona": opener["persona"],
            "author": personas.display_label(opener["persona"]),
            "text": opener["text"],
            "translation_ko": opener.get("translation_ko"),
            "locale": opener["locale"],
            "fact_ids": list(base.get("fact_ids") or []),
            "protagonist_relation": "subject",
            "function": "opens with the verified headline fact",
            "template_id": opener["template_id"],
        }
    )
    stance_sequence = [posts[0]["stance"]]
    parent = posts[0]
    for index in range(1, reply_count + 1):
        allowed = FOLLOW.get(parent["stance"], list(STANCES))
        if len(stance_sequence) >= 2 and stance_sequence[-1] == stance_sequence[-2]:
            allowed = [stance for stance in allowed if stance != stance_sequence[-1]] or allowed
        chosen_reply = None
        for stance in rng.sample(allowed, len(allowed)):
            replies = [
                row
                for row in _pool(store, locale=locale, role="reply", event_type=event_type, context=base, circles=circles, stance=stance)
                if row.get("persona") not in used_personas
            ]
            if not replies:
                continue
            choice = store.choose(replies, _context_for(base), seed=f"{seed}:reply:{index}:{stance}")
            realized = store.realize(choice, base)
            if guards and dominant_event.guard_violations(realized["text"], guards):
                continue
            chosen_reply = (choice, realized, stance)
            break
        if chosen_reply is None:
            break
        choice, realized, stance = chosen_reply
        used_personas.add(realized["persona"])
        post_id = _stable(seed, "post", index)
        posts.append(
            {
                "post_id": post_id,
                "thread_claim_id": _stable(seed, "claim", index),
                "parent_post_id": parent["post_id"],
                "addressed_claim_id": parent["thread_claim_id"],
                "stance": stance,
                "persona": realized["persona"],
                "author": personas.display_label(realized["persona"]),
                "text": realized["text"],
                "translation_ko": realized.get("translation_ko"),
                "locale": realized["locale"],
                "fact_ids": list(base.get("fact_ids") or []),
                "protagonist_relation": "subject" if stance != "redirect" else "teammate_context",
                "function": f"{stance} of the parent claim",
                "template_id": realized["template_id"],
            }
        )
        stance_sequence.append(stance)
        # Alternate between deepening the last reply and returning to the opener
        # so the graph branches briefly without losing the protagonist claim.
        parent = posts[-1] if index % 2 == 1 else posts[0]
    if len(posts) < 2:
        return None
    return {
        "thread_id": thread_id,
        "board": "홈 팬 게시판" if locale == "ko" else personas.display_label(posts[0]["persona"]),
        "code": f"reply-graph-{locale}",
        "title": headline_fact,
        "locale": locale,
        "visibility": visibility,
        "posts": posts,
        "comments": [
            {
                "author": row["author"],
                "text": row["text"] if row["locale"] == "ko" or not row.get("translation_ko") else f"{row['text']}\n[한국어 번역] {row['translation_ko']}",
                "stance": row["stance"],
                "parent_post_id": row["parent_post_id"],
                "post_id": row["post_id"],
            }
            for row in posts
        ],
        "stance_sequence": stance_sequence,
        "signature": realism_gate.headline_skeleton(headline_fact, names),
        "provenance": "fictional_intervention" if context.get("user_authored") else "generated_fiction",
        "creative_marker": "창작 반응",
    }


def build_press(
    store: ts.TemplateStore,
    *,
    seed: str,
    event_type: str | None,
    headline_fact: str,
    visibility: str,
    context: dict,
    count: int = 2,
    guards: list[dict] | None = None,
    names=(),
) -> list[dict]:
    """Short press notes from distinct fictional institutions (no body cloning)."""
    circles = CIRCLE_BY_VISIBILITY.get(visibility, ["local"])
    if not circles:
        return []
    base = dict(context)
    base["slots"] = dict(base.get("slots") or {}, headline_fact=headline_fact)
    base["visibility"] = visibility
    openers = [
        row
        for row in _pool(store, locale="ko", role="opener", event_type=event_type, context=base, circles=circles)
        if personas.PERSONAS.get(str(row.get("persona")), {}).get("role") in ("press", "broadcast", "analyst")
    ]
    articles: list[dict] = []
    used = set()
    skeletons: list[str] = []
    for index in range(count * 3):
        pool = [row for row in openers if row.get("persona") not in used]
        if not pool or len(articles) >= count:
            break
        choice = store.choose(pool, _context_for(base), seed=f"{seed}:press:{index}")
        realized = store.realize(choice, base)
        if guards and dominant_event.guard_violations(realized["text"], guards):
            used.add(realized["persona"])
            continue
        persona = personas.PERSONAS.get(realized["persona"], {})
        title = f"{persona.get('institution') or personas.label(realized['persona'])} — {headline_fact}"
        skeleton = realism_gate.headline_skeleton(realized["text"], names)
        if any(realism_gate.is_skeleton_clone(realized["text"], previous, names) for previous in skeletons):
            used.add(realized["persona"])
            continue
        skeletons.append(realized["text"])
        used.add(realized["persona"])
        articles.append(
            {
                "outlet": persona.get("institution") or personas.label(realized["persona"]),
                "persona": realized["persona"],
                "flag": "📝",
                "lang": "ko",
                "title": title,
                "sub": f"{personas.label(realized['persona'])} · 창작 반응",
                "body": [realized["text"]],
                "stance": choice["template"].get("stance"),
                "template_id": realized["template_id"],
                "skeleton": skeleton,
                "provenance": "generated_fiction",
            }
        )
    return articles


def build_foreign(
    store: ts.TemplateStore,
    *,
    seed: str,
    event_type: str | None,
    headline_fact: str,
    visibility: str,
    context: dict,
    count: int = 2,
    guards: list[dict] | None = None,
    locale: str = "ja",
) -> list[dict]:
    """Original-language lines with Korean translations (14.1 display contract)."""
    if visibility not in ("national", "international"):
        return []
    base = dict(context)
    base["slots"] = dict(base.get("slots") or {}, headline_fact=headline_fact)
    base["visibility"] = visibility
    base["locale"] = locale
    pool = _pool(store, locale=locale, role="opener", event_type=event_type, context=base, circles=["international"])
    pool += _pool(store, locale=locale, role="reply", event_type=event_type, context=base, circles=["international"])
    rows: list[dict] = []
    used = set()
    for index in range(count * 3):
        candidates = [row for row in pool if row.get("persona") not in used]
        if not candidates or len(rows) >= count:
            break
        choice = store.choose(candidates, _context_for(base), seed=f"{seed}:foreign:{locale}:{index}")
        realized = store.realize(choice, base)
        used.add(realized["persona"])
        if guards and dominant_event.guard_violations(realized.get("translation_ko") or "", guards):
            continue
        rows.append(
            {
                "persona": realized["persona"],
                "channel": personas.display_label(realized["persona"]),
                "locale": locale,
                "original": realized["text"],
                "translation_ko": realized.get("translation_ko"),
                "note_ko": realized.get("note_ko"),
                "stance": choice["template"].get("stance"),
                "template_id": realized["template_id"],
                "provenance": "generated_fiction",
                "creative_marker": "창작·번역",
            }
        )
    return rows
