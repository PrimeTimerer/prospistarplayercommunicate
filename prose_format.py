#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Readable Markdown normalization for generated Korean prose.

Models occasionally return every numbered scene on one physical line, for
example ``**1. heading** body **2. heading** body`` or
``## heading body ## next heading body``. Keep the wording intact, but turn
those explicit scene markers into real Markdown sections before new text is
persisted. The browser has the same compatibility transform so old stored
prose becomes readable without rewriting historical files.
"""

from __future__ import annotations

import re


_INLINE_NUMBERED_BOLD = re.compile(
    r"(?<=\S)[^\S\r\n]+(?=(?:\*\*|__)\d{1,2}[.)][^\S\r\n])"
)
_ADJACENT_NUMBERED_BOLD = re.compile(
    r"(?<=\S)(?=(?:\*\*|__)\d{1,2}[.)][^\S\r\n])"
)
_STAR_HEADING = re.compile(
    r"^\*\*(\d{1,2}[.)][^\r\n]*?)\*\*(?:[^\S\r\n]+(.*))?$"
)
_UNDER_HEADING = re.compile(
    r"^__(\d{1,2}[.)][^\r\n]*?)__(?:[^\S\r\n]+(.*))?$"
)
_INLINE_ATX = re.compile(r"([^#\s])[\t ]+(?=#{1,3}[\t ]+[^#\s])")
_ADJACENT_ATX = re.compile(r"([^#\s])(?=#{1,3}[\t ]+[^#\s])")
_ATX_HEADING = re.compile(r"^(#{1,3})[\t ]+(.+)$")
_BRACKETED_HEADING = re.compile(r"^\[([^\]\r\n]{1,80})\](?:[\t ]+(.*))?$")
_BOLD_HEADING = re.compile(r"^(?:\*\*|__)(.{1,80}?)(?:\*\*|__)(?:[\t ]+(.*))?$")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])[\t ]+|(?<=[.!?。！？][\"'”’])[\t ]+")


def _normalize_spacing(text: str) -> str:
    """Repair mechanical spacing, never guess Korean word boundaries.

    Leave links and literal code unchanged. In prose, remove invisible separators,
    fold typographic spaces and restore obvious sentence punctuation gaps.
    Mirror this boundary in the legacy browser renderer.
    """
    parts = re.split(r"(```[\s\S]*?```|`[^`\n]+`|https?://[^\s<>]+)", text)
    for index in range(0, len(parts), 2):
        value = re.sub(r"[\u00a0\u202f\u3000]", " ", parts[index])
        value = re.sub(r"[\u200b\ufeff]", "", value)
        value = re.sub(r"([가-힣][.!?。！？])(?=[가-힣])", r"\1 ", value)
        value = re.sub(r"([.!?。！？][\"'”’])(?=[가-힣])", r"\1 ", value)
        parts[index] = re.sub(r"[ \t]{2,}", " ", value)
    return "".join(parts)

# These are structural title cues, not a vocabulary generator. They are used
# only when a model has already emitted an ATX marker and then collapsed the
# title and a long Korean paragraph onto the same physical line.
_TITLE_TERMINALS = (
    "침묵", "시선", "메아리", "논쟁", "화면", "과열", "기대", "경계", "현상", "여운",
    "반응", "파장", "질문", "관심", "열기", "준비", "복선", "긴장", "관점", "평가",
    "기록", "이야기", "목소리", "분위기", "충격", "열광", "균열", "여파", "초점",
    "분석", "결론", "선택", "결심", "고민", "대화", "약속", "하루", "밤", "아침",
    "루틴", "훈련", "휴식", "승부", "장면", "순간", "변화", "역사", "무게", "중심",
)
_CONNECTIVE_ENDINGS = (
    "의", "와", "과", "을", "를", "로", "으로", "향한", "대한", "없는", "있는", "않는",
    "보이지", "뜨거운", "차가운", "고요한", "조용한", "번역된", "이어진", "남은", "새로운",
)
_BODY_START_ENDINGS = (
    "에서는", "에게서는", "으로부터", "에서", "에게", "에는", "에도", "부터", "까지", "은", "는", "이", "가",
)
_LONG_PARAGRAPH_MIN = 180
_LONG_PARAGRAPH_TARGET = 320


def _word_core(value: str) -> str:
    return value.strip("\"'‘’“”()[]{}<>《》〈〉「」『』,;:·…—-.!?。！？")


def _split_collapsed_atx_payload(payload: str) -> tuple[str, str]:
    """Separate a short heading from a collapsed body without rewriting it."""

    payload = payload.strip()
    explicit = _BRACKETED_HEADING.match(payload) or _BOLD_HEADING.match(payload)
    if explicit:
        return explicit.group(1).strip(), str(explicit.group(2) or "").strip()
    if len(payload) < 36 or not re.search(r"[.!?。！？]", payload):
        return payload, ""

    words = list(re.finditer(r"\S+", payload))
    if len(words) < 6:
        return payload, ""
    upper = min(8, len(words) - 3)
    boundary = None
    for index in range(1, upper):
        token = _word_core(words[index].group())
        if token and token.endswith(_TITLE_TERMINALS):
            boundary = words[index].end()
            break
    if boundary is None:
        for index in range(1, min(6, len(words) - 3)):
            token = _word_core(words[index].group())
            following = _word_core(words[index + 1].group())
            if token and not token.endswith(_CONNECTIVE_ENDINGS) and following.endswith(_BODY_START_ENDINGS):
                boundary = words[index].end()
                break
    if boundary is None:
        # The marker proves that a heading was intended. Four words is a
        # bounded last resort and preserves every original word in order.
        boundary = words[min(3, len(words) - 4)].end()
    return payload[:boundary].strip(), payload[boundary:].strip()


def _paragraph_chunks(line: str) -> list[str]:
    """Split only exceptionally long unbroken prose at sentence boundaries."""

    if len(line) < _LONG_PARAGRAPH_MIN or line.startswith((">", "- ", "* ")):
        return [line]
    sentences = [part.strip() for part in _SENTENCE_BREAK.split(line) if part.strip()]
    if len(sentences) < 3:
        return [line]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        projected = size + (1 if current else 0) + len(sentence)
        if current and (len(current) >= 2 or projected > _LONG_PARAGRAPH_TARGET):
            chunks.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += (1 if size else 0) + len(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def normalize_generated_markdown(value: object) -> str:
    """Return stable, readable Markdown without changing narrative wording."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _normalize_spacing(text)
    text = _INLINE_NUMBERED_BOLD.sub("\n\n", text)
    text = _ADJACENT_NUMBERED_BOLD.sub("\n\n", text)
    text = _INLINE_ATX.sub(r"\1\n\n", text)
    text = _ADJACENT_ATX.sub(r"\1\n\n", text)

    output: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        atx = _ATX_HEADING.match(line)
        if atx:
            title, remainder = _split_collapsed_atx_payload(atx.group(2))
            if output and output[-1] != "":
                output.append("")
            output.append(f"{atx.group(1)} {title}")
            if remainder:
                output.extend(("", remainder))
            continue
        match = _STAR_HEADING.match(line) or _UNDER_HEADING.match(line)
        if match:
            if output and output[-1] != "":
                output.append("")
            output.append(f"## {match.group(1).strip()}")
            remainder = str(match.group(2) or "").strip()
            if remainder:
                output.extend(("", remainder))
            continue
        for index, paragraph in enumerate(_paragraph_chunks(line)):
            if index:
                output.append("")
            output.append(paragraph)

    compact: list[str] = []
    for line in output:
        if not line and (not compact or compact[-1] == ""):
            continue
        compact.append(line)
    while compact and not compact[-1]:
        compact.pop()
    return "\n".join(compact).strip()
