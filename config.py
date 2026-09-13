#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility import path. Validated local configuration lives in config_v2."""

from __future__ import annotations

from config_v2 import (  # noqa: E402,F401
    CONFIG_PATH,
    DEFAULTS,
    HERE,
    PERSONA_PATH,
    PROVIDER_USAGE_PATH,
    SECRETS_PATH,
    autodetect_save,
    autodetect_saves,
    load,
    load_with_diagnostics,
    public_config,
    provider_usage_path,
    save,
    save_persona,
)
