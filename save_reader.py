#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility import path. The verified container reader lives in save_reader_v2."""

from __future__ import annotations

from save_reader_v2 import (  # noqa: E402,F401
    SaveBusyError,
    SaveIntegrityError,
    SaveReadError,
    SnapshotParseError,
    _AESKEY,
    _HM,
    _aes_cbc,
    _find_anchor,
    _find_anchors,
    _find_bat_header,
    _find_bat_headers,
    _identities,
    _inflate,
    _twoway_sig,
    _u16,
    decrypt_save,
    parse_header,
    read_identity_summary,
    read_snapshot,
    read_stable_bytes,
)
