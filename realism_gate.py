#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reality-preserving editorial gate (master plan sections 3.4, 4.1, 15, 19.1).

The gate is a pure text audit. It never rewrites prose. Callers decide whether
a violation blocks emission (new deterministic engine) or is only reported
(legacy generators, whose output is frozen as negative design evidence).

Layers:

- ``immersive``  article bodies, interviews, clubhouse scenes, translated
                 reactions, community posts. Grades, scores, and game-system
                 vocabulary are forbidden here.
- ``analysis``   header badges and analysis cards. A restrained grade badge
                 such as ``EX`` is allowed; game-system vocabulary is not.
- ``diagnostic`` developer views. Nothing is forbidden.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

GATE_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Forbidden public-language patterns
# --------------------------------------------------------------------------

# (code, compiled regex, human explanation). Every pattern is matched against
# NFKC-normalised text with collapsed whitespace.
_GRADE_LEAK = [
    ("grade_badge_in_prose", r"\bEX\s*(?:선수|급|등급|플레이어|타자|투수)", "EX 등급 호칭"),
    ("grade_badge_in_prose", r"\bEX\b(?=\s*(?:라는|이라는)?\s*(?:등급|평가|랭크))", "EX 등급 언급"),
    ("numeric_score_in_prose", r"\b\d{1,3}\s*/\s*100\b", "100점 만점 점수"),
    ("numeric_score_in_prose", r"(?:세계\s*)?주목도\s*(?:지수\s*)?\d{1,3}", "주목도 점수"),
    ("numeric_score_in_prose", r"(?:주목도|위상|반응)\s*(?:점수|스코어)", "내부 점수 어휘"),
]

_GAME_SYSTEM = [
    ("game_system_language", r"게임\s*밸런스", "게임 밸런스"),
    ("game_system_language", r"리그\s*밸런스\s*붕괴", "리그 밸런스 붕괴"),
    ("game_system_language", r"밸런스\s*붕괴", "밸런스 붕괴"),
    ("game_system_language", r"핵\s*쓰는", "핵 쓰는"),
    ("game_system_language", r"라스트\s*보스", "라스트보스"),
    ("game_system_language", r"이\s*세계의\s*데이터", "이 세계의 데이터"),
    ("game_system_language", r"게임\s*공식", "게임 공식"),
    ("game_system_language", r"플레이어가\s*조종", "플레이어가 조종"),
    ("game_system_language", r"게임\s*안에서는", "게임 안에서는"),
    ("game_system_language", r"컨트롤러|난이도\s*설정|패드\s*설정", "조작·설정 언급"),
    ("game_system_language", r"만화급|만화\s*같은\s*수치", "만화급 표현"),
    ("game_system_language", r"버그|오류\s*아니냐|불가능한\s*수치", "메타 논평"),
]

_INTERNAL_SIGNAL = [
    ("internal_signal_language", r"서사\s*지배력\s*지수", "서사 지배력 지수"),
    ("internal_signal_language", r"이벤트\s*포인트", "이벤트 포인트"),
    ("internal_signal_language", r"반응\s*예산", "반응 예산"),
    ("internal_signal_language", r"(?:세계\s*)?주목도(?:\s*단계|\s*모델|\s*지수)?", "주목도"),
    ("internal_signal_language", r"위상\s*파장|누적\s*위상|위상으로\s*주변", "위상 어휘"),
    ("internal_signal_language", r"관심\s*단계|\d\s*개\s*층위|파장\s*\d+", "단계·층위·파장 번호"),
    ("internal_signal_language", r"스타\s*레벨", "스타 레벨"),
    ("internal_signal_language", r"미션\s*포인트|미션\s*성공\s*수", "미션 포인트"),
]

_SHELL_NOTICE = [
    ("shell_notice_in_prose", r"세계선", "세계선(앱 어휘)"),
    ("shell_notice_in_prose", r"사용자가\s*만든", "사용자가 만든"),
    ("shell_notice_in_prose", r"가상\s*(?:기사|반응|세계)", "가상 기사·반응 고지"),
    ("shell_notice_in_prose", r"실제\s*보도(?:가|나)\s*아니", "실제 보도 아님 고지"),
    ("shell_notice_in_prose", r"검증\s*(?:세이브|기록\s*기반|스냅샷)", "검증 세이브 어휘"),
    ("shell_notice_in_prose", r"오프라인\s*(?:세계|서사|반응)", "오프라인 어휘"),
    ("shell_notice_in_prose", r"서사\s*근거는|기억\s*앵커", "서사 근거·기억 앵커"),
    ("shell_notice_in_prose", r"시뮬레이션|템플릿|프롬프트", "시뮬레이션·템플릿 어휘"),
]

