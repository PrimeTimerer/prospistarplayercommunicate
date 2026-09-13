"""Bounded Korean clause/negation analysis for confirmed activity dialogue.

This pure parser does not schedule multiple actions or infer arbitrary grammar.
Uncertain, reported and conflicting intentions remain clarification-only.
"""

from __future__ import annotations

import re
import unicodedata

VERSION = "1.0.0"
MAX_CLAUSES = 24
PATTERNS = (
    ("share", r"(?:공개|기자|인터뷰|SNS|팬들).*(?:말|알리|올리|공개|전하|이야기)|공개하|공개해"),
    ("change", r"(?:약속|계획).*(?:취소|바꾸|바꿀|변경|철회|못\s*지켜)|번복"),
    ("fulfill", r"약속.*(?:지켰|이행했|이행하지|지키지)|(?:사줬|사\s*줬|갚았|다녀왔)"),
    ("promise", r"약속|다음.*(?:사줄|사\s*줄|같이|함께|하자|가자|먹자|보자)|이번.*(?:사줄|사\s*줄)"),
    ("close", r"(?:대화|이야기|오늘).*(?:끝내|마무리|여기까지)|잘\s*가"),
    ("pause", r"잠시.*(?:멈추|쉬|중단)|나중에\s*(?:하자|얘기|이야기)"),
    ("reconcile", r"미안|사과|화해|내가\s*잘못"),
    ("refuse", r"싫어|거절|안\s*할래|안\s*가|그만해|하지\s*마"),
    ("disagree", r"동의.*(?:못|안|않)|반대|생각.*다르|화났|화가\s*나|서운"),
    ("confide", r"솔직|속마음|불안|무서|걱정|외롭|힘들|두렵|고민"),
    ("joke", r"농담|장난|ㅋㅋ|ㅎㅎ|웃기|웃긴|웃겨|개그|햄버거.*(?:값|계산|삼진)"),
    ("agree", r"^(?:응|그래|좋아|알겠|동의|그러자|좋지)|좋은\s*생각"),
    ("resume", r"다시.*(?:시작|얘기|이야기|만나)|이어(?:가|서|보)"),
    ("continue", r"더\s*(?:말|얘기|이야기|듣)|왜|어떻|너는|그건|그러면|그럼|그래서"),
)

_QUOTES = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|\'[^\'\n]*\'|`[^`\n]*`')
_NEGATIVE = re.compile(
    r"(?:하)?지(?:는|도)?\s*(?:말|마(?:라|세요)?|않|못)|(?<![가-힣])(?:안|못)\s*(?:하|해|할|했|가|갈|말|공개|지켜|지켰|지킬|사\s*줄)|"
    r"(?:농담|장난|약속|사과|화해|동의|반대|공개|이행|취소|변경|번복|철회|대화)(?:은|는|을|를)?(?:안|못)(?:하|해|할|했)|"
    r"(?:할|하려는)\s*(?:생각|뜻|마음|의향)(?:이|은|도)?\s*없|(?:아니야|아니다|아니고|아니라)|말고|원치\s*않|금지|금물|(?:면|서는)\s*안\s*(?:돼|되)"
)
_SPLIT = re.compile(
    r"[.!?;,\n]+|(?:하지만|그러나|그런데|그래도|대신|그리고|그러면서|그렇지만)\s*|"
    r"(?:말고|아니라|아니고|않고|않지만|지만)|"
    r"하고\s*(?=(?:농담|장난|속마음|사과|화해|약속|공개|걱정|대화|잠시))"
)
_PROP = re.compile(r"소재|별명|농담으로|반복|이걸\s*기억")
_PRIVATE = re.compile(r"비공개|비밀|우리끼리|우리\s*둘|둘만|안에서만")
_PUBLIC = re.compile(r"공개|기자|인터뷰|SNS|팬들|밖으로|언론", re.I)
_RECALL = re.compile(r"(?:무슨|어떤).*(?:약속|이야기)|(?:약속|얘기|이야기).*(?:뭐였|기억나|기억해|알려)")
_PAST_QUESTION = re.compile(r"(?:약속|사줬|사\s*줬|갚았).*(?:지켰|취소했|이행했|사줬|사\s*줬|갚았)?.*\?\s*$")
_REPORTED = re.compile(r"(?:라고|다고|자고|냐고)\s*(?:했|하던|들었|말했|적혀|써\s*있|전했)")
_UNCERTAIN = re.compile(r"만약|가정|했더라면|한다면|한다고\s*치면|할\s*경우|할까\s*말까|할지\s*말지|"
                        r"(?:취소|변경|공개|이행|화해).{0,8}면|(?:취소|공개|이행|화해).{0,6}할까\??\s*$")
