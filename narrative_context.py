"""Bounded discourse analysis for fictional prose, not a general truth oracle.

Keep layout, sentence boundaries, coordinated quantities and their predicates
together. Only engine-supplied count proofs authorize prospective quantities.
Nothing here changes records, starts a model, or treats generated text as evidence.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Statement:
    start: int
    end: int
    paragraph: int
    text: str
    previous: str = ""
    following: str = ""


def analysis_text(text: str) -> str:
    # Typography is projected only for validation, not spelling correction.
    value = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[*_`\u200b\ufeff]", "", value)


def statements(text: str):
    """Keep soft wraps; isolate paragraphs, list speakers and sentence clauses."""
    blocks = re.split(r"(\n[ \t]*\n|\n(?=[ \t]*(?:#{1,3}\s|[-*>]\s|ㄴ\s)))", text)
    offset = 0
    paragraph = 0
    for index, block in enumerate(blocks):
        if index % 2:
            offset += len(block)
            paragraph += 1
            continue
        spans = []
        start = 0
        # A decimal, URL, nickname tag or date is not a sentence boundary.
        for boundary in re.finditer(r"[.!?。！？]+(?=[\s\"'”’]|[가-힣]|$)|[;；]|하지만|그러나|그런데|반면", block):
            left, right = boundary.start(), boundary.end()
            if boundary[0] == "." and left and right < len(block) and block[left - 1].isdigit() and block[right].isdigit():
                continue
            spans.append((start, right))
            start = right
        if start < len(block):
            spans.append((start, len(block)))
        for number, (start, end) in enumerate(spans):
            yield Statement(offset + start, offset + end, paragraph, block[start:end],
                            block[slice(*spans[number - 1])] if number else "",
                            block[slice(*spans[number + 1])] if number + 1 < len(spans) else "")
        offset += len(block)


def target_mentions(text: str, row: dict):
    token = "(?:" + str(row["target"]) + "|" + re.escape(f"{row['target']:,}") + ")"
    label = r"\s*".join(map(re.escape, row["label"]))
    return re.finditer(
        r"(?<![A-Za-z0-9.,+-])" + token + r"\s*(?:번째\s*)?" + label
        + r"|(?<![가-힣A-Za-z])" + label + r"\s*" + token
        + r"(?![A-Za-z0-9.,+-]|\s*(?:명|원|달러|미터|km|분|시간|이닝))\s*(?:개)?", text)


_LINK = re.compile(r"^\s*(?:(?:과|와|및|그리고|랑|하고|·|,|/)\s*)(?:(?:시즌|한\s*시즌)\s*)?$")
_GOAL_PREFIX = re.compile(r"(?:다음|새로운|다가올|앞으로의)\s*(?:목표|이정표|고지|마일스톤)|(?:목표|도전)(?:는|은|인|으로)")
_FUTURE = re.compile(
    r"목표|도전|카운트다운|예상|전망|가능성|가정|눈\s*앞|목전|향(?:한|해|하|하고)|앞두|기다|노리|바라보|내다보|"
    r"남[았아은겨]|남기|부족|모자|더\s*(?:필요|하면|잡|보태|쌓)|"
    r"(?:채우|넘기|달성하|돌파하|기록하)면|(?:달성|돌파|기록|채울|넘길)\s*할?\s*(?:수|것|예정)|"
    r"(?:달성|돌파|기록)할|아직.{0,24}(?:못|않)|(?:달성|돌파|기록)하지\s*(?:못|않)")
_COMPLETED = re.compile(
    r"(?:달성|돌파|기록)(?:했|한\s|하며|하고|해냈|해\s)|"
    r"채웠|넘겼|넘어섰|도달했|(?:목표|도전)(?:를|을)?\s*(?:이뤘|완수|성공)|"
    r"(?:달성|돌파)의\s*순간|(?:기록|성적)(?:이다|이었다|입니다)")
_NEGATED = re.compile(r"(?:달성|돌파|기록)한\s*(?:것은|건)\s*아니|(?:달성|돌파|기록)하지\s*(?:못|않)")
_GAP = re.compile(r"(?P<count>\d+(?:,\d+)*|한|두|세|네|다섯)\s*(?P<unit>개|탈\s*삼\s*진|홈\s*런|승|도루|안타|타점)(?:씩)?")
_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}


def _issue(statement, rows, reason, numbers=()):
    return {"paragraph_index": statement.paragraph, "reason": reason,
            "numbers": sorted(set(map(str, numbers)) | {str(row["target"]) for row in rows}),
            "metrics": [row["stat"] for row in rows],
            "proofs": [{key: row[key] for key in ("stat", "label", "current", "target", "remaining")} for row in rows]}


def _resolved_mentions(statement, rows):
    mentions = [(match.start(), match.end(), row) for row in rows
                for match in target_mentions(statement.text, row)]
    # Resolve a dropped metric only from an unambiguous proved current quantity
    # in the same/preceding sentence. Never use an unrelated number or speaker.
    for match in re.finditer(r"(?<![\w.,+-])(?P<n>\d+(?:,\d+)*)\s*(?:개|번째)(?![가-힣A-Za-z])", statement.text):
        if any(start < match.end() and match.start() < end for start, end, _ in mentions):
            continue
        context = statement.previous + statement.text[:match.start()]
        candidates = [row for row in rows if int(match["n"].replace(",", "")) == row["target"]
                      and any(target_mentions(context, {**row, "target": row["current"]}))]
        if len(candidates) == 1:
            mentions.append((match.start(), match.end(), candidates[0]))
    return sorted(mentions)


def prospective_projection(text: str, targets, known_numbers=()):
    """Project proved goals by discourse unit; collect independent conflicts.

    A predicate may govern several adjacent targets, not a fixed character
    window. A completed clause cannot borrow a goal from another sentence,
    speaker or paragraph. A following sentence may qualify a nominal target
    only with an explicit anaphoric reference ("that goal", "each milestone").
    """
    value = analysis_text(text)
    # A known number still participates in a coordinated phrase. Dropping it
    # first would misread "500 K and 25 wins, one left" as a 25-unit remainder.
    rows = list(targets)
    masks, issues = [], []
    for statement in statements(value):
        mentions = _resolved_mentions(statement, rows)
        groups = []
        for mention in mentions:
            if groups and _LINK.fullmatch(statement.text[groups[-1][-1][1]:mention[0]]):
                groups[-1].append(mention)
            else:
                groups.append([mention])
        for index, group in enumerate(groups):
            start, end = group[0][0], group[-1][1]
            group_rows = [entry[2] for entry in group]
            if all(str(row["target"]) in known_numbers for row in group_rows):
                continue
            prefix = statement.text[groups[index - 1][-1][1] if index else 0:start]
            suffix = statement.text[end:groups[index + 1][0][0] if index + 1 < len(groups) else None]
            # Do not allow another subject's later goal to qualify this record.
            suffix = re.split(r"(?:[.!?。！？]|[,，]\s*(?:하지만|그는|선수는)|(?:반면|그러나))", suffix, maxsplit=1)[0]
            completed = bool(_COMPLETED.search(suffix) and not _NEGATED.search(suffix))
            forward = bool(_FUTURE.search(suffix) or _GOAL_PREFIX.search(prefix))
            continuation = ""
            if not completed and not forward and re.fullmatch(r"[\s\"'”’.,]*(?:이?다[.!]?)?[\s\"'”’.,]*", suffix):
                if re.match(r"^[\s\"'”’]*(?:이|그|두|각)\s*(?:목표|고지|이정표|기록)", statement.following):
                    continuation = statement.following
                    forward = bool(_FUTURE.search(continuation))
                if _GOAL_PREFIX.search(statement.previous) and not re.search(r"달성|돌파|기록했", statement.previous):
                    forward = True
            if completed or not forward:
                issues.append(_issue(statement, group_rows, "achieved_future_target" if completed else "unresolved_target_context"))
                continue
            gap_text = suffix + continuation
            gaps = list(_GAP.finditer(gap_text)) if re.search(r"남[았아은겨]|남기|부족|모자|더\s*필요", gap_text) else []
            if gaps:
                counts = [_WORDS[m["count"]] if m["count"] in _WORDS else int(m["count"].replace(",", "")) for m in gaps]
                expected = [row["remaining"] for row in group_rows]
                valid = (len(counts) == 1 and all(count == counts[0] for count in expected)
                         or len(counts) == len(expected) and "각각" in gap_text and counts == expected)
                if not valid:
                    issues.append(_issue(statement, group_rows, "remaining_mismatch", counts))
                    continue
                for gap in gaps:
                    if gap.end() <= len(suffix):
                        masks.append((statement.start + end + gap.start("count"), statement.start + end + gap.end("count")))
                    elif continuation:
                        masks.append((statement.end + gap.start("count") - len(suffix),
                                      statement.end + gap.end("count") - len(suffix)))
            masks.extend((statement.start + first, statement.start + last) for first, last, _ in group)
    for start, end in sorted(set(masks), reverse=True):
        value = value[:start] + " " * (end - start) + value[end:]
    return value, issues


_STAT_LABELS = {
    "pit_IP": "이닝", "pit_K": "탈삼진", "pit_W": "승", "pit_H": "피안타", "pit_TBF": "상대타자",
    "bat_AB": "타수", "bat_H": "안타", "bat_HR": "홈런", "bat_RBI": "타점", "bat_R": "득점", "bat_SB": "도루",
}
_PERIOD = re.compile(
    r"(?P<history>(?:지난|이전|전년)\s*(?:시즌|해)|작년|과거)|(?P<year>\d{4})년|"
    r"(?P<game>오늘\s*경기|이날\s*경기|이번\s*경기|오늘\s*(?:또\s*)?)|"
    r"(?P<career>통산\s*(?:누적|누계|기록|성적)?)|(?P<season>시즌|누적)")


def _period_kind(periods, facts):
    if not periods:
        return None
    last = periods[-1]
    if last.lastgroup == "year":
        return "season" if str(facts.get("season_year")) == last[0][:-1] else "history"
    return last.lastgroup


def record_context_issues(text: str, facts=None):
    """Check explicit period/unit contradictions against structured save facts.

    Unknown natural-language assertions are not guessed. An absent game fact
    never turns a save delta into a single game. Temporal words in a heading
    alone do not make every statistic under that heading a daily statistic.
    """
    if not facts:
        return []
    season = facts.get("season") or {}
    issues = []
    inherited_period = {}
    for statement in statements(analysis_text(text)):
        declared = list(_PERIOD.finditer(statement.text))
        introduced = bool(re.search(r"(?:기록|결과|성적|이닝).*(?:말씀|발표|다음|보면)", statement.text))
        mentions = []
        for stat, label in _STAT_LABELS.items():
            if season.get(stat) is None:
                continue
            pattern = (r"(?<![A-Za-z0-9.,+-])(?P<a>\d+(?:[.,]\d+)*)\s*" + label
                       + r"|(?<![가-힣A-Za-z])" + label
                       + r"\s*(?:(?:는|은|가|이)\s*)?(?:단\s*|총\s*)?(?P<b>\d+(?:[.,]\d+)*)(?!\d|\.\d)")
            for match in re.finditer(pattern, statement.text):
                mentions.append((match.start(), match.end(), stat, match["a"] or match["b"]))
        for start, end, stat, number in sorted(mentions):
            prefix = statement.text[:start]
            periods = list(_PERIOD.finditer(prefix))
            # A preceding sentence may explicitly introduce the scope of a
            # quoted/listed record in the same paragraph, not another speaker.
            period = _period_kind(periods, facts) if periods else inherited_period.get(statement.paragraph)
            if period not in ("game", "season"):
                continue
            try:
                numeric = Decimal(number.replace(",", ""))
                current = Decimal(str(season[stat]))
            except InvalidOperation:
                continue  # Missing/unknown engine fields are never new proofs.
            if not numeric.is_finite() or not current.is_finite():
                continue
            if period == "game" and str((facts.get("game") or {}).get(stat)) == number:
                continue
            suffix = statement.text[end:]
            if period == "season" and (numeric == current or _FUTURE.search(suffix) and not _COMPLETED.search(suffix)
                                       or _GOAL_PREFIX.search(prefix) and not _COMPLETED.search(suffix)):
                continue
            if period == "game" and numeric != current:
                continue
            issues.append({"paragraph_index": statement.paragraph,
                           "reason": "season_as_single_game" if period == "game" else "current_metric_mismatch",
                           "numbers": [number], "metrics": [stat], "proofs": [],
                           "verified_counts": [{"stat": stat, "label": _STAT_LABELS[stat], "current": season[stat]}]})
        if declared and (introduced or mentions):
            inherited_period[statement.paragraph] = _period_kind(declared, facts)
        # An explicit zero-hit inference contradicts a positive supplied count;
        # colloquial fan hyperbole without that inference remains untouched.
        hits_allowed = season.get("pit_H")
        if (type(hits_allowed) in (int, float) and hits_allowed > 0
                and re.search(r"(?:이라는|라는)\s*것은", statement.text)
                and re.search(r"동안.{0,32}안타를?\s*맞지\s*않았다는\s*뜻", statement.text)):
            issues.append({"paragraph_index": statement.paragraph, "reason": "contradictory_hit_inference",
                           "numbers": [], "metrics": ["pit_H"], "proofs": [],
                           "verified_counts": [{"stat": "pit_H", "label": "피안타", "current": season["pit_H"]}]})
    return issues


def mask_scene_metadata(text: str) -> str:
    """Do not interpret fictional timeline labels or URL identifiers as stats."""
    value = re.sub(r"https?://[^\s<>]+", lambda m: re.sub(r"\d", " ", m[0]), text)
    value = re.sub(r"^(#{1,3}\s+)([^\n]*)", lambda m: m[1] + re.sub(
        r"(?<!\w)T\+\d+(?:분|시간)", lambda t: re.sub(r"\d", " ", t[0]), m[2]), value, flags=re.M)
    expected = 1
    def ordinal(match):
        nonlocal expected
        if int(match[2]) != expected:
            return match[0]
        expected += 1
        return match[1] + " " * len(match[2]) + match[3]
    return re.sub(r"^(#{1,3}[ \t]+)(\d{1,2})([.)][ \t]+)", ordinal, value, flags=re.M)
