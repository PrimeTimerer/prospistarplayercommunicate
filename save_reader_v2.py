#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified offline reader for Pro-Yakyuu-Spirits StarPlayer saves.

Only save-file facts are returned as verified facts. Schedule/result fields that
have not been independently decoded are intentionally reported as unavailable.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import struct
import time
import zlib
from pathlib import Path
from typing import Iterable

_HM = b"5A8CA36B895641B16D03BAEDB582BE5A"
_KEY_MATERIAL = hashlib.sha256(_HM).digest()
_AESKEY = _KEY_MATERIAL[:16]
_HEADER_SIZE = 0x448
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_PLAIN_BYTES = 512 * 1024 * 1024

# Prospi 2026 StarPlayer keeps one fixed-width season summary for every career
# year. The layout is validated against its zero sentinels, the empty next
# slot, internal baseball equations, and the independently located live-season
# counters before historical rows are exposed.
_CAREER_SUMMARY_BASE = 0x11EA0
_CAREER_SUMMARY_STRIDE = 0x310
_CAREER_SUMMARY_DATA_SIZE = 0x190
_MAX_CAREER_YEARS = 30

# Identifier of the plaintext layout this parser understands. It is reported
# in every snapshot's validation block so the Diagnostics view can show which
# layout produced the facts, and so a future game update that shifts the
# layout is visible as a signature change rather than a silent parse failure.
FORMAT_SIGNATURE_ID = "prospi2026-starplayer-plain-v1"


def format_signature(parsed_header: dict, identities: list[dict], career_history: dict) -> dict:
    kinds = sorted({f"0x{int(row.get('record_kind') or 0):02X}" for row in identities})
    return {
        "id": FORMAT_SIGNATURE_ID,
        "header_size": f"0x{_HEADER_SIZE:X}",
        "header_line_recognized": bool(parsed_header.get("year")),
        "career_summary_base": f"0x{_CAREER_SUMMARY_BASE:X}",
        "career_summary_stride": f"0x{_CAREER_SUMMARY_STRIDE:X}",
        "career_summary_status": career_history.get("status", "unavailable"),
        "profile_record_kinds": kinds,
    }


class SaveReadError(RuntimeError):
    """Base class for actionable save-read failures."""


class SaveBusyError(SaveReadError):
    """Raised when the game is still changing the save during a read."""


class SaveIntegrityError(SaveReadError):
    """Raised when chunk layout, HMAC, padding, or compression is invalid."""


class SnapshotParseError(SaveReadError):
    """Raised when a verified save cannot be mapped to one player snapshot."""