_DOUBLE_NEGATIVE = re.compile(r"(?:않|못|안\s*하|말)[^.!?]{0,22}(?:아니|않|없)|아니[^.!?]{0,18}않")
_CONDITION = re.compile(r"^다음(?:에)?[^.!?]{0,35}(?:홈런|경기|등판|승리|우승|안타)[^.!?]{0,20}?(?:면|때)\s*")
_KEEP = re.compile(r"(?:약속|계획).*(?:그대로|유지|변경\s*없이)")


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    return re.sub(r"[ \t\f\v]+", " ", re.sub(r"[\u200b-\u200d\ufeff]", "", value)).strip()


def _clauses(text: str, masked: str) -> list[dict]:
    rows, start = [], 0
    for match in _SPLIT.finditer(masked):
        # A negative ending belongs to the clause it excludes, not its neighbour.
        ending = match.group().strip()
        end = match.end() if ending in ("말고", "아니라", "아니고", "않고", "않지만", "지만", "하고") else match.start()
        if masked[start:end].strip():
            rows.append({"text": text[start:end].strip(), "masked": masked[start:end].strip(), "span": [start, end]})
        start = match.end()
    if masked[start:].strip() or (not rows and text[start:].strip()):
        rows.append({"text": text[start:].strip(), "masked": masked[start:].strip(), "span": [start, len(text)]})
    return rows


def _negated(clause: str, beat: str) -> bool:
    probe = _CONDITION.sub("", clause) if beat == "promise" else clause
    if beat == "disagree" and re.search(r"동의.*(?:못|안|않)", probe) and not _DOUBLE_NEGATIVE.search(probe):
        return False
    if beat == "refuse" and re.fullmatch(r"(?:그건\s*)?(?:싫어.*|거절할래|안\s*할래|안\s*가|그만해|하지\s*마)", probe):
        return False
    return bool(_NEGATIVE.search(probe))