# Claims that need a verified record fact behind them (claims ladder 15.2).
_RECORD_CLAIM = [
    ("unsupported_record_claim", r"(?:NPB|일본\s*프로야구|리그|구단|세계|사상|역대|프로야구)\s*(?:최초|신기록|첫)", "최초·신기록 주장"),
    ("unsupported_record_claim", r"(?:역사를|역사\s*다시)\s*(?:다시\s*)?(?:쓰|써)", "역사를 다시 쓴다"),
    ("unsupported_record_claim", r"신기록\s*(?:달성|수립|경신|페이스)", "신기록 달성"),
    ("unsupported_record_claim", r"(?:최연소|최고령|최단|최다)\s*(?:기록|달성|경신)", "최연소·최다 기록 주장"),
    ("unsupported_record_claim", r"기록이\s*의미를\s*잃", "기록이 의미를 잃었다"),
    ("unsupported_record_claim", r"스포츠의\s*상식을\s*다시", "상식을 다시 쓰다"),
]

_LAYER_PATTERNS = {
    "immersive": _GRADE_LEAK + _GAME_SYSTEM + _INTERNAL_SIGNAL + _SHELL_NOTICE + _RECORD_CLAIM,
    "analysis": _GAME_SYSTEM + _INTERNAL_SIGNAL[:3] + _RECORD_CLAIM,
    "diagnostic": [],
}

_COMPILED = {
    layer: [(code, re.compile(pattern), label) for code, pattern, label in rows]
    for layer, rows in _LAYER_PATTERNS.items()
}

RECORD_FACT_KINDS = frozenset(
    {
        "record_tied",
        "record_broken",
        "first_ever",
        "franchise_record",
        "league_record",
        "season_record",
        "career_record",
        "npb_reference_tied",
        "npb_reference_exceeded",
        "youngest",
        "oldest",
        "fastest",
    }
)

# Role words that may precede a quotation without naming a specific person.
_GENERIC_SPEAKER_WORDS = (
    "감독", "코치", "동료", "선배", "후배", "기자", "해설", "해설자", "팬", "관계자",
    "구단", "에이전트", "가족", "포수", "투수", "타자", "트레이너", "분석가", "데스크",
    "취재진", "베테랑", "신인", "라이벌", "상대", "본인", "선수", "주장", "단장",
)

_QUOTE_ATTRIBUTION = re.compile(
    r"(?P<name>[A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+)*|[가-힣]{2,4})\s*"
    r"(?:감독|코치|기자|해설위원|선수|단장)?\s*(?:은|는|이|가)?\s*[\"“「『‘']"
    r"(?P<quote>[^\"”」』’']{4,})[\"”」』’']\s*(?:라고|이라고|고)\s*(?:말했|밝혔|전했|덧붙였|강조했)"
)


@dataclass
class Violation:
    code: str
    label: str
    span: str
    message: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "span": self.span, "message": self.message}


@dataclass
class AuditResult:
    layer: str
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> list[str]:
        return [row.code for row in self.violations]

    def as_dict(self) -> dict:
        return {
            "gate_version": GATE_VERSION,
            "layer": self.layer,
            "ok": self.ok,
            "violations": [row.as_dict() for row in self.violations],
        }


