#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import save_reader_v2 as reader
import service as service_module


class ProfileAgeTests(unittest.TestCase):
    def test_custom_profile_record_kind_is_discoverable(self):
        blob = bytearray(0x200)
        base = 0x20
        blob[base] = 0x1B
        struct.pack_into("<I", blob, base + 8, 100866)
        blob[base + 0x14 : base + 0x1A] = bytes((20, 5, 14, 4, 15, 1))
        encoded_name = "天刀\0山王\0ＴＥＮＤＯ".encode("utf-8")
        blob[base + 0x24 : base + 0x24 + len(encoded_name)] = encoded_name
        identities = reader._identities(bytes(blob))
        self.assertEqual(1, len(identities))
        self.assertEqual(0x1B, identities[0]["record_kind"])
        self.assertEqual(100866, identities[0]["id"])

    def test_repeated_profile_year_must_agree_before_age_is_exposed(self):
        blob = bytearray(0x500)
        records = [
            {"off": 0x20, "id": 77, "parts": ["Skenes", "Paul"]},
            {"off": 0x220, "id": 77, "parts": ["Skenes", "Paul"]},
        ]
        for record in records:
            struct.pack_into("<H", blob, record["off"] + 8 + 0x120, 2007)
        self.assertEqual(2007, reader._profile_birth_year(blob, records, records[0], 2027))
        self.assertEqual(20, reader._season_age(2027, 2007))

        struct.pack_into("<H", blob, records[1]["off"] + 8 + 0x120, 2008)
        self.assertIsNone(reader._profile_birth_year(blob, records, records[0], 2027))

    def test_implausible_age_is_not_presented(self):
        self.assertIsNone(reader._season_age(2027, 2020))
        self.assertIsNone(reader._season_age(2027, 1900))


class SaveDiscoveryTests(unittest.TestCase):
    def test_scan_returns_verified_identity_and_marks_unsupported_saves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "00" / "StarPlayer.dat"
            unsupported = root / "01" / "StarPlayer.dat"
            valid.parent.mkdir()
            unsupported.parent.mkdir()
            valid.write_bytes(b"valid")
            unsupported.write_bytes(b"old")

            summary = {
                "player": {
                    "id": 100866,
                    "name": "Paul Skenes",
                    "display_name": "Paul Skenes(투수)",
                    "team": "YOKOHAMA DeNA BAYSTARS",
                    "birth_year": 2007,
                    "age": 20,
                    "age_basis": "game_year_minus_save_profile_birth_year",
                },
                "date": {"year": 2027, "month": 7, "day": 24, "career_year": 2},
                "provenance": {
                    "identity": "save_verified",
                    "team": "save_verified",
                    "age": "derived_from_save_verified_profile_and_game_year",
                },
                "validation": {
                    "container": "verified",
                    "verified_chunks": 51,
                    "chunk_count": 51,
                    "identity_method": "header-name",
                },
            }

            def read_summary(path):
                if path == str(unsupported):
                    raise reader.SnapshotParseError("unsupported fixture")
                return summary

            app = service_module.StarModeService()
            with patch.object(
                service_module.config_module,
                "autodetect_saves",
                return_value=[str(valid), str(unsupported)],
            ), patch.object(
                service_module.save_reader,
                "read_identity_summary",
                side_effect=read_summary,
            ) as identity_reader:
                first = app.rescan_saves()
                second = app.rescan_saves()

            self.assertEqual("verified", first[0]["identity_status"])
            self.assertEqual("Paul Skenes", first[0]["player"]["name"])
            self.assertEqual("YOKOHAMA DeNA BAYSTARS", first[0]["player"]["team"])
            self.assertEqual(20, first[0]["player"]["age"])
            self.assertEqual("unsupported", first[1]["identity_status"])
            self.assertIsNone(first[1]["player"])
            self.assertEqual(first, second)
            self.assertEqual(2, identity_reader.call_count)


if __name__ == "__main__":
    unittest.main()
