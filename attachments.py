#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local, non-LLM attachment understanding (master plan 8.12, 16.7, 17.4).

Two analysis paths, both offline:

1. Known game-screen parsers — the Windows-built-in OCR engine
   (``Windows.Media.Ocr`` through Windows PowerShell 5.1, no network) plus
   versioned layout profiles for the Korean game UI: 경기 결과, 타격 기록,
   야수 성적. Every parsed field carries a confidence and a validation state.
2. Manual description — when no parser applies or no OCR engine is installed,
   the user names what matters. Manual confirmation is a designed path.

Nothing here contacts an LLM or the network. EXIF/APP segments are never
stored in derived records. Parsed numbers are candidates until the user
reviews them; the save file remains the only ``save_verified`` source.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

ANALYSIS_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
OCR_SCRIPT = HERE / "tools" / "winrt_ocr.ps1"
PROFILE_FILE = HERE / "data" / "parser_profiles" / "prospi_ko_v1.json"
OCR_TIMEOUT_SECONDS = 120
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

DECISIONS = ("user_confirmed", "story_prop", "session_only", "ignore")
DECISION_LABELS = {
    "user_confirmed": "사용자 확인 사실로 반영",
    "story_prop": "창작 소재로만 사용",
    "session_only": "이번 대화에서만 사용",
    "ignore": "무시",
}
RETENTION = ("keep_original", "analysis_only", "delete_after")
RETENTION_LABELS = {"keep_original": "원본 보관", "analysis_only": "분석 결과만 보관", "delete_after": "이번 대화 후 삭제"}