def analyze(text: str) -> dict:
    """Return one explicit beat or a non-mutating explanation; never a batch."""
    text = normalize(text)
    masked = _QUOTES.sub(lambda match: " " * len(match.group()), text)
    rows = _clauses(text, masked)
    result = {"version": VERSION, "text": text, "status": "unknown", "beat": None,
              "selected_text": "", "clauses": [], "candidates": [], "excluded": [],
              "keep_private": False, "reasons": [], "allow_literal_quote": False}
    if len(rows) > MAX_CLAUSES:
        result.update(status="clarify", reasons=["too_many_clauses"])
        return result
    if any(mark in masked for mark in ('"', '“', '”', '「', '」', '‘', '’', "'", "`")):
        result.update(status="clarify", reasons=["unclosed_quote"])
        return result
    candidates = []
    for index, row in enumerate(rows):
        probe = row["masked"]
        neg = bool(_NEGATIVE.search(probe))
        private = bool((_PUBLIC.search(probe) and neg) or (_PRIVATE.search(probe) and not neg))
        result["keep_private"] |= private
        risky = "reported_speech" if _REPORTED.search(probe) else "hypothetical" if _UNCERTAIN.search(probe) else "double_negative" if _DOUBLE_NEGATIVE.search(probe) else None
        beats = [beat for beat, pattern in PATTERNS if re.search(pattern, probe, re.I)]
        query_text = probe + ("?" if text[row["span"][1]:].startswith("?") else "")
        recall = bool(_RECALL.search(probe)) or bool(_PAST_QUESTION.search(query_text) and re.search(r"지켰|취소했|이행했|사줬|사\s*줬|갚았", probe))
        if _PROP.search(probe):
            beats = ["prop"]  # Explicit material commands keep a separate owner.
        elif recall:
            beats = ["recall"]
        elif "share" in beats:
            beats = ["share"]  # A quoted public claim is not promise fulfillment.
        elif any(beat in beats for beat in ("change", "fulfill")):
            beats = [beat for beat in beats if beat not in ("promise", "continue")]
        elif "disagree" in beats:
            beats = [beat for beat in beats if beat != "agree"]
        if len(beats) > 1:
            beats = [beat for beat in beats if beat not in ("continue", "agree")]
        active, denied = [], []
        for beat in beats:
            reason = risky or ("preserve_existing" if beat == "promise" and _KEEP.search(probe) and not neg else "negated" if _negated(probe, beat) else None)
            # A direct, sole request to stop joking retains the old boundary beat.
            if beat == "refuse" and re.search(r"(?:농담|장난).*하지\s*마(?:라|세요)?\s*$", probe) and not risky:
                reason = None
            if reason:
                denied.append(beat)
                result["excluded"].append({"beat": beat, "clause": index, "reason": reason})
            else:
                active.append(beat)
                candidates.append({"beat": beat, "clause": index, "text": row["text"]})
        if risky:
            result["reasons"].append(risky)
        if neg and not beats:
            result["excluded"].append({"beat": None, "clause": index, "reason": "unresolved_negation"})
        result["clauses"].append({"text": row["text"], "span": row["span"], "active": active, "excluded": denied})

    if any(row["beat"] != "continue" for row in candidates):
        candidates = [row for row in candidates if row["beat"] != "continue"]
    if any(row["beat"] != "agree" for row in candidates):
        candidates = [row for row in candidates if row["beat"] != "agree" or not re.fullmatch(r"(?:응|그래|좋아|알겠어|그러자|좋지)", row["text"])]
    unique = list(dict.fromkeys(row["beat"] for row in candidates))
    result["candidates"] = unique
    if "share" in unique and result["keep_private"]:
        result["reasons"].append("conflicting_disclosure")
    denied = {row["beat"] for row in result["excluded"] if row["reason"] == "negated"}
    if denied.intersection(unique):
        result["reasons"].append("conflicting_intentions")
    if len(unique) > 1:
        result["reasons"].append("multiple_actions")
    if result["reasons"]:
        result["status"] = "clarify"
    elif unique == ["recall"]:
        result["status"] = "recall"
    elif len(unique) == 1:
        beat = unique[0]
        selected = [row["text"] for row in candidates if row["beat"] == beat]
        result.update(status="resolved", beat=beat, selected_text=". ".join(selected),
                      allow_literal_quote=not result["excluded"] and bool(_QUOTES.search(text)))
    elif result["keep_private"]:
        result["status"] = "private"
    elif result["excluded"] and all(row["reason"] == "preserve_existing" for row in result["excluded"]):
        result["status"] = "keep"
    elif result["excluded"] or _QUOTES.search(text):
        result["status"] = "clarify"
    return result


def prop_needs_review(text: str) -> bool:
    result = analyze(text)
    # A single explicit material-privacy restriction belongs to the existing
    # prop owner; negating publication is its affirmative protective command.
    if (len(result["clauses"]) == 1 and not result["reasons"] and _PROP.search(result["text"])
            and re.search(r"공개하지\s*(?:마|말자|말아)\s*$", result["text"])):
        return False
    return result["status"] != "resolved" or result["beat"] != "prop" or bool(result["excluded"]) or len(result["clauses"]) != 1


def clarification(result: dict, labels: dict) -> str:
    if result["status"] == "keep":
        return "기록된 약속을 그대로 유지합니다. 새 약속으로 덮어쓰거나 이행·취소로 바꾸지 않습니다."
    if result["keep_private"] and not result["candidates"] and not result["reasons"]:
        return "이 만남의 비공개·팀 내부 범위를 유지합니다. 기사나 팬 반응에 사적인 대화를 알리지 않습니다."
    if "multiple_actions" in result["reasons"]:
        names = [{"recall": "기억 조회", "prop": "소재 관리", **labels}.get(beat, beat) for beat in result["candidates"]]
        return f"{' · '.join(names)} 뜻이 함께 있어요. 먼저 진행할 한 가지를 알려 주세요. 아직 어느 장면도 확정하지 않았습니다."
    if result["keep_private"]:
        return "공개 범위에 서로 다른 뜻이 있거나 해석이 불분명해요. 비공개·팀 내부 범위를 유지하며, 공개 발언은 만들지 않습니다. 원하는 뜻을 한 번 더 알려 주세요."
    return "부정하거나 인용·가정한 말을 실행할 행동으로 정하지 않았어요. 지금 이 만남에서 실제로 제안할 일을 한 가지로 알려 주세요. 기록된 약속과 관계는 그대로입니다."
