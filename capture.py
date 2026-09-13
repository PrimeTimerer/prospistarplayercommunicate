#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dependency-free Windows screen/window capture (ctypes GDI + stdlib PNG).

Finds the running game window (title match) and captures it to a PNG so the
vision LLM can read the live game screen — no Pillow/mss, so it packages into
the offline .exe. Games are GPU-rendered, so PrintWindow can return black;
we detect that and fall back to a screen-region BitBlt of the window rect.
"""
import ctypes
import struct
import time
import zlib
from ctypes import wintypes

from atomic_io import atomic_write_bytes

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32

# Win32 handles are pointer-sized. Explicit signatures prevent 64-bit handle
# truncation when ctypes would otherwise assume a 32-bit C ``int``.
_u.IsWindowVisible.argtypes = [wintypes.HWND]
_u.IsWindowVisible.restype = wintypes.BOOL
_u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u.GetWindowTextLengthW.restype = ctypes.c_int
_u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u.GetWindowTextW.restype = ctypes.c_int
_u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u.GetWindowRect.restype = wintypes.BOOL
_u.GetDC.argtypes = [wintypes.HWND]
_u.GetDC.restype = wintypes.HDC
_u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u.ReleaseDC.restype = ctypes.c_int
_u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_u.PrintWindow.restype = wintypes.BOOL
_u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u.ShowWindow.restype = wintypes.BOOL
_u.SetForegroundWindow.argtypes = [wintypes.HWND]
_u.SetForegroundWindow.restype = wintypes.BOOL

_g.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g.CreateCompatibleDC.restype = wintypes.HDC
_g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g.SelectObject.restype = wintypes.HGDIOBJ
_g.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
_g.BitBlt.restype = wintypes.BOOL
_g.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
_g.GetDIBits.restype = ctypes.c_int
_g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g.DeleteObject.restype = wintypes.BOOL
_g.DeleteDC.argtypes = [wintypes.HDC]
_g.DeleteDC.restype = wintypes.BOOL
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        _u.SetProcessDPIAware()
    except Exception:
        pass

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
GAME_KEYWORDS = ("prospi", "프로야구", "야구", "spirits", "실황", "파워풀", "ebaseball", "baseball")


def find_window(keywords=GAME_KEYWORDS):
    """Return hwnd of the first visible window whose title matches a keyword."""
    match = {"hwnd": None, "best": None}
    kws = [k.lower() for k in keywords]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, l):
        if not _u.IsWindowVisible(h):
            return True
        n = _u.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _u.GetWindowTextW(h, buf, n + 1)
        t = buf.value.strip().lower()
        if not t:
            return True
        for k in kws:
            if k in t:
                # exact 'prospi' preferred; otherwise first keyword hit
                if t == "prospi" and match["hwnd"] is None:
                    match["hwnd"] = h
                elif match["best"] is None:
                    match["best"] = h
                break
        return True

    _u.EnumWindows(cb, 0)
    return match["hwnd"] or match["best"]


def _rect(hwnd):
    r = wintypes.RECT()
    _u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _grab_bits(src_dc, x, y, w, h, blt_src_xy):
    """BitBlt/PrintWindow into a memory bitmap, return top-down BGRA bytes."""
    owned_screen_dc = None
    compatible_dc = src_dc
    if blt_src_xy is None:
        owned_screen_dc = _u.GetDC(0)
        compatible_dc = owned_screen_dc
    mem_dc = _g.CreateCompatibleDC(compatible_dc)
    bmp = _g.CreateCompatibleBitmap(compatible_dc, w, h)
    old_object = _g.SelectObject(mem_dc, bmp)
    try:
        if blt_src_xy is not None:
            sx, sy = blt_src_xy
            ok = bool(_g.BitBlt(mem_dc, 0, 0, w, h, src_dc, sx, sy, SRCCOPY))
        else:
            # src_dc is an HWND for this branch; flag 2 is PW_RENDERFULLCONTENT.
            ok = bool(_u.PrintWindow(src_dc, mem_dc, 2))
        if not ok:
            return None
        # BITMAPINFOHEADER, negative height = top-down
        bmi = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bmi_buf = ctypes.create_string_buffer(bmi, 40 + 16)
        nbytes = w * h * 4
        buf = ctypes.create_string_buffer(nbytes)
        got = _g.GetDIBits(mem_dc, bmp, 0, h, buf, bmi_buf, DIB_RGB_COLORS)
        return buf.raw if got else None
    finally:
        if old_object:
            _g.SelectObject(mem_dc, old_object)
        if bmp:
            _g.DeleteObject(bmp)
        if mem_dc:
            _g.DeleteDC(mem_dc)
        if owned_screen_dc:
            _u.ReleaseDC(0, owned_screen_dc)


def _mostly_black(bgra, w, h, thresh=0.985):
    """True if the image is almost entirely black (GPU-window PrintWindow fail)."""
    step = max(1, (w * h) // 4000)
    total = 0
    black = 0
    for i in range(0, w * h, step):
        b = bgra[i * 4]; g = bgra[i * 4 + 1]; r = bgra[i * 4 + 2]
        total += 1
        if b < 8 and g < 8 and r < 8:
            black += 1
    return total and (black / total) >= thresh


def _bgra_to_rgb(bgra, w, h):
    # Strided slice assignment runs in C; the previous per-pixel loop took
    # seconds for a 1440p frame and made the burst interval meaningless.
    count = w * h
    view = bytes(bgra[: count * 4])
    out = bytearray(count * 3)
    out[0::3] = view[2::4]
    out[1::3] = view[1::4]
    out[2::3] = view[0::4]
    return bytes(out)


def write_png(path, w, h, rgb):
    stride = w * 3
    rgb = bytes(rgb)
    # One filter byte (0 = None) in front of every scanline.
    raw = b"".join(
        b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h)
    )
    comp = zlib.compress(raw, 6)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    atomic_write_bytes(path, png)


def capture_game(out_path, keywords=GAME_KEYWORDS, bring_to_front=True):
    """Capture the game window to out_path (PNG). Returns (ok, msg)."""
    hwnd = find_window(keywords)
    if not hwnd:
        return False, "게임 창을 찾지 못했습니다 (게임이 실행 중인지 확인)."
    x, y, w, h = _rect(hwnd)
    if w < 50 or h < 50:
        return False, "게임 창 크기가 비정상입니다."
    # 1) try PrintWindow (works without focus, but GPU windows may be black)
    bgra = _grab_bits(hwnd, x, y, w, h, blt_src_xy=None)
    if bgra is None or _mostly_black(bgra, w, h):
        # 2) fallback: bring window forward and BitBlt the screen region
        if bring_to_front:
            try:
                _u.ShowWindow(hwnd, 9)          # SW_RESTORE
                _u.SetForegroundWindow(hwnd)
                time.sleep(0.35)
            except Exception:
                pass
        x, y, w, h = _rect(hwnd)
        screen = _u.GetDC(0)
        bgra = _grab_bits(screen, x, y, w, h, blt_src_xy=(x, y))
        _u.ReleaseDC(0, screen)
        if bgra is None:
            return False, "화면 캡처에 실패했습니다."
        if _mostly_black(bgra, w, h):
            return False, "캡처가 검은 화면입니다 (게임이 최소화/가려짐일 수 있음)."
    write_png(out_path, w, h, _bgra_to_rgb(bgra, w, h))
    return True, out_path


if __name__ == "__main__":
    import os
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "output", "_capture_test.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ok, msg = capture_game(out)
    print("OK" if ok else "FAIL", msg)
