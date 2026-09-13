#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stat calculations, tone assessment, and milestone detection.

Numbers are treated conservatively (engine 06): only derive what the data
supports. ER is not stored per-save here, so ERA is shown as an estimate
when a season ER figure is unknown.
"""

# round batting milestone thresholds worth a story
_HR_MARKS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
_K_MARKS = [50, 100, 150, 200, 250, 300, 350, 400, 450]
_W_MARKS = [5, 10, 15, 20, 25]
_SB_MARKS = [20, 40, 60, 80, 100, 120]
_H_MARKS = [50, 100, 150, 200, 250]
_RBI_MARKS = [50, 100, 150, 200, 250]

_NARRATIVE_TARGETS = (
    ("pit_K", "탈삼진", _K_MARKS),
    ("pit_W", "승", _W_MARKS),
    ("bat_HR", "홈런", _HR_MARKS),
    ("bat_SB", "도루", _SB_MARKS),
    ("bat_H", "안타", _H_MARKS),
    ("bat_RBI", "타점", _RBI_MARKS),
)


def upcoming_narrative_targets(stats):
    """Project nearby round season targets, never award achieved milestones.

    Continue each existing threshold series beyond its last entry. Only count
    stats within five of the next threshold qualify; rates and innings do not.
    This read-only projection does not change event detection or stored data.
    """
    result = []
    for key, label, marks in _NARRATIVE_TARGETS:
        current = stats.get(key)
        if type(current) is not int or current <= 0:
            continue
        step = marks[-1] - marks[-2]
        target = next((mark for mark in marks if mark > current), None)
        if target is None:
            target = marks[-1] + ((current - marks[-1]) // step + 1) * step
        remaining = target - current
        if 1 <= remaining <= 5:
            result.append({"stat": key, "label": label, "scope": "season",
                           "current": current, "target": target, "remaining": remaining})
    return result


def validate_narrative_targets(targets):
    """Accept only exact engine-derived proofs, not a caller's number allowlist."""
    rows = list(targets or ())
    if len(rows) > len(_NARRATIVE_TARGETS):
        raise ValueError("Too many narrative target proofs")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("stat"), str)
                or row.get("stat") in seen
                or any(type(row.get(key)) is not int for key in ("current", "target", "remaining"))):
            raise ValueError("Invalid narrative target proof")
        expected = upcoming_narrative_targets({row.get("stat"): row.get("current")})
        if len(expected) != 1 or row != expected[0]:
            raise ValueError("Narrative targets must match the current count and threshold")
        seen.add(row["stat"])
    return rows


def narrative_target_context(targets):
    """Supply exact arithmetic as prospective context, separate from hard facts."""
    if not targets:
        return ""
    lines = ["[계산된 다음 목표 — 아직 달성한 기록이 아님]",
             "다음 수치는 현재 시즌 기록에서 계산한 전망이다. 현재 성적·통산 성적·달성 사실로 쓰지 마라."]
    for row in targets:
        lines.append(
            f"- 시즌 {row['label']}: 현재 {row['current']}, 다음 목표 {row['target']}, "
            f"남은 수량 {row['remaining']}. 표현 예: \"시즌 {row['target']}{row['label']}까지 "
            f"{row['remaining']}개 남았다.\" 아직 달성·돌파·기록했다고 쓰지 않는다."
        )
    lines.append("목표를 쓰면 같은 문장에 '목표', '도전', '남았다', '앞두다' 같은 미래 맥락을 분명히 쓴다.")
    return "\n".join(lines)


def _crossed(prev, now, marks):
    return [m for m in marks if (prev or 0) < m <= (now or 0)]


def innings_to_outs(value):
    """Convert save innings notation (153, 153.1, "153.2") to outs.

    The stored ``pit_IP`` keeps the game's ``X.1`` / ``X.2`` display form.
    Every calculation must go through outs so that 6.1 - 5.2 is two outs and
    not a decimal subtraction. Unknown or malformed values count as zero.
    """
    if value in (None, "", False):
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value) * 3
    text = str(value).strip()
    if not text:
        return 0
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    whole, _, remainder = text.partition(".")
    try:
        whole_n = int(whole) if whole else 0
    except ValueError:
        return 0
    remainder_n = 0
    if remainder:
        if remainder in ("1", "2"):
            remainder_n = int(remainder)
        elif set(remainder) <= {"0"}:
            remainder_n = 0
        else:
            # Legacy decimal fractions (for example 0.333 or the result of a
            # float subtraction) are rounded to the nearest third.
            try:
                remainder_n = int(round(float("0." + remainder) * 3))
            except ValueError:
                remainder_n = 0
    outs = whole_n * 3 + remainder_n
    return -outs if negative else outs


def outs_to_innings_value(outs):
    """Inverse of ``innings_to_outs`` in the stored numeric form (int or X.1/X.2)."""
    outs = int(outs)
    sign = -1 if outs < 0 else 1
    whole, remainder = divmod(abs(outs), 3)
    if remainder == 0:
        return sign * whole
    return sign * float(f"{whole}.{remainder}")


def innings_float(value):
    """Real innings (153.2 -> 153.667) for rate calculations such as K/9."""
    return innings_to_outs(value) / 3.0


