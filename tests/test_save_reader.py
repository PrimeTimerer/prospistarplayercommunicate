#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import config
import save_reader_v2 as reader


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def build_save(payload: bytes, header: bytes | None = None) -> bytes:
    try:
        from Crypto.Cipher import AES
    except Exception as exc:  # pragma: no cover
        raise unittest.SkipTest("pycryptodome is required to build the fixture") from exc
    compressed = zlib.compress(payload)
    padding = 16 - (len(compressed) % 16)
    padded = compressed + b"\x00" * padding
    iv = bytes(range(16))
    ciphertext = AES.new(reader._AESKEY, AES.MODE_CBC, iv).encrypt(padded)
    tag = hmac.new(reader._KEY_MATERIAL, ciphertext, hashlib.sha256).digest()
    body = iv + tag + ciphertext
    if header is None:
        header = bytes(reader._HEADER_SIZE)
    assert len(header) == reader._HEADER_SIZE
    return bytes(header) + struct.pack("<II", len(body), len(compressed)) + body


class CommittedFixtureTests(unittest.TestCase):
    """Parse the synthetic layout constructed from zero by build_test_fixture.py.

    No user save, extracted season history, or real profile is distributed.
    """

    @classmethod
    def setUpClass(cls):
        import json

        blob_path = FIXTURE_DIR / "starplayer-plain.bin"
        sidecar_path = FIXTURE_DIR / "starplayer-plain.json"
        if not blob_path.is_file() or not sidecar_path.is_file():
            raise unittest.SkipTest("committed fixture is missing")
        raw = blob_path.read_bytes()
        cls.header = raw[: reader._HEADER_SIZE]
        cls.plain = raw[reader._HEADER_SIZE :]
        cls.expected = json.loads(sidecar_path.read_text(encoding="utf-8"))

    def _write(self, temp: str) -> Path:
        path = Path(temp) / "00" / "StarPlayer.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_save(self.plain, self.header))
        return path

    def test_snapshot_reproduces_recorded_facts(self):
        expected = self.expected
        with tempfile.TemporaryDirectory() as temp:
            snapshot = reader.read_snapshot(str(self._write(temp)))
        self.assertEqual(expected["player_name"], snapshot["player"]["name"])
        self.assertEqual(expected["team"], snapshot["player"]["team"])
        self.assertEqual(expected["player_id"], snapshot["player"]["id"])
        self.assertEqual(expected["birth_year"], snapshot["player"]["birth_year"])
        self.assertEqual(expected["date"], snapshot["date"])
        for key, value in expected["current_stats"].items():
            self.assertEqual(value, snapshot["stats"][key], key)
        history = snapshot["career_history"]
        self.assertEqual("verified", history["status"])
        self.assertEqual(len(expected["previous_seasons"]), len(history["seasons"]))
        for row, season in zip(expected["previous_seasons"], history["seasons"]):
            self.assertEqual(row["career_year"], season["career_year"])
            self.assertEqual(row["pit_K"], season["stats"]["pit_K"])
            self.assertEqual(row["bat_HR"], season["stats"]["bat_HR"])
        validation = snapshot["validation"]
        self.assertEqual("verified", validation["container"])
        self.assertEqual("header-name", validation["identity_method"])
        signature = validation["format_signature"]
        self.assertEqual(reader.FORMAT_SIGNATURE_ID, signature["id"])
        self.assertTrue(signature["header_line_recognized"])
        self.assertEqual("0x11EA0", signature["career_summary_base"])
        self.assertEqual("verified", signature["career_summary_status"])
        self.assertEqual("save_verified", snapshot["provenance"]["season_stats"])
        self.assertEqual("unavailable", snapshot["provenance"]["opponent"])

    def test_identity_summary_matches_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            summary = reader.read_identity_summary(str(self._write(temp)))
        self.assertEqual(self.expected["player_name"], summary["player"]["name"])
        self.assertEqual(self.expected["team"], summary["player"]["team"])
        self.assertEqual(self.expected["birth_year"], summary["player"]["birth_year"])
        self.assertEqual("header-name", summary["validation"]["identity_method"])

    def test_shifted_layout_is_reported_not_guessed(self):
        # Move the career array by one stride: the parser must refuse the
        # rows instead of reading a neighbouring record as this season.
        shifted = bytearray(len(self.plain) + reader._CAREER_SUMMARY_STRIDE)
        shifted[reader._CAREER_SUMMARY_STRIDE :] = self.plain
        shifted[: reader._CAREER_SUMMARY_STRIDE] = b"\x00" * reader._CAREER_SUMMARY_STRIDE
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "00" / "StarPlayer.dat"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_save(bytes(shifted), self.header))
            with self.assertRaises(reader.SnapshotParseError):
                reader.read_snapshot(str(path))


class ContainerValidationTests(unittest.TestCase):
    def test_verified_chunk_round_trip(self):
        payload = (b"verified-save-payload" * 4000) + b"!"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "StarPlayer.dat"
            path.write_bytes(build_save(payload))
            header, plain, size, diagnostics = reader.decrypt_save(
                str(path), with_diagnostics=True
            )
            self.assertEqual(reader._HEADER_SIZE, len(header))
            self.assertEqual(payload, plain)
            self.assertEqual(path.stat().st_size, size)
            self.assertEqual(1, diagnostics["verified_chunks"])
            self.assertEqual("verified", diagnostics["hmac"])

    def test_hmac_tamper_is_rejected(self):
        data = bytearray(build_save(b"facts" * 1000))
        data[reader._HEADER_SIZE + 8 + 16] ^= 0x01
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "StarPlayer.dat"
            path.write_bytes(data)
            with self.assertRaises(reader.SaveIntegrityError):
                reader.decrypt_save(str(path))