def _aes_cbc(iv: bytes, ciphertext: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES

        return AES.new(_AESKEY, AES.MODE_CBC, iv).decrypt(ciphertext)
    except Exception:
        from aes_pure import cbc_decrypt

        return cbc_decrypt(_AESKEY, iv, ciphertext)


def _inflate_strict(payload: bytes) -> bytes:
    failures: list[str] = []
    for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            stream = zlib.decompressobj(window_bits)
            result = stream.decompress(payload, _MAX_PLAIN_BYTES)
            result += stream.flush()
            if not stream.eof:
                raise SaveIntegrityError("compressed stream ended early")
            if stream.unused_data or stream.unconsumed_tail:
                raise SaveIntegrityError("compressed stream contains trailing data")
            return result
        except (zlib.error, SaveIntegrityError) as exc:
            failures.append(str(exc))
    raise SaveIntegrityError("zlib validation failed: " + "; ".join(failures[-2:]))


def _inflate(payload: bytes) -> bytes:
    """Compatibility wrapper retained for diagnostic callers and tests."""
    return _inflate_strict(payload)


def _signature(path: str) -> tuple[int, int]:
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def read_stable_bytes(path: str, attempts: int = 5, gap: float = 0.18) -> bytes:
    """Read a save only after size and mtime remain unchanged around the read."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            before = _signature(path)
            if before[0] < _HEADER_SIZE:
                raise SaveIntegrityError("save is smaller than its plaintext header")
            if before[0] > _MAX_FILE_BYTES:
                raise SaveIntegrityError("save exceeds the supported safety limit")
            with open(path, "rb") as handle:
                data = handle.read()
            after = _signature(path)
            if before == after and len(data) == after[0]:
                return data
        except (OSError, SaveIntegrityError) as exc:
            last_error = exc
            if isinstance(exc, SaveIntegrityError):
                raise
        if attempt + 1 < attempts:
            time.sleep(gap)
    if last_error:
        raise SaveBusyError(f"save could not be read stably: {last_error}") from last_error
    raise SaveBusyError("save is still being written; retry after autosave completes")


def _validated_chunks(data: bytes) -> list[tuple[int, int, int, bytes, bytes]]:
    """Return ``(offset, packed_size, data_size, iv, ciphertext)`` records."""
    chunks: list[tuple[int, int, int, bytes, bytes]] = []
    offset = _HEADER_SIZE
    size = len(data)
    while offset < size:
        if offset + 8 > size:
            raise SaveIntegrityError(f"truncated chunk header at 0x{offset:X}")
        packed_size, data_size = struct.unpack_from("<II", data, offset)
        if packed_size < 64 or packed_size % 16:
            raise SaveIntegrityError(f"invalid packed chunk size at 0x{offset:X}")
        end = offset + 8 + packed_size
        if end > size:
            raise SaveIntegrityError(f"chunk exceeds file boundary at 0x{offset:X}")
        body = data[offset + 8 : end]
        iv, expected_tag, ciphertext = body[:16], body[16:48], body[48:]
        if not ciphertext or len(ciphertext) % 16:
            raise SaveIntegrityError(f"invalid AES-CBC length at 0x{offset:X}")
        padding_size = len(ciphertext) - data_size
        if not 1 <= padding_size <= 16:
            raise SaveIntegrityError(f"chunk data length is not AES aligned at 0x{offset:X}")
        actual_tag = hmac.new(_KEY_MATERIAL, ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_tag, actual_tag):
            raise SaveIntegrityError(f"chunk HMAC mismatch at 0x{offset:X}")
        chunks.append((offset, packed_size, data_size, iv, ciphertext))
        offset = end
    if not chunks:
        raise SaveIntegrityError("save contains no encrypted chunks")
    if offset != size:
        raise SaveIntegrityError("save contains unparsed trailing bytes")
    return chunks


def decrypt_save(
    path: str,
    need_plain: int | None = None,
    *,
    with_diagnostics: bool = False,
):
    """Verify the complete container and decrypt the requested plaintext prefix.

    ``need_plain=None`` decrypts all chunks and is the correctness-first default.
    The historical three-value return shape is preserved unless diagnostics are
    explicitly requested.
    """
    data = read_stable_bytes(path)
    header = data[:_HEADER_SIZE]
    chunks = _validated_chunks(data)
    plain = bytearray()
    decrypted_chunks = 0
    for offset, _packed_size, data_size, iv, ciphertext in chunks:
        if need_plain is not None and len(plain) >= need_plain:
            break
        padded = _aes_cbc(iv, ciphertext)
        pad = len(padded) - data_size
        if not 1 <= pad <= 16 or padded[data_size:] != b"\x00" * pad:
            raise SaveIntegrityError(f"invalid AES zero padding at 0x{offset:X}")
        if len(padded) - pad != data_size:
            raise SaveIntegrityError(f"chunk plaintext size mismatch at 0x{offset:X}")
        block = _inflate_strict(padded[:data_size])
        if len(plain) + len(block) > _MAX_PLAIN_BYTES:
            raise SaveIntegrityError("decompressed save exceeds the supported safety limit")
        plain.extend(block)
        decrypted_chunks += 1
    diagnostics = {
        "stable_read": True,
        "container": "verified",
        "hmac": "verified",
        "padding": "zero-padding-verified",
        "compression": "verified",
        "file_bytes": len(data),
        "chunk_count": len(chunks),
        "verified_chunks": len(chunks),
        "decrypted_chunks": decrypted_chunks,
        "plain_bytes": len(plain),
    }
    result = (header, bytes(plain), len(data))
    return (*result, diagnostics) if with_diagnostics else result


def _decode_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").replace("\ufffd", "")
    text = "".join(character if character >= " " else " " for character in text)
    return " ".join(text.split())


def _cstr(buffer: bytes, position: int, limit: int = 256) -> str:
    if position < 0 or position >= len(buffer):
        return ""
    end = buffer.find(b"\x00", position, min(len(buffer), position + limit))
    if end < 0:
        end = min(len(buffer), position + limit)
    return _decode_text(buffer[position:end])


def parse_header(header: bytes) -> dict:
    output: dict = {}
    line = _cstr(header, 72)
    output["header_line"] = line
    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+\((\d+)\D*\)", line)
    if match:
        output["year"], output["month"], output["day"], output["career_year"] = map(
            int, match.groups()
        )
        output["team"] = line[: match.start()].strip()
    output["star_name_raw"] = _cstr(header, 584)
    return output


def _u16(buffer: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(buffer):
        return 0
    return struct.unpack_from("<H", buffer, offset)[0]


def _u32(buffer: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(buffer):
        return 0
    return struct.unpack_from("<I", buffer, offset)[0]


def _season_summary_stats(blob: bytes, base: int) -> dict:
    """Decode one structurally validated StarPlayer season-summary record."""
    innings = _u16(blob, base)
    inning_remainder = _u16(blob, base + 0x02)
    if inning_remainder not in (0, 1, 2):
        inning_remainder = 0

    singles = _u16(blob, base + 0x84)
    doubles = _u16(blob, base + 0x7A)
    triples = _u16(blob, base + 0x8A)
    home_runs = _u16(blob, base + 0x7C)
    hits = singles + doubles + triples + home_runs
    plate_appearances = _u16(blob, base + 0x88)
    walks = _u16(blob, base + 0x86)
    hit_by_pitch = _u16(blob, base + 0x96)
    sacrifice_hits = _u16(blob, base + 0x82)
    sacrifice_flies = _u16(blob, base + 0x8C)
    at_bats = plate_appearances - walks - hit_by_pitch - sacrifice_hits - sacrifice_flies

    stats: dict[str, int | str | float | None] = {
        "pit_G": _u16(blob, base + 0x50),
        "pit_IP": (
            float(f"{innings}.{inning_remainder}") if inning_remainder else innings
        ),
        "pit_TBF": _u16(blob, base + 0x48),
        "pit_H": _u16(blob, base + 0x3C),
        "pit_K": _u16(blob, base + 0x4A),
        "pit_W": _u16(blob, base + 0x54),
        "pit_L": _u16(blob, base + 0x56),
        "pit_SV": _u16(blob, base + 0x58),
        "pit_HP": _u16(blob, base + 0x5A),
        "pit_HLD": _u16(blob, base + 0x5C),
        "pit_CG": _u16(blob, base + 0x5E),
        "pit_SHO": _u16(blob, base + 0x60),
        "pit_BB": _u16(blob, base + 0x64),
        "pit_HBP": _u16(blob, base + 0x66),
        "pit_R": _u16(blob, base + 0x46),
        "pit_ER": _u16(blob, base + 0x40),
        "bat_G": _u16(blob, base + 0x70),
        "bat_PA": plate_appearances,
        "bat_AB": max(0, at_bats),
        "bat_H": hits,
        "bat_1B": singles,
        "bat_2B": doubles,
        "bat_3B": triples,
        "bat_HR": home_runs,
        "bat_RBI": _u16(blob, base + 0x78),
        "bat_R": _u16(blob, base + 0x7E),
        "bat_SO": _u16(blob, base + 0x80),
        "bat_SH": sacrifice_hits,
        "bat_SF": sacrifice_flies,
        "bat_BB": walks,
        "bat_HBP": hit_by_pitch,
        "bat_SB": _u16(blob, base + 0x8E),
    }
    stats["bat_AVG"] = round(hits / at_bats, 3) if at_bats else None
    obp_denominator = at_bats + walks + hit_by_pitch + sacrifice_flies
    stats["bat_OBP"] = (
        round((hits + walks + hit_by_pitch) / obp_denominator, 3)
        if obp_denominator
        else None
    )
    total_bases = singles + doubles * 2 + triples * 3 + home_runs * 4
    stats["bat_SLG"] = round(total_bases / at_bats, 3) if at_bats else None
    stats["bat_OPS"] = (
        round(float(stats["bat_OBP"]) + float(stats["bat_SLG"]), 3)
        if stats["bat_OBP"] is not None and stats["bat_SLG"] is not None
        else None
    )
    outs = innings * 3 + inning_remainder
    stats["pit_ERA"] = (
        round(int(stats["pit_ER"]) * 27 / outs, 2) if outs else None
    )
    return stats


def _season_summary_valid(stats: dict, *, allow_empty: bool = False) -> bool:
    """Reject coincidental bytes before publishing them as season history."""
    integer_keys = (
        "pit_G", "pit_TBF", "pit_H", "pit_K", "pit_W", "pit_L", "pit_SV",
        "pit_HP", "pit_HLD", "pit_CG", "pit_SHO", "pit_BB", "pit_HBP",
        "pit_R", "pit_ER", "bat_G", "bat_PA", "bat_AB", "bat_H", "bat_1B",
        "bat_2B", "bat_3B", "bat_HR", "bat_RBI", "bat_R", "bat_SO",
        "bat_SH", "bat_SF", "bat_BB", "bat_HBP", "bat_SB",
    )
    if any(not isinstance(stats.get(key), int) or int(stats[key]) < 0 for key in integer_keys):
        return False
    if int(stats["pit_G"]) > 400 or int(stats["bat_G"]) > 400:
        return False
    if int(stats["pit_TBF"]) > 5000 or int(stats["pit_K"]) > int(stats["pit_TBF"]):
        return False
    if int(stats["pit_W"]) > int(stats["pit_G"]) or int(stats["pit_L"]) > int(stats["pit_G"]):
        return False
    if int(stats["pit_CG"]) > int(stats["pit_G"]) or int(stats["pit_SHO"]) > int(stats["pit_CG"]):
        return False
    if int(stats["pit_ER"]) > int(stats["pit_R"]):
        return False
    if int(stats["bat_PA"]) > 2500 or int(stats["bat_AB"]) > int(stats["bat_PA"]):
        return False
    if int(stats["bat_H"]) > int(stats["bat_AB"]) or int(stats["bat_HR"]) > int(stats["bat_H"]):
        return False
    innings_text = str(stats.get("pit_IP") or "0")
    try:
        if float(innings_text) > 1000:
            return False
    except ValueError:
        return False
    core = sum(
        int(stats.get(key) or 0)
        for key in ("pit_TBF", "pit_K", "pit_W", "bat_PA", "bat_H", "bat_HR")
    )
    return allow_empty or core > 0


def _season_summary_history(blob: bytes, date: dict, live_stats: dict) -> dict:
    """Return current and prior season summaries only after layout validation."""
    career_year = int(date.get("career_year") or 0)
    current_year = int(date.get("year") or 0)
    result = {
        "format": "prospi2026-season-summary-v1",
        "status": "unavailable",
        "seasons": [],
        "current": None,
        "awards": {
            "status": "not_structurally_decoded",
            "honors": [],
            "note": "수상 열거 구조는 시즌 성적 구조와 별도이므로 검증 전에는 이름을 단정하지 않습니다.",
        },
    }
    if not (1 <= career_year <= _MAX_CAREER_YEARS):
        return result
    current_base = _CAREER_SUMMARY_BASE + (career_year - 1) * _CAREER_SUMMARY_STRIDE
    next_base = current_base + _CAREER_SUMMARY_STRIDE
    if next_base + _CAREER_SUMMARY_DATA_SIZE > len(blob):
        return result
    if any(blob[_CAREER_SUMMARY_BASE - 0x10 : _CAREER_SUMMARY_BASE]):
        return result
    if any(blob[next_base : next_base + _CAREER_SUMMARY_DATA_SIZE]):
        return result

    current_stats = _season_summary_stats(blob, current_base)
    if not _season_summary_valid(current_stats, allow_empty=True):
        return result
    history: list[dict] = []
    for index in range(career_year - 1):
        base = _CAREER_SUMMARY_BASE + index * _CAREER_SUMMARY_STRIDE
        stats = _season_summary_stats(blob, base)
        if not _season_summary_valid(stats):
            return result
        history.append(
            {
                "season_year": current_year - (career_year - 1 - index),
                "career_year": index + 1,
                "stats": stats,
                "provenance": "save_verified_historical_season",
                "date_precision": "season_year",
            }
        )

    compared = (
        "pit_IP", "pit_TBF", "pit_K", "pit_W", "bat_AB", "bat_H",
        "bat_HR", "bat_RBI", "bat_R", "bat_SO", "bat_SB",
    )
    comparisons = [key for key in compared if key in live_stats and live_stats.get(key) is not None]

    def _same(key: str) -> bool:
        live_value = live_stats.get(key)
        current_value = current_stats.get(key)
        if key == "pit_IP":
            # The live log stores whole innings only; the season summary keeps
            # the X.1 / X.2 remainder. Compare the whole-inning part.
            try:
                return int(float(str(live_value))) == int(float(str(current_value)))
            except (TypeError, ValueError):
                return False
        return str(live_value) == str(current_value)

    matches = sum(_same(key) for key in comparisons)
    corroborated = not comparisons or matches >= max(2, (len(comparisons) + 1) // 2)
    result.update(
        {
            "status": "verified" if corroborated else "verified_layout_current_log_differs",
            "seasons": history,
            "current": {
                "season_year": current_year,
                "career_year": career_year,
                "stats": current_stats,
                "provenance": "save_verified_season_summary",
            },
            "validation": {
                "status": "verified" if corroborated else "verified_layout_current_log_differs",
                "array_offset": f"0x{_CAREER_SUMMARY_BASE:X}",
                "record_stride": f"0x{_CAREER_SUMMARY_STRIDE:X}",
                "previous_records": len(history),
                "current_comparisons": len(comparisons),
                "current_matches": matches,
                "empty_next_record": True,
            },
        }
    )
    return result


def _identity_parts(raw: bytes) -> list[str]:
    parts: list[str] = []
    for value in raw.split(b"\x00"):
        if not value:
            continue
        text = _decode_text(value)
        if 1 <= len(text) <= 48 and any(ch.isalnum() for ch in text):
            parts.append(text)
    return parts[:3]


def _identities(blob: bytes) -> list[dict]:
    records: list[dict] = []
    # 0x25 is the standard profile record and 0x1B is the custom/imported
    # StarPlayer profile shape. Their validated identity fields are otherwise
    # laid out identically for the metadata used here.
    marker = rb"[\x1B\x25]\x00\x00\x00\x00\x00\x00\x00"
    for match in re.finditer(marker, blob):
        base = match.start()
        if base + 0x54 > len(blob):
            continue
        player_id = struct.unpack_from("<I", blob, base + 8)[0]
        parts = _identity_parts(blob[base + 0x24 : base + 0x54])
        if not (0 < player_id < 0x400000 and parts):
            continue
        raw_profile = blob[base : min(len(blob), base + 0x80)]
        records.append(
            {
                "off": base,
                "record_kind": blob[base],
                "id": player_id,
                "parts": parts,
                "ability": list(blob[base + 0x14 : base + 0x1A]),
                "fingerprint": hashlib.sha256(raw_profile).hexdigest()[:16],
            }
        )
    return records


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _identity_score(record: dict, header_name: str) -> tuple[int, int]:
    normalized_header = _normalize_name(header_name)
    parts = [_normalize_name(part) for part in record.get("parts", [])]
    score = 0
    if normalized_header and any(part and part in normalized_header for part in parts):
        score += 100
    if record["id"] >= 0x1000:
        score += 10
    score += min(6, len(record.get("parts", [])) * 2)
    return score, -record["off"]


def _select_identity(records: list[dict], header_name: str) -> tuple[dict, str]:
    if not records:
        raise SnapshotParseError("선수 프로필 레코드를 찾지 못했습니다.")
    ranked = sorted(records, key=lambda row: _identity_score(row, header_name), reverse=True)
    selected = ranked[0]
    matched = _identity_score(selected, header_name)[0] >= 100
    method = "header-name" if matched else "primary-profile-fallback"
    return selected, method


def _player_name(record: dict) -> str:
    parts = record.get("parts", [])
    if not parts:
        return ""
    return f"{parts[1]} {parts[0]}" if len(parts) >= 2 else parts[0]


def _header_player_name(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", value or "").strip()


def _profile_birth_year(blob: bytes, records: list[dict], selected: dict, game_year: int) -> int | None:
    """Return the repeated save-profile birth year only when copies agree."""
    selected_parts = tuple(_normalize_name(value) for value in selected.get("parts", []))
    years: list[int] = []
    for record in records:
        if record.get("id") != selected.get("id"):
            continue
        parts = tuple(_normalize_name(value) for value in record.get("parts", []))
        if parts != selected_parts:
            continue
        # The serialized profile stores this field 0x120 bytes after player ID.
        # Multiple copies are required to agree before it is exposed as identity data.
        value = _u16(blob, int(record["off"]) + 8 + 0x120)
        if value:
            years.append(value)
    if not years or len(set(years)) != 1:
        return None
    birth_year = years[0]
    age = game_year - birth_year
    if not (1900 <= birth_year <= game_year and 15 <= age <= 80):
        return None
    return birth_year


def _season_age(game_year: int, birth_year: int | None) -> int | None:
    if birth_year is None:
        return None
    age = game_year - birth_year
    return age if 15 <= age <= 80 else None


def read_identity_summary(path: str) -> dict:
    """Read a fully verified save and return settings-list identity metadata."""
    header, blob, cipher_size, diagnostics = decrypt_save(path, with_diagnostics=True)
    parsed_header = parse_header(header)
    date = _safe_date(parsed_header)
    identities = _identities(blob)
    star, identity_method = _select_identity(
        identities, parsed_header.get("star_name_raw", "")
    )
    birth_year = _profile_birth_year(blob, identities, star, int(date["year"]))
    age = _season_age(int(date["year"]), birth_year)
    player_name = _player_name(star)
    header_name = _header_player_name(parsed_header.get("star_name_raw", ""))
    if identity_method != "header-name" and header_name:
        player_name = header_name
    return {
        "player": {
            "id": star["id"],
            "name": player_name,
            "display_name": parsed_header.get("star_name_raw", ""),
            "team": parsed_header.get("team", "?"),
            "birth_year": birth_year,
            "age": age,
            "age_basis": "game_year_minus_save_profile_birth_year" if age is not None else "unavailable",
        },
        "date": date,
        "source": {"file": Path(path).name, "slot": Path(path).parent.name},
        "cipher_size": cipher_size,
        "validation": {
            **diagnostics,
            "identity_method": identity_method,
            "identity_candidates": len(identities),
        },
        "provenance": {
            "identity": "save_verified",
            "team": "save_verified",
            "age": "derived_from_save_verified_profile_and_game_year"
            if age is not None
            else "unavailable",
        },
    }


def _twoway_sig(buffer: bytes, offset: int) -> bool:
    if offset < 0x48 or offset + 0x50 >= len(buffer):
        return False
    batters, strikeouts = _u16(buffer, offset), _u16(buffer, offset + 2)
    if not (50 < batters < 4000 and 0 < strikeouts < 2000):
        return False
    values = (
        _u16(buffer, offset + 0x30),
        _u16(buffer, offset + 0x34),
        _u16(buffer, offset + 0x36),
        _u16(buffer, offset + 0x38),
    )
    return all(0 < value < 1000 for value in values) and _u16(buffer, offset + 0x32) == 16


def _pid_offsets(blob: bytes, player_id: int) -> Iterable[int]:
    key = struct.pack("<I", player_id)
    start = 0
    while True:
        found = blob.find(key, start)
        if found < 0:
            return
        yield found
        start = found + 1


def _find_anchors(blob: bytes, player_id: int) -> list[int]:
    anchors: list[int] = []
    for player_offset in _pid_offsets(blob, player_id):
        begin = max(0, player_offset - 0x100)
        end = min(len(blob) - 0x50, player_offset + 0x900)
        for candidate in range(begin, end, 2):
            if _twoway_sig(blob, candidate) and candidate not in anchors:
                anchors.append(candidate)
    return anchors


def _find_anchor(blob: bytes, player_id: int) -> int | None:
    anchors = _find_anchors(blob, player_id)
    return anchors[0] if anchors else None


def _find_bat_headers(blob: bytes, player_id: int) -> list[tuple[int, int, int]]:
    candidates: list[tuple[int, int, int]] = []
    for player_offset in _pid_offsets(blob, player_id):
        if player_offset < 0x14:
            continue
        at_bats = _u16(blob, player_offset - 0x14)
        hits = _u16(blob, player_offset - 0x12)
        if 0 < hits <= at_bats < 2000:
            candidates.append((player_offset - 0x14, at_bats, hits))
    return candidates


def _find_bat_header(blob: bytes, player_id: int) -> int | None:
    candidates = _find_bat_headers(blob, player_id)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[1], item[2], item[0]))[0]


def _safe_date(header: dict) -> dict:
    date = {
        "year": header.get("year"),
        "month": header.get("month"),
        "day": header.get("day"),
        "career_year": header.get("career_year"),
    }
    year, month, day = date["year"], date["month"], date["day"]
    if not (year and 2000 <= year <= 2200 and month and 1 <= month <= 12 and day and 1 <= day <= 31):
        raise SnapshotParseError("세이브 헤더 날짜를 검증하지 못했습니다.")
    return date


def read_snapshot(path: str) -> dict:
    """Return one provenance-tagged, save-verified snapshot."""
    header, blob, cipher_size, diagnostics = decrypt_save(path, with_diagnostics=True)
    parsed_header = parse_header(header)
    date = _safe_date(parsed_header)
    identities = _identities(blob)
    star, identity_method = _select_identity(identities, parsed_header.get("star_name_raw", ""))
    player_id = star["id"]
    anchors = _find_anchors(blob, player_id)
    bat_candidates = _find_bat_headers(blob, player_id)
    anchor = anchors[0] if anchors else None
    bat = max(bat_candidates, key=lambda item: (item[1], item[2], item[0]))[0] if bat_candidates else None

    warnings: list[str] = []
    if len(anchors) > 1:
        warnings.append("복수 투구 누적 후보가 있어 첫 구조 일치 후보를 사용했습니다.")
    if identity_method != "header-name":
        warnings.append("표시명/별명 불일치로 기본 선수 프로필을 후속 기록과 함께 사용했습니다.")

    stats: dict[str, int | float] = {}
    if anchor is not None:
        stats.update(
            {
                "pit_IP": _u16(blob, anchor - 0x48),
                "pit_TBF": _u16(blob, anchor),
                "pit_H": _u16(blob, anchor - 0x06),
                "pit_K": _u16(blob, anchor + 0x02),
                "pit_W": _u16(blob, anchor + 0x08),
                "bat_HR": _u16(blob, anchor + 0x34),
                "bat_RBI": _u16(blob, anchor + 0x30),
                "bat_R": _u16(blob, anchor + 0x36),
                "bat_SO": _u16(blob, anchor + 0x38),
                "bat_SB": _u16(blob, anchor + 0x46),
            }
        )
    if bat is not None:
        stats["bat_AB"] = _u16(blob, bat)
        stats["bat_H"] = _u16(blob, bat + 2)
    if stats.get("bat_AB"):
        stats["bat_AVG"] = round(int(stats.get("bat_H", 0)) / int(stats["bat_AB"]), 3)

    career_history = _season_summary_history(blob, date, stats)
    summary_current = career_history.get("current") or {}
    summary_stats = summary_current.get("stats") if isinstance(summary_current, dict) else None
    if isinstance(summary_stats, dict):
        if career_history.get("status") == "verified_layout_current_log_differs":
            warnings.append(
                "시즌 요약 원장과 경기별 누적 로그가 달라 더 완결된 시즌 요약 원장을 사용했습니다."
            )
        stats = dict(summary_stats)
    elif anchor is None and bat is None:
        raise SnapshotParseError("검증된 선수의 누적 기록 레코드를 찾지 못했습니다.")

    name_parts = star["parts"]
    player_name = _player_name(star)
    header_name = _header_player_name(parsed_header.get("star_name_raw", ""))
    if identity_method != "header-name" and header_name:
        player_name = header_name
    birth_year = _profile_birth_year(blob, identities, star, int(date["year"]))
    age = _season_age(int(date["year"]), birth_year)
    slot = Path(path).parent.name
    player = {
        "id": player_id,
        "name": player_name,
        "display_name": parsed_header.get("star_name_raw", ""),
        "name_parts": name_parts,
        "team": parsed_header.get("team", "?"),
        "pos": parsed_header.get("star_name_raw", ""),
        "ability": star["ability"],
        "birth_year": birth_year,
        "age": age,
        "age_basis": "game_year_minus_save_profile_birth_year" if age is not None else "unavailable",
    }
    snapshot = {
        "schema_version": 2,
        "player": player,
        "date": date,
        "stats": stats,
        "career_history": career_history,
        "cipher_size": cipher_size,
        "profile_fingerprint": star["fingerprint"],
        "source": {"file": Path(path).name, "slot": slot},
        "provenance": {
            "identity": "save_verified",
            "team": "save_verified",
            "age": "derived_from_save_verified_profile_and_game_year"
            if age is not None
            else "unavailable",
            "date": "save_verified",
            "season_stats": "save_verified",
            "historical_seasons": (
                "save_verified_historical_season"
                if career_history.get("status", "").startswith("verified")
                else "unavailable"
            ),
            "historical_awards": "not_structurally_decoded",
            "game_delta": "derived_from_verified_snapshots",
            "opponent": "unavailable",
            "score": "unavailable",
            "home_away": "unavailable",
            "team_result": "unavailable",
            "visual_context": "unverified_hint_only",
        },
        "validation": {
            **diagnostics,
            "identity_method": identity_method,
            "identity_candidates": len(identities),
            "pitching_candidates": len(anchors),
            "batting_candidates": len(bat_candidates),
            "career_summary": career_history.get("validation") or {},
            "format_signature": format_signature(parsed_header, identities, career_history),
            "warnings": warnings,
        },
    }
    fact_payload = {
        "player_id": player_id,
        "profile_fingerprint": star["fingerprint"],
        "date": snapshot["date"],
        "stats": sorted(stats.items()),
        "historical_seasons": [
            (row.get("season_year"), sorted((row.get("stats") or {}).items()))
            for row in career_history.get("seasons") or []
        ],
    }
    snapshot["content_hash"] = hashlib.sha256(
        repr(fact_payload).encode("utf-8")
    ).hexdigest()[:20]
    return snapshot


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: save_reader_v2.py <StarPlayer.dat>")
    print(json.dumps(read_snapshot(sys.argv[1]), ensure_ascii=False, indent=2))
