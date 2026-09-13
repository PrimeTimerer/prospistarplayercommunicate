#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hotkeys
import service as service_module
from service import MAX_NARRATIVE_IMAGES, StarModeService


class FakeCaptureModule:
    def __init__(self):
        self.paths = []

    def capture_game(self, path):
        target = Path(path)
        target.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        self.paths.append(target)
        return True, str(target)


class MultipleCaptureTests(unittest.TestCase):
    def test_burst_creates_unique_files_and_gallery_items(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {"shots_dir": str(Path(temp) / "shots")}
            fake = FakeCaptureModule()
            app = StarModeService()
            with (
                patch.object(service_module.config_module, "load", return_value=config),
                patch.object(service_module, "capture_module", fake),
                patch.object(service_module.time, "sleep", return_value=None),
            ):
                result = app.capture_game(count=3, interval_ms=150, trigger="test")
                status = app.capture_status(config)

            self.assertTrue(result["ok"])
            self.assertEqual(3, result["count"])
            self.assertEqual(3, len({path.name for path in fake.paths}))
            self.assertEqual(3, status["pending_count"])
            self.assertTrue(all(row["image_url"].startswith("/api/v1/capture-image/") for row in status["items"]))
            self.assertEqual(MAX_NARRATIVE_IMAGES, status["narrative_limit"])

    def test_capture_count_is_bounded_to_narrative_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {"shots_dir": str(Path(temp) / "shots")}
            fake = FakeCaptureModule()
            app = StarModeService()
            with (
                patch.object(service_module.config_module, "load", return_value=config),
                patch.object(service_module, "capture_module", fake),
                patch.object(service_module.time, "sleep", return_value=None),
            ):
                result = app.capture_game(count=999, interval_ms=150)
            self.assertEqual(MAX_NARRATIVE_IMAGES, result["count"])

    def test_capture_removal_rejects_traversal_and_removes_exact_item(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "shots"
            root.mkdir()
            target = root / "capture-known.png"
            target.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            config = {"shots_dir": str(root)}
            app = StarModeService()
            with patch.object(service_module.config_module, "load", return_value=config):
                with self.assertRaises(ValueError):
                    app.read_capture_path("../capture-known.png")
                result = app.remove_capture("capture-known.png")
            self.assertEqual("capture-known.png", result["removed"])
            self.assertFalse(target.exists())


class PixelConversionTests(unittest.TestCase):
    @staticmethod
    def _reference_rgb(bgra, w, h):
        out = bytearray(w * h * 3)
        j = 0
        for i in range(0, w * h * 4, 4):
            out[j] = bgra[i + 2]; out[j + 1] = bgra[i + 1]; out[j + 2] = bgra[i]
            j += 3
        return bytes(out)

    @staticmethod
    def _reference_png(w, h, rgb):
        import struct
        import zlib

        raw = bytearray()
        stride = w * 3
        for y in range(h):
            raw.append(0)
            raw += rgb[y * stride:(y + 1) * stride]
        comp = zlib.compress(bytes(raw), 6)

        def chunk(typ, data):
            return (struct.pack(">I", len(data)) + typ + data
                    + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", comp)
        png += chunk(b"IEND", b"")
        return png

    def test_vectorized_conversion_matches_reference_loop_byte_for_byte(self):
        import random

        import capture

        rng = random.Random(1234)
        w, h = 37, 11
        bgra = bytes(rng.randrange(256) for _ in range(w * h * 4))
        rgb = capture._bgra_to_rgb(bgra, w, h)
        self.assertEqual(self._reference_rgb(bgra, w, h), rgb)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame.png"
            capture.write_png(str(target), w, h, rgb)
            self.assertEqual(self._reference_png(w, h, rgb), target.read_bytes())


class AtomicReplaceRetryTests(unittest.TestCase):
    def test_transient_permission_error_is_retried(self):
        import atomic_io

        calls = {"n": 0}
        real_replace = atomic_io.os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("sharing violation")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            with patch.object(atomic_io.os, "replace", side_effect=flaky), patch.object(
                atomic_io.time, "sleep", return_value=None
            ):
                atomic_io.atomic_write_json(target, {"ok": True})
            self.assertEqual(3, calls["n"])
            self.assertEqual('{\n  "ok": true\n}\n', target.read_text(encoding="utf-8"))
            self.assertEqual([], [p for p in Path(temp).iterdir() if p.suffix == ".tmp"])


class HotkeyContractTests(unittest.TestCase):
    def test_capture_hotkeys_have_distinct_single_and_burst_bindings(self):
        bindings = {row["keys"]: row["count"] for row in hotkeys.HOTKEY_BINDINGS}
        self.assertEqual(1, bindings["F8"])
        self.assertEqual(3, bindings["F9"])
        self.assertEqual(len(hotkeys.HOTKEY_BINDINGS), len({row["id"] for row in hotkeys.HOTKEY_BINDINGS}))


if __name__ == "__main__":
    unittest.main()