class CareerSeasonSummaryTests(unittest.TestCase):
    @staticmethod
    def _put_u16(blob: bytearray, base: int, offset: int, value: int) -> None:
        struct.pack_into("<H", blob, base + offset, value)

    def _fill_example_season(self, blob: bytearray, base: int) -> None:
        values = {
            0x00: 90, 0x02: 0, 0x3C: 55, 0x40: 12, 0x46: 14,
            0x48: 320, 0x4A: 95, 0x50: 15, 0x54: 7, 0x56: 4,
            0x5E: 2, 0x60: 1, 0x64: 20, 0x66: 0,
            0x70: 40, 0x78: 18, 0x7A: 5, 0x7C: 3, 0x7E: 13,
            0x80: 12, 0x82: 0, 0x84: 20, 0x86: 6, 0x88: 108,
            0x8A: 0, 0x8C: 0, 0x8E: 5, 0x96: 2,
        }
        for offset, value in values.items():
            self._put_u16(blob, base, offset, value)

    def test_prior_season_layout_reconstructs_synthetic_rates(self):
        size = reader._CAREER_SUMMARY_BASE + reader._CAREER_SUMMARY_STRIDE * 3
        blob = bytearray(size)
        prior = reader._CAREER_SUMMARY_BASE
        current = prior + reader._CAREER_SUMMARY_STRIDE
        self._fill_example_season(blob, prior)
        self._put_u16(blob, current, 0x00, 10)
        self._put_u16(blob, current, 0x48, 30)
        self._put_u16(blob, current, 0x4A, 25)
        self._put_u16(blob, current, 0x50, 2)
        self._put_u16(blob, current, 0x54, 2)

        history = reader._season_summary_history(
            bytes(blob),
            {"year": 2027, "career_year": 2},
            {},
        )

        self.assertEqual("verified", history["status"])
        self.assertEqual(1, len(history["seasons"]))
        season = history["seasons"][0]
        stats = season["stats"]
        self.assertEqual(2026, season["season_year"])
        self.assertEqual(100, stats["bat_AB"])
        self.assertEqual(28, stats["bat_H"])
        self.assertEqual(0.28, stats["bat_AVG"])
        self.assertEqual(0.333, stats["bat_OBP"])
        self.assertEqual(0.42, stats["bat_SLG"])
        self.assertEqual(0.753, stats["bat_OPS"])
        self.assertEqual(90, stats["pit_IP"])
        self.assertEqual(1.2, stats["pit_ERA"])
        self.assertEqual(95, stats["pit_K"])
        self.assertEqual("0x310", history["validation"]["record_stride"])

    def test_invalid_prior_record_is_not_published_as_history(self):
        size = reader._CAREER_SUMMARY_BASE + reader._CAREER_SUMMARY_STRIDE * 3
        blob = bytearray(size)
        prior = reader._CAREER_SUMMARY_BASE
        self._fill_example_season(blob, prior)
        self._put_u16(blob, prior, 0x84, 100)
        self._put_u16(blob, prior, 0x88, 10)

        history = reader._season_summary_history(
            bytes(blob),
            {"year": 2027, "career_year": 2},
            {},
        )

        self.assertEqual("unavailable", history["status"])
        self.assertEqual([], history["seasons"])

    def test_trailing_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "StarPlayer.dat"
            path.write_bytes(build_save(b"facts" * 1000) + b"trailing")
            with self.assertRaises(reader.SaveIntegrityError):
                reader.decrypt_save(str(path))


class RealSaveSmokeTests(unittest.TestCase):
    def test_detected_save_has_full_container_evidence(self):
        # Optional local-only smoke must never discover a maintainer's save.
        path = os.environ.get("STARMODEFEED_REAL_SAVE")
        if not path or not os.path.exists(path):
            self.skipTest("no local StarPlayer save detected")
        snapshot = reader.read_snapshot(path)
        evidence = snapshot["validation"]
        self.assertEqual("verified", evidence["container"])
        self.assertEqual(evidence["chunk_count"], evidence["verified_chunks"])
        self.assertEqual(evidence["chunk_count"], evidence["decrypted_chunks"])
        self.assertEqual("save_verified", snapshot["provenance"]["season_stats"])
        self.assertEqual("unavailable", snapshot["provenance"]["team_result"])
        self.assertTrue(snapshot["career_history"]["status"].startswith("verified"))
        self.assertEqual(
            max(0, int(snapshot["date"]["career_year"]) - 1),
            len(snapshot["career_history"]["seasons"]),
        )
        self.assertGreater(snapshot["player"]["id"], 0)
        self.assertTrue(snapshot["player"]["name"])
        self.assertTrue(snapshot["player"]["team"])
        if snapshot["player"]["age"] is not None:
            self.assertGreaterEqual(snapshot["player"]["age"], 15)
            self.assertLessEqual(snapshot["player"]["age"], 80)


if __name__ == "__main__":
    unittest.main()
