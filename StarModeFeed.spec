# -*- mode: python ; coding: utf-8 -*-
"""Reproducible one-file build for the local StarModeFeed desktop shell."""

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from product_version import APP_VERSION, WINDOWS_VERSION


crypto_datas, crypto_binaries, crypto_hiddenimports = collect_all("Crypto")

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=WINDOWS_VERSION,
        prodvers=WINDOWS_VERSION,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "041204B0",
                    [
                        StringStruct("CompanyName", "StarModeFeed Local Project"),
                        StringStruct("FileDescription", "StarPlayer Community Simulator"),
                        StringStruct("FileVersion", APP_VERSION),
                        StringStruct("InternalName", "StarModeFeed"),
                        StringStruct("OriginalFilename", "StarModeFeed.exe"),
                        StringStruct("ProductName", "StarPlayer Community Simulator"),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1042, 1200])]),
    ],
)

a = Analysis(
    ["app_native.py"],
    pathex=[],
    binaries=crypto_binaries,
    datas=[
        ("ui", "ui"),
        ("data/npb_records.json", "data"),
        ("data/packs", "data/packs"),
        ("data/editorial", "data/editorial"),
        ("data/interactions", "data/interactions"),
        ("data/story", "data/story"),
        ("data/parser_profiles", "data/parser_profiles"),
        ("tools/winrt_ocr.ps1", "tools"),
        *crypto_datas,
    ],
    hiddenimports=crypto_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StarModeFeed",
    icon="assets/StarModeFeed.ico",
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