def normalize_text(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", value).strip()


def _has_record_fact(facts) -> bool:
    for fact in facts or ():
        if isinstance(fact, dict):
            kind = str(fact.get("kind") or fact.get("fact_kind") or "")
            status = str(fact.get("status") or "")
            if kind in RECORD_FACT_KINDS or status in ("tied", "broken", "first_ever"):
                return True
        elif isinstance(fact, str) and fact in RECORD_FACT_KINDS:
            return True
    return False


def audit_public_prose(
    text: object,
    *,
    layer: str = "immersive",
    facts=(),
    personas=(),
    protagonist_names=(),
) -> AuditResult:
    """Audit one prose string.

    ``facts`` may contain dicts with ``kind``/``status`` describing verified
    record facts; ``personas`` lists fictional persona labels that may be
    quoted; ``protagonist_names`` lists the save protagonist's display forms.
    """
    layer = layer if layer in _COMPILED else "immersive"
    result = AuditResult(layer=layer)
    value = normalize_text(text)
    if not value:
        return result
    record_ok = _has_record_fact(facts)
    for code, pattern, label in _COMPILED[layer]:
        if code == "unsupported_record_claim" and record_ok:
            continue
        for match in pattern.finditer(value):
            result.violations.append(
                Violation(code=code, label=label, span=match.group(0), message=_message_for(code))
            )
    if layer != "diagnostic":
        allowed = {normalize_text(name) for name in (*personas, *protagonist_names) if name}
        for match in _QUOTE_ATTRIBUTION.finditer(value):
            name = match.group("name")
            if name in allowed or name in _GENERIC_SPEAKER_WORDS:
                continue
            if any(name in allowed_name or allowed_name in name for allowed_name in allowed):
                continue
            result.violations.append(
                Violation(
                    code="unregistered_quote_attribution",
                    label="등록되지 않은 화자 인용",
                    span=match.group(0)[:80],
                    message="인용문의 화자는 주인공 또는 등록된 창작 인물이어야 합니다.",
                )
            )
    return result


def _message_for(code: str) -> str:
    return {
        "grade_badge_in_prose": "등급 배지는 분석 헤더에서만 허용됩니다.",
        "numeric_score_in_prose": "내부 점수 대신 관찰 가능한 결과를 서술합니다.",
        "game_system_language": "게임 시스템 어휘는 현실 야구 문장으로 바꿉니다.",
        "internal_signal_language": "내부 신호 어휘는 개발 정보 보기에만 남깁니다.",
        "shell_notice_in_prose": "고지 문구는 셸에 한 번만 두고 본문에서는 뺍니다.",
        "unsupported_record_claim": "최초·신기록 주장은 검증 기록 사실 ID가 있어야 합니다.",
    }.get(code, "")


# --------------------------------------------------------------------------
# Skeleton and clone detection (screenshot failure signatures 4.1)
# --------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTED = re.compile(r"[\"“„「『‘'][^\"”」』’']*[\"”」』’']")
_TOKEN = re.compile(r"[A-Za-z]+|[가-힣]+|[ぁ-んァ-ン一-龥]+|[^\sA-Za-z가-힣ぁ-んァ-ン一-龥]")


def mask_names(text: str, names=()) -> str:
    value = normalize_text(text)
    for name in sorted({normalize_text(n) for n in names if n}, key=len, reverse=True):
        if not name:
            continue
        value = value.replace(name, "{P}")
        parts = name.split()
        if len(parts) > 1:
            for part in parts:
                if len(part) >= 2:
                    value = value.replace(part, "{P}")
    value = _QUOTED.sub("{Q}", value)
    value = _NUMBER.sub("{N}", value)
    return value


def skeleton_tokens(text: str, names=()) -> list[str]:
    return _TOKEN.findall(mask_names(text, names))


def headline_skeleton(title: str, names=()) -> str:
    """Stable signature of a headline with names, numbers, and quotes masked."""
    return " ".join(skeleton_tokens(title, names))


def skeleton_similarity(a: str, b: str, names=()) -> float:
    left = skeleton_tokens(a, names)
    right = skeleton_tokens(b, names)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(a=left, b=right).ratio()


def is_skeleton_clone(a: str, b: str, names=(), *, threshold: float = 0.8) -> bool:
    """True when two headlines share a skeleton with at most cosmetic changes.

    Two titles that are byte-identical after masking count as clones; so do
    titles whose masked token sequences differ in one substituted noun.
    """
    left = skeleton_tokens(a, names)
    right = skeleton_tokens(b, names)
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) == len(right):
        different = sum(1 for x, y in zip(left, right) if x != y)
        if different <= max(1, len(left) // 6):
            return True
    return difflib.SequenceMatcher(a=left, b=right).ratio() >= threshold


def find_skeleton_clones(titles: list[str], names=(), *, threshold: float = 0.8) -> list[tuple[int, int]]:
    pairs = []
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            if is_skeleton_clone(titles[i], titles[j], names, threshold=threshold):
                pairs.append((i, j))
    return pairs


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|(?<=다\.)\s*|(?<=요\.)\s*")


def sentences(text: object) -> list[str]:
    value = normalize_text(text)
    if not value:
        return []
    return [row.strip() for row in _SENTENCE_SPLIT.split(value) if row and row.strip()]


def body_signature(paragraphs, names=()) -> list[str]:
    rows = []
    for paragraph in paragraphs or ():
        for sentence in sentences(paragraph):
            rows.append(" ".join(skeleton_tokens(sentence, names)))
    return rows


def body_clone_ratio(a_paragraphs, b_paragraphs, names=()) -> float:
    left = body_signature(a_paragraphs, names)
    right = body_signature(b_paragraphs, names)
    if not left or not right:
        return 0.0
    shared = 0
    right_pool = list(right)
    for sentence in left:
        if sentence in right_pool:
            shared += 1
            right_pool.remove(sentence)
    return shared / max(len(left), len(right))


def is_body_clone(a_paragraphs, b_paragraphs, names=(), *, threshold: float = 0.6) -> bool:
    return body_clone_ratio(a_paragraphs, b_paragraphs, names) >= threshold


def find_body_clones(bodies: list[list[str]], names=(), *, threshold: float = 0.6) -> list[tuple[int, int]]:
    pairs = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            if is_body_clone(bodies[i], bodies[j], names, threshold=threshold):
                pairs.append((i, j))
    return pairs


_SUFFIX_SEPARATOR = re.compile(r"\s[·—-]\s")


def mechanical_suffixes(comments: list[str], *, minimum: int = 3) -> dict[str, int]:
    """Find a fixed trailing clause that is appended to many comments."""
    counts: dict[str, int] = {}
    for comment in comments:
        value = normalize_text(comment)
        parts = _SUFFIX_SEPARATOR.split(value)
        if len(parts) >= 2:
            suffix = parts[-1]
            if len(suffix) >= 6:
                counts[suffix] = counts.get(suffix, 0) + 1
    return {suffix: count for suffix, count in counts.items() if count >= minimum}


def ending_reuse(comments: list[str], *, tail: int = 5, minimum: int = 3) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comment in comments:
        value = normalize_text(comment)
        value = _SUFFIX_SEPARATOR.split(value)[0]
        if len(value) >= tail:
            key = value[-tail:]
            counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count >= minimum}