def innings_delta(current, previous):
    """Difference of two innings values expressed in the stored form."""
    return outs_to_innings_value(innings_to_outs(current) - innings_to_outs(previous))


def assess(stats):
    """Overall season tone + narrative hooks from cumulative stats."""
    ip = stats.get("pit_IP", 0)
    k = stats.get("pit_K", 0)
    ph = stats.get("pit_H", 0)
    w = stats.get("pit_W", 0)
    ab = stats.get("bat_AB", 0)
    hr = stats.get("bat_HR", 0)
    rbi = stats.get("bat_RBI", 0)
    sb = stats.get("bat_SB", 0)
    avg = stats.get("bat_AVG", 0.0)
    # Rate math uses real innings (X.1 = one third); display keeps the raw value.
    ip_value = innings_float(ip)
    k9 = round(k * 9.0 / ip_value, 1) if ip_value else 0.0

    facts = []
    grade = 0
    if ip and ph <= max(10, int(ip_value // 15)):
        facts.append(f"시즌 피안타 단 {ph}개 — 타자들이 공을 맞히질 못한다")
        grade += 3
    if k9 >= 15:
        facts.append(f"9이닝당 {k9}탈삼진, 시즌 {k}K — 만화급 탈삼진")
        grade += 3
    if w >= 15:
        facts.append(f"시즌 {w}승")
        grade += 2
    if avg >= 0.400:
        facts.append(f"타율 {avg:.3f} — '4할 불가능'이 무색")
        grade += 3
    if hr >= 50:
        facts.append(f"{hr}홈런 — 홈런 신기록 페이스")
        grade += 3
    if sb >= 50:
        facts.append(f"{sb}도루 — 파워와 스피드 동시에")
        grade += 2
    if k and ab and avg >= 0.4:
        facts.append("투수·타자 양쪽 리그 지배 — '이도류' 이상")
        grade += 3

    if grade >= 12:
        tone = "폭발"
    elif grade >= 7:
        tone = "좋음"
    elif grade >= 3:
        tone = "애매"
    elif grade >= 1:
        tone = "보통"
    else:
        tone = "부진"

    line_pit = None
    if ip:
        line_pit = f"시즌 {w}승 · {ip}이닝 · {k}탈삼진 · 피안타 {ph}"
    line_bat = None
    if ab:
        line_bat = f"타율 {avg:.3f} · {hr}홈런 · {rbi}타점 · {sb}도루 · {stats.get('bat_H',0)}안타"
    return {"tone": tone, "grade": grade, "facts": facts, "k9": k9,
            "line_pit": line_pit, "line_bat": line_bat}


def new_milestones(prev, now):
    """Milestones crossed between two stat snapshots (drives EXPLOSION mode)."""
    p = prev or {}
    ms = []
    for m in _crossed(p.get("bat_HR"), now.get("bat_HR"), _HR_MARKS):
        ms.append(("홈런", f"시즌 {m}홈런 돌파"))
    for m in _crossed(p.get("pit_K"), now.get("pit_K"), _K_MARKS):
        ms.append(("탈삼진", f"시즌 {m}탈삼진 돌파"))
    for m in _crossed(p.get("pit_W"), now.get("pit_W"), _W_MARKS):
        ms.append(("승수", f"시즌 {m}승 달성"))
    for m in _crossed(p.get("bat_SB"), now.get("bat_SB"), _SB_MARKS):
        ms.append(("도루", f"시즌 {m}도루 돌파"))
    for m in _crossed(p.get("bat_H"), now.get("bat_H"), _H_MARKS):
        ms.append(("안타", f"시즌 {m}안타 돌파"))
    for m in _crossed(p.get("bat_RBI"), now.get("bat_RBI"), _RBI_MARKS):
        ms.append(("타점", f"시즌 {m}타점 돌파"))
    # crossing the .400 / .500 / .700 average line
    pa, na = (p.get("bat_AVG") or 0), (now.get("bat_AVG") or 0)
    for line in (0.400, 0.500, 0.600, 0.700):
        if pa < line <= na:
            ms.append(("타율", f"타율 {line:.3f} 돌파"))
    return ms


def describe_game(delta):
    """Turn a stat delta into a one-line game description (pitching/batting)."""
    parts = []
    dip = delta.get("pit_IP", 0)
    dk = delta.get("pit_K", 0)
    dtbf = delta.get("pit_TBF", 0)
    dph = delta.get("pit_H", 0)
    if dip or dtbf:
        seg = f"{dip}이닝" if dip else f"{dtbf}타자 상대"
        line = f"등판: {seg}, {dk}탈삼진, 피안타 {dph}"
        if dtbf and dk == dtbf and dph == 0:
            line += " — 기록상 상대한 타자 전원 삼진"
        parts.append(line)
    dab = delta.get("bat_AB", 0)
    dh = delta.get("bat_H", 0)
    dhr = delta.get("bat_HR", 0)
    drbi = delta.get("bat_RBI", 0)
    if dab:
        seg = f"{dab}타수 {dh}안타"
        if dhr:
            seg += f" {dhr}홈런"
        if drbi:
            seg += f" {drbi}타점"
        parts.append("타격: " + seg)
    return parts