class OcrUnavailable(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable(*parts, length: int = 12) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# Image bytes: type, dimensions, metadata stripping
# --------------------------------------------------------------------------


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return None


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    kind = sniff_image(data)
    try:
        if kind == "png" and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if kind == "gif" and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if kind == "bmp" and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))
        if kind == "jpg":
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker = data[offset + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    offset += 2
                    continue
                length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                    return int(width), int(height)
                offset += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Remove APP1..APP15 (EXIF, XMP, ICC...) and COM segments from a JPEG.

    APP0 (JFIF) is kept because some decoders expect it. Non-JPEG input is
    returned unchanged. The user's original file is never rewritten by the
    analysis path; this is used for uploads and derived copies.
    """
    if sniff_image(data) != "jpg":
        return data
    out = bytearray(data[:2])
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            out.extend(data[offset:])
            break
        marker = data[offset + 1]
        if marker == 0xDA:  # start of scan: copy the rest verbatim
            out.extend(data[offset:])
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out.extend(data[offset : offset + 2])
            offset += 2
            continue
        length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        segment = data[offset : offset + 2 + length]
        if 0xE1 <= marker <= 0xEF or marker == 0xFE:
            offset += 2 + length
            continue
        out.extend(segment)
        offset += 2 + length
    return bytes(out)


def file_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Local OCR runner (Windows.Media.Ocr through Windows PowerShell 5.1)
# --------------------------------------------------------------------------

_ocr_lock = threading.Lock()
_ocr_status: dict | None = None


def _powershell_exe() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _run_script(args: list[str], timeout: float) -> dict:
    exe = _powershell_exe()
    if not os.path.isfile(exe) or not OCR_SCRIPT.is_file():
        raise OcrUnavailable("Windows PowerShell 5.1 or the OCR script is not available")
    command = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT), *args]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=creation_flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrUnavailable(f"local OCR did not complete: {exc}") from exc
    text = completed.stdout.decode("utf-8-sig", errors="replace").strip()
    if completed.returncode != 0 or not text:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OcrUnavailable(f"local OCR failed: {error[:300] or 'no output'}")
    try:
        payload = json.loads(text[text.index("{") :])
    except (ValueError, json.JSONDecodeError) as exc:
        raise OcrUnavailable(f"local OCR returned unreadable output: {exc}") from exc
    if payload.get("error"):
        raise OcrUnavailable(str(payload["error"]))
    return payload


def ocr_status(*, refresh: bool = False) -> dict:
    """Availability of the local engine and its languages (cached per process)."""
    global _ocr_status
    with _ocr_lock:
        if _ocr_status is not None and not refresh:
            return dict(_ocr_status)
        try:
            payload = _run_script([], timeout=60)
            _ocr_status = {
                "available": True,
                "engine": "windows_media_ocr",
                "languages": list(payload.get("languages") or []),
                "max_dimension": payload.get("max_dimension"),
                "network": False,
                "llm": False,
            }
        except OcrUnavailable as exc:
            _ocr_status = {"available": False, "engine": None, "languages": [], "reason": str(exc), "network": False, "llm": False}
        return dict(_ocr_status)


def run_local_ocr(path: str | os.PathLike, *, language: str = "ko", timeout: float = OCR_TIMEOUT_SECONDS) -> dict:
    """OCR one image locally. Raises OcrUnavailable when the engine is missing."""
    status = ocr_status()
    if not status.get("available"):
        raise OcrUnavailable(str(status.get("reason") or "local OCR unavailable"))
    languages = status.get("languages") or []
    chosen = language if language in languages else next((row for row in languages if row.startswith(language.split("-")[0])), None)
    if chosen is None:
        chosen = "ko" if "ko" in languages else (languages[0] if languages else "")
    payload = _run_script(["-Path", str(Path(path).resolve()), "-Lang", chosen], timeout=timeout)
    return {
        "engine": "windows_media_ocr",
        "engine_language": payload.get("engine_language") or chosen,
        "width": payload.get("width"),
        "height": payload.get("height"),
        "text_angle": payload.get("text_angle"),
        "lines": list(payload.get("lines") or []),
    }


# --------------------------------------------------------------------------
# Profiles and geometry helpers
# --------------------------------------------------------------------------

_profile_cache: dict | None = None


def load_profiles() -> dict:
    global _profile_cache
    if _profile_cache is None:
        try:
            _profile_cache = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _profile_cache = {"profiles": [], "plate_appearance_tokens": {}}
    return _profile_cache


def _words(ocr: dict) -> list[dict]:
    rows = []
    for line_index, line in enumerate(ocr.get("lines") or []):
        for word in line.get("words") or []:
            try:
                rows.append({"text": str(word.get("text") or ""), "x": int(word.get("x") or 0), "y": int(word.get("y") or 0), "w": int(word.get("w") or 0), "h": int(word.get("h") or 0), "line": line_index})
            except (TypeError, ValueError):
                continue
    rows.sort(key=lambda row: (row["y"], row["x"]))
    return rows


def _line_texts(ocr: dict) -> list[str]:
    return [str(line.get("text") or "") for line in ocr.get("lines") or []]


def _center_x(word: dict) -> float:
    return word["x"] + word["w"] / 2.0


def _center_y(word: dict) -> float:
    return word["y"] + word["h"] / 2.0


def classify_screen(ocr: dict) -> dict:
    """Pick the best layout profile from anchors; unknown when none applies."""
    texts = " ".join(_line_texts(ocr))
    best = None
    for profile in load_profiles().get("profiles") or []:
        required = profile.get("required_any") or []
        if required and not any(token in texts for token in required):
            continue
        support = sum(1 for token in profile.get("supporting") or [] if token in texts)
        total = len(profile.get("supporting") or []) or 1
        score = 0.6 + 0.4 * (support / total)
        if best is None or score > best["confidence"]:
            best = {"profile_id": profile["profile_id"], "screen_type": profile["screen_type"], "label": profile.get("label"), "confidence": round(score, 3), "supporting_matches": support}
    return best or {"profile_id": None, "screen_type": "unknown", "label": None, "confidence": 0.0, "supporting_matches": 0}


def _assign_chars(word: dict, columns: list[tuple[str, float]]) -> list[tuple[str, str]]:
    """Map each character of a (possibly merged) numeric word to the nearest column."""
    text = word["text"]
    chars = [char for char in text if char.isdigit() or char in "xX"]
    if not chars or not columns:
        return []
    count = len(chars)
    results = []
    for index, char in enumerate(chars):
        center = word["x"] + word["w"] * (index + 0.5) / count
        column = min(columns, key=lambda item: abs(item[1] - center))
        results.append((column[0], "x" if char in "xX" else char))
    return results


def _linescore_header(words: list[dict]) -> dict | None:
    """Find the 1..9 R H E header; returns column centers and header y."""
    digits = [row for row in words if row["text"] in tuple("123456789")]
    best = None
    for anchor in digits:
        group = [row for row in digits if abs(_center_y(row) - _center_y(anchor)) <= 14]
        seen = {}
        for row in sorted(group, key=lambda item: item["x"]):
            seen.setdefault(row["text"], row)
        if len(seen) >= 7 and (best is None or len(seen) > len(best)):
            best = seen
    if not best:
        return None
    header_y = sum(_center_y(row) for row in best.values()) / len(best)
    columns = [(text, _center_x(row)) for text, row in sorted(best.items(), key=lambda item: item[1]["x"])]
    # Fill missing inning columns by interpolation.
    known = {int(text): x for text, x in columns}
    if len(known) >= 2:
        pitch = (max(known.values()) - min(known.values())) / max(1, (max(known) - min(known)))
        for inning in range(1, 10):
            if inning not in known:
                base = min(known, key=lambda item: abs(item - inning))
                known[inning] = known[base] + (inning - base) * pitch
    columns = [(str(inning), known[inning]) for inning in sorted(known)]
    rhe = {}
    for row in words:
        if abs(_center_y(row) - header_y) > 16:
            continue
        text = row["text"].replace(" ", "")
        if text in ("R", "H", "E"):
            rhe[text] = _center_x(row)
        elif text == "RHE":
            for index, name in enumerate("RHE"):
                rhe[name] = row["x"] + row["w"] * (index + 0.5) / 3.0
    if len(rhe) < 3 and len(known) >= 9:
        # R/H/E were not recognised: extrapolate from the inning pitch.
        pitch = (known[9] - known[1]) / 8.0
        rhe = {"R": known[9] + pitch * 1.3, "H": known[9] + pitch * 2.15, "E": known[9] + pitch * 3.0}
    columns.extend((name, rhe[name]) for name in ("R", "H", "E") if name in rhe)
    return {"y": header_y, "columns": columns, "recognised_rhe": len(rhe) >= 3}


def parse_linescore(words: list[dict]) -> dict | None:
    header = _linescore_header(words)
    if not header:
        return None
    columns = header["columns"]
    candidates = [row for row in words if header["y"] + 30 < _center_y(row) < header["y"] + 520 and any(char.isdigit() or char in "xX" for char in row["text"])]
    clusters: list[list[dict]] = []
    for row in sorted(candidates, key=lambda item: _center_y(item)):
        for cluster in clusters:
            if abs(_center_y(cluster[0]) - _center_y(row)) <= 24:
                cluster.append(row)
                break
        else:
            clusters.append([row])
    parsed_rows = []
    for cluster in clusters[:2]:
        cells: dict[str, str] = {}
        for row in sorted(cluster, key=lambda item: item["x"]):
            for column, char in _assign_chars(row, columns):
                cells[column] = cells.get(column, "") + char
        innings = []
        for inning in range(1, 10):
            value = cells.get(str(inning))
            if value is None:
                innings.append(None)
            elif "x" in value:
                innings.append("x")
            else:
                innings.append(int(value))
        def _int(name):
            value = cells.get(name)
            try:
                return int(value) if value not in (None, "") else None
            except ValueError:
                return None
        parsed_rows.append({"innings": innings, "R": _int("R"), "H": _int("H"), "E": _int("E"), "y": round(_center_y(cluster[0]))})
    if len(parsed_rows) < 2:
        return None
    visitor, home = parsed_rows[0], parsed_rows[1]
    for row in (visitor, home):
        played = [value for value in row["innings"] if isinstance(value, int)]
        row["sum"] = sum(played)
        row["consistent"] = row["R"] is not None and row["R"] == row["sum"] and None not in row["innings"][: len(played)]
    return {"visitor": visitor, "home": home, "header_y": round(header["y"]), "recognised_rhe": header["recognised_rhe"]}


def _observation(attachment_id: str, field: str, label: str, value, raw_text: str, confidence: float, *, validation: str = "ok", note: str = "", impact: str = "normal") -> dict:
    suggested = "user_confirmed" if validation == "ok" and confidence >= 0.6 else "ignore"
    return {
        "observation_id": _stable(attachment_id, field, raw_text, str(value)),
        "field": field,
        "label": label,
        "value": value,
        "raw_text": raw_text,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "validation": validation,
        "note": note,
        "impact": impact,
        "suggested_decision": suggested,
        "decision": "pending",
    }


def _normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", str(text or "").casefold())


def name_matches(text: str, names) -> bool:
    haystack = _normalize_name(text)
    for name in names or ():
        for part in str(name).replace("(", " ").replace(")", " ").split():
            token = _normalize_name(part)
            if len(token) >= 4 and token in haystack:
                return True
    return False


def _team_side(home_team: str | None, visitor_team: str | None, protagonist_team: str | None) -> str | None:
    if not protagonist_team:
        return None
    team = _normalize_name(protagonist_team)
    for side, name in (("home", home_team), ("visitor", visitor_team)):
        token = _normalize_name(name or "")
        if len(token) >= 3 and token in team:
            return side
    return None


# --------------------------------------------------------------------------
# Screen parsers
# --------------------------------------------------------------------------

_DATE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_ATTENDANCE = re.compile(r"(\d{3,6})\s*명")
_MATCHUP = re.compile(r"^(?P<stadium>.*?)\s*(?P<home>[A-Za-z][A-Za-z.]+)\s+VS\s+(?P<visitor>[A-Za-z][A-Za-z.]+)\s+(?P<game>\d+)회전\s+(?P<w>\d+)승\s+(?P<l>\d+)패\s+(?P<t>\d+)무")
_PITCHER = re.compile(r"^(?P<name>[A-Za-z][A-Za-z.\- ]{1,30}?)\s+(?P<w>\d+)승\s+(?P<l>\d+)패")
_HOME_RUNS = re.compile(r"^(?P<name>[A-Za-z][A-Za-z.\- ]{1,30}?)\s+(?P<numbers>(?:\d+\s*,\s*)*\d+)\s*호")


def parse_game_result(ocr: dict, attachment_id: str, *, protagonist_names=(), protagonist_team: str | None = None) -> list[dict]:
    words = _words(ocr)
    lines = _line_texts(ocr)
    observations: list[dict] = []
    joined = "\n".join(lines)
    match = _DATE.search(joined)
    if match:
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        valid = 1 <= month <= 12 and 1 <= day <= 31
        observations.append(_observation(attachment_id, "game.date", "경기 날짜", f"{year:04d}-{month:02d}-{day:02d}", match.group(0), 0.95 if valid else 0.3, validation="ok" if valid else "fail", impact="binding"))
    match = _ATTENDANCE.search(joined)
    if match:
        observations.append(_observation(attachment_id, "game.attendance", "관중", int(match.group(1)), match.group(0), 0.9))
    for line in lines:
        match = _MATCHUP.search(line.strip())
        if match:
            observations.append(_observation(attachment_id, "game.stadium", "구장", match.group("stadium").strip() or None, line, 0.8 if match.group("stadium").strip() else 0.4))
            observations.append(_observation(attachment_id, "game.home_team", "홈 팀", match.group("home"), line, 0.85))
            observations.append(_observation(attachment_id, "game.visitor_team", "원정 팀", match.group("visitor"), line, 0.85))
            observations.append(_observation(attachment_id, "game.series_game", "시즌 맞대결 차수", int(match.group("game")), line, 0.85))
            observations.append(_observation(attachment_id, "game.head_to_head", "상대 전적 (승-패-무)", {"wins": int(match.group("w")), "losses": int(match.group("l")), "ties": int(match.group("t"))}, line, 0.85))
            break
    linescore = parse_linescore(words)
    if linescore:
        for side in ("visitor", "home"):
            row = linescore[side]
            label = "원정" if side == "visitor" else "홈"
            validation = "ok" if row["consistent"] else "warn"
            note = "" if row["consistent"] else "이닝 합계와 R이 일치하지 않습니다. 화면과 대조하세요."
            observations.append(_observation(attachment_id, f"game.linescore.{side}", f"{label} 이닝별 득점", row["innings"], json.dumps(row["innings"], ensure_ascii=False), 0.8 if row["consistent"] else 0.5, validation=validation, note=note))
            for key, name in (("R", "득점"), ("H", "안타"), ("E", "실책")):
                if row[key] is not None:
                    observations.append(_observation(attachment_id, f"game.{key}.{side}", f"{label} {name}", row[key], str(row[key]), 0.8 if (key != "R" or row["consistent"]) else 0.5, validation="ok" if (key != "R" or row["consistent"]) else "warn", impact="high" if key == "R" else "normal"))
    teams = {row["field"]: row["value"] for row in observations if row["field"] in ("game.home_team", "game.visitor_team")}
    side = _team_side(teams.get("game.home_team"), teams.get("game.visitor_team"), protagonist_team)
    if linescore and side:
        mine = linescore[side]["R"]
        theirs = linescore["visitor" if side == "home" else "home"]["R"]
        if mine is not None and theirs is not None:
            outcome = "win" if mine > theirs else "loss" if mine < theirs else "tie"
            opponent = teams.get("game.visitor_team" if side == "home" else "game.home_team")
            label = {"win": "승리", "loss": "패배", "tie": "무승부"}[outcome]
            consistent = linescore[side]["consistent"] and linescore["visitor" if side == "home" else "home"]["consistent"]
            observations.append(_observation(attachment_id, "game.result", "경기 결과", {"outcome": outcome, "runs_for": mine, "runs_against": theirs, "side": side, "opponent": opponent}, f"{mine}-{theirs} {label} ({'홈' if side == 'home' else '원정'}, vs {opponent})", 0.85 if consistent else 0.55, validation="ok" if consistent else "warn", note="" if consistent else "이닝별 득점 합계 확인 필요", impact="high"))
    elif linescore and not side:
        observations.append(_observation(attachment_id, "game.result", "경기 결과", None, "팀 식별 불가", 0.2, validation="fail", note="세이브의 팀 이름과 화면의 팀 표기를 연결하지 못했습니다. 직접 설명으로 알려 주세요.", impact="high"))
    section = None
    for line in lines:
        text = line.strip()
        if text.startswith("승리 투수"):
            section = "winning"
            continue
        if text.startswith("패전 투수"):
            section = "losing"
            continue
        if text.startswith("세이브"):
            section = "save"
            continue
        if text.startswith("홈런"):
            section = "home_runs"
            continue
        match = _PITCHER.match(text)
        if match and section in ("winning", "losing", "save", None):
            field = {"winning": "game.winning_pitcher", "losing": "game.losing_pitcher", "save": "game.save_pitcher"}.get(section or "", None)
            if field is None:
                # Label and value are separate OCR lines; infer by order.
                field = "game.winning_pitcher" if not any(row["field"] == "game.winning_pitcher" for row in observations) else "game.losing_pitcher"
            if not any(row["field"] == field for row in observations):
                observations.append(_observation(attachment_id, field, {"game.winning_pitcher": "승리 투수", "game.losing_pitcher": "패전 투수", "game.save_pitcher": "세이브 투수"}[field], {"name": match.group("name").strip(), "wins": int(match.group("w")), "losses": int(match.group("l"))}, text, 0.8))
            continue
        match = _HOME_RUNS.match(text)
        if match:
            numbers = [int(value) for value in re.findall(r"\d+", match.group("numbers"))]
            ascending = numbers == sorted(numbers) and all(b - a == 1 for a, b in zip(numbers, numbers[1:]))
            hitter = match.group("name").strip()
            mine = name_matches(hitter, protagonist_names)
            observations.append(_observation(attachment_id, "game.home_runs", "홈런", {"hitter": hitter, "numbers": numbers, "count": len(numbers), "protagonist": mine}, text, 0.85 if ascending else 0.55, validation="ok" if ascending else "warn", note="" if ascending else "누적 홈런 번호가 연속되지 않습니다.", impact="high" if mine else "normal"))
            if mine:
                observations.append(_observation(attachment_id, "game.protagonist_home_runs", "내 선수 홈런 수", len(numbers), text, 0.85 if ascending else 0.55, validation="ok" if ascending else "warn", impact="high"))
    return observations


_PA_TOKEN = re.compile(r"^(?P<pos>P|C|1B|2B|3B|SS|LF|CF|RF)?(?P<res>내야안타|안타|2루타|3루타|HR|플라이|땅볼|병살타|직선타|삼진|볼넷|사구|희생플라이|희생번트|실책)$")


def parse_batting_log(ocr: dict, attachment_id: str, *, protagonist_names=(), protagonist_team: str | None = None) -> list[dict]:
    words = _words(ocr)
    observations: list[dict] = []
    linescore = parse_linescore(words)
    if linescore:
        for side in ("visitor", "home"):
            row = linescore[side]
            label = "원정" if side == "visitor" else "홈"
            observations.append(_observation(attachment_id, f"game.linescore.{side}", f"{label} 이닝별 득점", row["innings"], json.dumps(row["innings"], ensure_ascii=False), 0.75 if row["consistent"] else 0.45, validation="ok" if row["consistent"] else "warn", note="" if row["consistent"] else "이닝 합계와 R이 일치하지 않습니다."))
            if row["R"] is not None:
                observations.append(_observation(attachment_id, f"game.R.{side}", f"{label} 득점", row["R"], str(row["R"]), 0.75 if row["consistent"] else 0.45, validation="ok" if row["consistent"] else "warn", impact="high"))
    tokens = load_profiles().get("plate_appearance_tokens") or {}
    hits = set(tokens.get("hits") or [])
    no_ab = set(tokens.get("no_at_bat") or [])
    name_word = next((row for row in words if name_matches(row["text"], protagonist_names)), None)
    if name_word is None:
        # Names may be split across words: try joining adjacent words on a line.
        for line in ocr.get("lines") or []:
            if name_matches(str(line.get("text") or ""), protagonist_names) and line.get("words"):
                name_word = dict(line["words"][0])
                break
    if name_word is not None:
        row_y = _center_y(name_word)
        cells = [row for row in words if row["x"] > name_word["x"] + max(60, name_word["w"]) and abs(_center_y(row) - row_y) <= 26]
        plate_appearances = []
        for cell in sorted(cells, key=lambda item: item["x"]):
            match = _PA_TOKEN.match(cell["text"].replace(" ", ""))
            if not match:
                continue
            result = match.group("res")
            plate_appearances.append({"position": match.group("pos"), "result": result, "category": "hit" if result in hits else "no_at_bat" if result in no_ab else "out", "x": cell["x"]})
        if plate_appearances:
            at_bats = sum(1 for row in plate_appearances if row["category"] != "no_at_bat")
            hit_count = sum(1 for row in plate_appearances if row["category"] == "hit")
            hr_count = sum(1 for row in plate_appearances if row["result"] == "HR")
            summary = f"{at_bats}타수 {hit_count}안타" + (f" {hr_count}홈런" if hr_count else "")
            observations.append(_observation(attachment_id, "batting.plate_appearances", "내 선수 타석 결과", [{"position": row["position"], "result": row["result"], "category": row["category"]} for row in plate_appearances], " · ".join(f"{row['position'] or ''}{row['result']}" for row in plate_appearances), 0.8, impact="high"))
            observations.append(_observation(attachment_id, "batting.at_bats", "내 선수 타수", at_bats, summary, 0.75, impact="high"))
            observations.append(_observation(attachment_id, "batting.hits", "내 선수 안타", hit_count, summary, 0.75, impact="high"))
            observations.append(_observation(attachment_id, "batting.home_runs", "내 선수 홈런", hr_count, summary, 0.75, impact="high"))
    else:
        observations.append(_observation(attachment_id, "batting.plate_appearances", "내 선수 타석 결과", None, "선수 이름을 찾지 못함", 0.2, validation="fail", note="화면에서 내 선수의 행을 찾지 못했습니다. 직접 설명으로 알려 주세요.", impact="high"))
    return observations


_STATS_COLUMNS = ("AB", "안타", "2B", "3B", "홈런", "타점", "득점", "삼진", "도루", "타율")
_STATS_KEYS = {"AB": "bat_AB", "안타": "bat_H", "2B": "bat_2B", "3B": "bat_3B", "홈런": "bat_HR", "타점": "bat_RBI", "득점": "bat_R", "삼진": "bat_SO", "도루": "bat_SB", "타율": "bat_AVG"}


def parse_batting_stats(ocr: dict, attachment_id: str, *, protagonist_names=(), protagonist_team: str | None = None) -> list[dict]:
    words = _words(ocr)
    observations: list[dict] = []
    header_words = [row for row in words if row["text"] in _STATS_COLUMNS]
    if not header_words:
        return [_observation(attachment_id, "batting.line", "타격 성적표", None, "표 머리글을 찾지 못함", 0.2, validation="fail", note="야수 성적 표의 머리글을 읽지 못했습니다.")]
    header_y = sorted(_center_y(row) for row in header_words)[len(header_words) // 2]
    columns = {}
    for row in header_words:
        if abs(_center_y(row) - header_y) <= 14 and row["text"] not in columns:
            columns[row["text"]] = _center_x(row)
    candidates = [row for row in words if re.fullmatch(r"[,.'|I]?[A-Z][A-Za-z.'\- ]{2,}", row["text"]) and row["x"] < 600 and _center_y(row) > header_y + 40]
    # A name split into several OCR words ("Paul" "Skenes") is one row.
    name_rows: list[dict] = []
    for row in sorted(candidates, key=lambda item: (_center_y(item), item["x"])):
        if name_rows and abs(_center_y(name_rows[-1]) - _center_y(row)) <= 20 and row["x"] - (name_rows[-1]["x"] + name_rows[-1]["w"]) < 60:
            merged = name_rows[-1]
            merged["text"] = f"{merged['text']} {row['text']}"
            merged["w"] = row["x"] + row["w"] - merged["x"]
            continue
        name_rows.append(dict(row))
    lines_out = []
    for name in name_rows:
        row_y = _center_y(name)
        cells = [row for row in words if row["x"] > name["x"] + name["w"] and abs(_center_y(row) - row_y) <= 26]
        values: dict[str, object] = {}
        average = None
        for cell in cells:
            text = cell["text"].strip()
            avg = re.search(r"\.\d{3}", text)
            if avg and "타율" in columns and abs(_center_x(cell) - columns["타율"]) < 160:
                average = float("0" + avg.group(0))
                continue
            if not re.fullmatch(r"\d{1,3}", text):
                continue
            column = min(columns.items(), key=lambda item: abs(item[1] - _center_x(cell)))
            if abs(column[1] - _center_x(cell)) <= 70 and column[0] != "타율":
                values.setdefault(_STATS_KEYS[column[0]], int(text))
        clean_name = name["text"].strip(" ,.'|I")
        mine = name_matches(clean_name, protagonist_names)
        line = {"name": clean_name, "protagonist": mine, "average": average, **values}
        lines_out.append(line)
        if mine:
            observations.append(_observation(attachment_id, "batting.line", "내 선수 경기 타격 성적", line, json.dumps(line, ensure_ascii=False), 0.65 if values else 0.35, validation="ok" if values else "warn", note="" if values else "숫자 칸을 읽지 못했습니다. 화면과 대조하세요.", impact="high"))
            if average is not None:
                observations.append(_observation(attachment_id, "batting.average", "내 선수 시즌 타율 (화면 표기)", average, f"{average:.3f}", 0.7, impact="normal"))
    if not any(row["field"] == "batting.line" for row in observations):
        observations.append(_observation(attachment_id, "batting.line", "내 선수 경기 타격 성적", None, "내 선수 행을 찾지 못함", 0.2, validation="fail", note="화면에서 내 선수의 행을 찾지 못했습니다.", impact="high"))
    observations.append(_observation(attachment_id, "batting.table", "팀 타격 성적표 (참고)", lines_out, f"{len(lines_out)}명", 0.5, impact="normal"))
    observations[-1]["suggested_decision"] = "session_only"
    return observations


PARSERS = {"game_result": parse_game_result, "batting_log": parse_batting_log, "batting_stats": parse_batting_stats}


# --------------------------------------------------------------------------
# Whole-attachment analysis
# --------------------------------------------------------------------------


def text_signature(ocr: dict) -> set[str]:
    tokens = set()
    for line in _line_texts(ocr):
        for token in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}", line):
            tokens.add(token)
    return tokens


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def analyze_local(
    path: str | os.PathLike,
    *,
    protagonist_names=(),
    protagonist_team: str | None = None,
    protagonist_id: object = None,
    universe_id: str | None = None,
    current_game_date: str | None = None,
    language: str = "ko",
    ocr: dict | None = None,
    kind: str | None = None,
) -> dict:
    """Analyse one image locally. ``ocr`` may be injected (fixtures, tests)."""
    file_path = Path(path)
    data = file_path.read_bytes()
    image_type = sniff_image(data)
    dimensions = image_dimensions(data)
    sha = hashlib.sha256(data).hexdigest()
    attachment_id = sha[:16]
    record = {
        "attachment_id": attachment_id,
        "analysis_version": ANALYSIS_VERSION,
        "file_name": file_path.name,
        "kind": kind or ("upload" if file_path.name.startswith("upload-") else "capture"),
        "bytes": len(data),
        "sha256": sha,
        "image_type": image_type,
        "width": dimensions[0] if dimensions else None,
        "height": dimensions[1] if dimensions else None,
        "metadata_stripped": True,
        "analyzed_at": _now(),
        "llm_used": False,
        "network_used": False,
        "universe_id": universe_id,
        "protagonist_id": str(protagonist_id) if protagonist_id is not None else None,
    }
    if image_type is None and ocr is None:
        record.update({"analysis_path": "unsupported", "screen_type": "unknown", "screen_confidence": 0.0, "observations": [], "needs_user_description": True, "ocr": None, "binding": {"universe_id": universe_id, "protagonist_id": record["protagonist_id"], "game_date": current_game_date, "confidence": 0.0, "date_source": "current_open_date", "requires_confirmation": True}})
        return record
    ocr_payload = ocr
    ocr_error = None
    if ocr_payload is None:
        try:
            ocr_payload = run_local_ocr(file_path, language=language)
        except OcrUnavailable as exc:
            ocr_error = str(exc)
    if ocr_payload is None:
        record.update(
            {
                "analysis_path": "no_local_ocr",
                "parser_profile": None,
                "screen_type": "unknown",
                "screen_confidence": 0.0,
                "observations": [],
                "needs_user_description": True,
                "ocr": None,
                "ocr_error": ocr_error,
                "binding": {"universe_id": universe_id, "protagonist_id": record["protagonist_id"], "game_date": current_game_date, "confidence": 0.3, "date_source": "current_open_date", "requires_confirmation": True},
            }
        )
        return record
    screen = classify_screen(ocr_payload)
    parser = PARSERS.get(screen["screen_type"])
    observations = parser(ocr_payload, attachment_id, protagonist_names=protagonist_names, protagonist_team=protagonist_team) if parser else []
    ocr_date = next((row["value"] for row in observations if row["field"] == "game.date" and row.get("validation") == "ok"), None)
    binding_date = ocr_date or current_game_date
    protagonist_seen = any(name_matches(line, protagonist_names) for line in _line_texts(ocr_payload))
    binding_confidence = 0.5
    if ocr_date and current_game_date and ocr_date == current_game_date:
        binding_confidence = 0.95 if protagonist_seen else 0.8
    elif ocr_date and current_game_date and ocr_date != current_game_date:
        binding_confidence = 0.35
    elif protagonist_seen:
        binding_confidence = 0.7
    binding = {
        "universe_id": universe_id,
        "protagonist_id": record["protagonist_id"],
        "game_date": binding_date,
        "date_source": "screen" if ocr_date else "current_open_date",
        "current_game_date": current_game_date,
        "date_conflict": bool(ocr_date and current_game_date and ocr_date != current_game_date),
        "protagonist_seen": protagonist_seen,
        "confidence": round(binding_confidence, 3),
        "requires_confirmation": binding_confidence < 0.8,
    }
    record.update(
        {
            "analysis_path": "known_screen_parser" if parser else "ocr_only",
            "parser_profile": screen.get("profile_id"),
            "screen_type": screen["screen_type"],
            "screen_label": screen.get("label"),
            "screen_confidence": screen["confidence"],
            "ocr": {"engine": ocr_payload.get("engine", "windows_media_ocr"), "language": ocr_payload.get("engine_language"), "line_count": len(ocr_payload.get("lines") or []), "lines": [str(line.get("text") or "") for line in ocr_payload.get("lines") or []]},
            "text_signature": sorted(text_signature(ocr_payload)),
            "observations": observations,
            "needs_user_description": not parser or not observations,
            "binding": binding,
        }
    )
    return record


def detect_duplicates_and_conflicts(records: list[dict]) -> list[dict]:
    """Mark duplicate images (hash or text similarity) and conflicting dates side by side."""
    rows = [dict(row) for row in records]
    for index, row in enumerate(rows):
        row["duplicates"] = []
        row["conflicts"] = []
        for other_index, other in enumerate(rows):
            if other_index == index:
                continue
            if other.get("sha256") == row.get("sha256"):
                row["duplicates"].append({"attachment_id": other.get("attachment_id"), "reason": "identical_bytes"})
                continue
            score = similarity(set(row.get("text_signature") or []), set(other.get("text_signature") or []))
            if score >= 0.9 and row.get("screen_type") == other.get("screen_type"):
                row["duplicates"].append({"attachment_id": other.get("attachment_id"), "reason": "near_identical_text", "similarity": round(score, 3)})
        dates = {other.get("binding", {}).get("game_date") for other in rows if other.get("binding", {}).get("date_source") == "screen"}
        mine = row.get("binding", {}).get("game_date")
        others = {value for value in dates if value and value != mine}
        if row.get("binding", {}).get("date_source") == "screen" and others:
            row["conflicts"].append({"kind": "date", "this": mine, "others": sorted(others), "note": "날짜가 다른 화면이 섞여 있습니다. 자동으로 합치지 않습니다."})
    return rows


# --------------------------------------------------------------------------
# Review -> proposed application
# --------------------------------------------------------------------------

HIGH_IMPACT_FIELDS = ("game.result", "game.R.home", "game.R.visitor", "game.protagonist_home_runs", "batting.home_runs", "batting.hits", "batting.at_bats", "batting.line", "batting.plate_appearances", "game.home_runs")


def propose_application(record: dict, decisions: dict | None, *, manual_description: str | None = None) -> dict:
    """Turn reviewed observations into proposed writes without committing."""
    decisions = decisions or {}
    proposals = []
    blocked = []
    for observation in record.get("observations") or []:
        decision = str(decisions.get(observation["observation_id"]) or observation.get("suggested_decision") or "ignore")
        if decision not in DECISIONS:
            decision = "ignore"
        row = dict(observation, decision=decision)
        if decision == "ignore":
            continue
        if decision == "user_confirmed" and observation.get("validation") == "fail":
            blocked.append({"observation_id": observation["observation_id"], "reason": "validation_failed", "label": observation.get("label")})
            continue
        proposals.append(
            {
                "observation_id": observation["observation_id"],
                "field": observation["field"],
                "label": observation.get("label"),
                "value": observation.get("value"),
                "decision": decision,
                "evidence_class": "user_confirmed" if decision == "user_confirmed" else "fictional_intervention" if decision == "story_prop" else None,
                "target": "fact_registry" if decision == "user_confirmed" else "narrative_prop" if decision == "story_prop" else "conversation_only",
                "impact": observation.get("impact"),
                "validation": observation.get("validation"),
            }
        )
    description = str(manual_description or "").strip()
    if description:
        proposals.append(
            {
                "observation_id": _stable(record.get("attachment_id"), "manual", description),
                "field": "manual.description",
                "label": "직접 설명",
                "value": description,
                "decision": str(decisions.get("manual") or "story_prop"),
                "evidence_class": "user_confirmed" if decisions.get("manual") == "user_confirmed" else "fictional_intervention",
                "target": "fact_registry" if decisions.get("manual") == "user_confirmed" else "narrative_prop",
                "impact": "normal",
                "validation": "ok",
            }
        )
    binding = dict(record.get("binding") or {})
    return {
        "attachment_id": record.get("attachment_id"),
        "binding": binding,
        "proposals": proposals,
        "blocked": blocked,
        "requires_binding_confirmation": bool(binding.get("requires_confirmation")) or bool(binding.get("date_conflict")),
        "summary": {"facts": sum(1 for row in proposals if row["target"] == "fact_registry"), "props": sum(1 for row in proposals if row["target"] == "narrative_prop"), "session_only": sum(1 for row in proposals if row["target"] == "conversation_only"), "blocked": len(blocked)},
    }