def opening_reuse(texts: list[str], *, head: int = 6, minimum: int = 3) -> dict[str, int]:
    counts: dict[str, int] = {}
    for text in texts:
        value = normalize_text(text)
        if len(value) >= head:
            key = value[:head]
            counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count >= minimum}


def char_ngrams(text: object, n: int = 8) -> set[str]:
    value = re.sub(r"\s+", "", normalize_text(text))
    return {value[i : i + n] for i in range(0, max(0, len(value) - n + 1))}


def ngram_overlap(a: object, b: object, n: int = 8) -> float:
    left = char_ngrams(a, n)
    right = char_ngrams(b, n)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


# --------------------------------------------------------------------------
# Feed-level audit used by tests and by the new engine's self check
# --------------------------------------------------------------------------


def audit_feed(feed: dict, *, names=(), facts=(), personas=()) -> dict:
    """Audit a legacy feed or a reaction bundle (media + boards + social)."""
    media = list((feed or {}).get("media") or [])
    boards = list((feed or {}).get("boards") or [])
    social = list((feed or {}).get("social") or [])
    prose_violations: list[dict] = []
    for article in media:
        for piece in [article.get("title"), article.get("sub"), *(article.get("body") or [])]:
            audit = audit_public_prose(piece, facts=facts, personas=personas, protagonist_names=names)
            for row in audit.violations:
                prose_violations.append({**row.as_dict(), "where": f"media:{article.get('outlet')}"})
    comments: list[str] = []
    for board in boards:
        audit = audit_public_prose(board.get("title"), facts=facts, personas=personas, protagonist_names=names)
        for row in audit.violations:
            prose_violations.append({**row.as_dict(), "where": f"board:{board.get('board')}:title"})
        for comment in board.get("comments") or []:
            text = comment.get("text") if isinstance(comment, dict) else str(comment)
            comments.append(text or "")
            audit = audit_public_prose(text, facts=facts, personas=personas, protagonist_names=names)
            for row in audit.violations:
                prose_violations.append({**row.as_dict(), "where": f"board:{board.get('board')}"})
    social_texts: list[str] = []
    for post in social:
        text = str(post.get("text") or "")
        social_texts.append(text)
        audit = audit_public_prose(text, facts=facts, personas=personas, protagonist_names=names)
        for row in audit.violations:
            prose_violations.append({**row.as_dict(), "where": f"social:{post.get('platform')}"})
        for reply in post.get("replies") or []:
            reply_text = reply.get("text") if isinstance(reply, dict) else str(reply)
            social_texts.append(reply_text or "")
            audit = audit_public_prose(reply_text, facts=facts, personas=personas, protagonist_names=names)
            for row in audit.violations:
                prose_violations.append({**row.as_dict(), "where": f"social:{post.get('platform')}:reply"})
    titles = [str(article.get("title") or "") for article in media]
    bodies = [list(article.get("body") or []) for article in media]
    return {
        "gate_version": GATE_VERSION,
        "prose_violations": prose_violations,
        "headline_clones": find_skeleton_clones(titles, names),
        "body_clones": find_body_clones(bodies, names),
        "mechanical_suffixes": mechanical_suffixes(comments),
        "ending_reuse": ending_reuse(comments),
        "comment_count": len(comments),
        "article_count": len(media),
        "social_post_count": len(social),
        "social_reply_count": max(0, len(social_texts) - len(social)),
    }


def feed_is_clean(report: dict) -> bool:
    return not (
        report.get("prose_violations")
        or report.get("headline_clones")
        or report.get("body_clones")
        or report.get("mechanical_suffixes")
    )
