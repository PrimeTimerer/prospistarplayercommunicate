#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validated local configuration with legacy import and atomic persistence."""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

from atomic_io import JsonStateError, atomic_write_json, atomic_write_text, load_json
import gemini_provider
from provider_usage import ProviderUsageStore
from secret_store import SecretStore

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

LOCAL_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "StarModeFeed")
CONFIG_PATH = os.path.join(LOCAL_ROOT, "config.json")
PERSONA_PATH = os.path.join(LOCAL_ROOT, "persona.txt")
SECRETS_PATH = os.path.join(LOCAL_ROOT, "secrets.json")
PROVIDER_USAGE_PATH = os.path.join(LOCAL_ROOT, "provider-usage.json")
LEGACY_CONFIG_PATH = os.path.join(HERE, "config.json")
LEGACY_PERSONA_PATH = os.path.join(HERE, "persona.txt")

DEFAULTS = {
    "schema_version": 2,
    "save_path": None,
    "heat": 7,
    # Expression heat controls disagreement. Community language level is a
    # separate vocabulary ceiling so an intense debate need not be profane.
    "community_language_level": 2,
    # Extra spellings that count as the protagonist. The save spells the
    # player in Latin; a Korean board writes the same person in Hangul.
    "protagonist_aliases": [],
    "mode": "standard",
    "platforms": ["dc", "fmk", "mlb"],
    "persona": "",
    "auto_narrate": False,
    "auto_open": True,
    "auto_capture": False,
    "poll_interval": 3.0,
    "output_dir": os.path.join(LOCAL_ROOT, "output"),
    "shots_dir": os.path.join(LOCAL_ROOT, "shots"),
    "data_dir": os.path.join(LOCAL_ROOT, "data"),
    "ledger_path": os.path.join(HERE, "data", "ledger.json"),
    # A launcher path is inert configuration, never durable consent. It may be
    # executed only by the explicit start control after a one-shot confirmation.
    "llm_launcher": None,
    # Legacy behavior is retained unless the user explicitly selects Gemini.
    # ``local_auto`` means an already-running loopback LLM when available and
    # the deterministic renderer otherwise. It never starts a model process.
    # Remote calls also require a separate consent flag.
    "ai_provider": "local_auto",
    "gemini_model": gemini_provider.DEFAULT_MODEL,
    "gemini_batch_concurrency": 5,
    "gemini_consent": False,
    # Native/browser notifications are opt-in. In-app status toasts remain
    # available regardless of this setting.
    "notifications_enabled": False,
    "theme": "dark",
    "window_width": 1180,
    "window_height": 820,
}


def autodetect_saves() -> list[str]:
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), "KONAMI"),
        os.path.join(os.path.expanduser("~"), "Documents", "KONAMI"),
    ]
    found: set[str] = set()
    for root in roots:
        if root and os.path.isdir(root):
            found.update(glob.glob(os.path.join(root, "**", "StarPlayer.dat"), recursive=True))
    return sorted(
        found,
        key=lambda value: (
            os.path.basename(os.path.dirname(value)) == "00",
            os.path.getmtime(value),
        ),
        reverse=True,
    )


def autodetect_save() -> str | None:
    candidates = autodetect_saves()
    return candidates[0] if candidates else None


def _config_source() -> str | None:
    if os.path.exists(CONFIG_PATH):
        return CONFIG_PATH
    if os.path.exists(LEGACY_CONFIG_PATH):
        return LEGACY_CONFIG_PATH
    return None


def _validate(raw: dict) -> dict:
    config = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in raw:
            config[key] = raw[key]
    try:
        config["heat"] = max(1, min(10, int(config["heat"])))
    except (TypeError, ValueError):
        config["heat"] = DEFAULTS["heat"]
    try:
        config["community_language_level"] = max(
            1, min(5, int(config["community_language_level"]))
        )
    except (TypeError, ValueError):
        config["community_language_level"] = DEFAULTS["community_language_level"]
    raw_aliases = config.get("protagonist_aliases")
    try:
        config["gemini_batch_concurrency"] = max(1, min(5, int(config["gemini_batch_concurrency"])))
    except (TypeError, ValueError, OverflowError):
        config["gemini_batch_concurrency"] = DEFAULTS["gemini_batch_concurrency"]
    rows = raw_aliases if isinstance(raw_aliases, (list, tuple)) else ([raw_aliases] if raw_aliases else [])
    seen: list[str] = []
    for row in rows:
        text = str(row or "").strip()
        if text and len(text) <= 40 and text.casefold() not in {item.casefold() for item in seen}:
            seen.append(text)
        if len(seen) >= 8:
            break
    config["protagonist_aliases"] = seen
    if config["mode"] not in ("quick", "standard", "explosion"):
        config["mode"] = "standard"
    config["platforms"] = [
        value for value in config.get("platforms", []) if value in ("dc", "fmk", "mlb")
    ] or ["dc", "fmk", "mlb"]
    try:
        config["poll_interval"] = max(1.0, min(60.0, float(config["poll_interval"])))
    except (TypeError, ValueError):
        config["poll_interval"] = DEFAULTS["poll_interval"]
    for key in ("auto_narrate", "auto_open", "auto_capture"):
        config[key] = bool(config.get(key))
    # A persisted notification preference is explicit opt-in. String values
    # from hand-edited or legacy files must not unexpectedly enable OS alerts.
    config["notifications_enabled"] = config.get("notifications_enabled") is True
    if config.get("ai_provider") not in ("local_auto", "deterministic", "gemini"):
        config["ai_provider"] = DEFAULTS["ai_provider"]
    try:
        config["gemini_model"] = gemini_provider.normalize_model(config.get("gemini_model"))
    except ValueError:
        config["gemini_model"] = DEFAULTS["gemini_model"]
    # Consent is security-sensitive. Only a real JSON boolean true enables
    # outbound context; strings such as "true" or "false" never do.
    config["gemini_consent"] = config.get("gemini_consent") is True
    for key in ("output_dir", "shots_dir", "data_dir", "ledger_path"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            config[key] = DEFAULTS[key]
        else:
            config[key] = os.path.abspath(os.path.expandvars(os.path.expanduser(value)))
    save_path = config.get("save_path")
    config["save_path"] = (
        os.path.abspath(os.path.expandvars(os.path.expanduser(save_path)))
        if isinstance(save_path, str) and save_path.strip()
        else None
    )
    launcher = config.get("llm_launcher")
    config["llm_launcher"] = (
        os.path.abspath(os.path.expandvars(os.path.expanduser(launcher)))
        if isinstance(launcher, str) and launcher.strip()
        else None
    )
    config["schema_version"] = 2
    return config


def load_with_diagnostics() -> tuple[dict, dict]:
    source = _config_source()
    raw: dict = {}
    quarantined = None
    if source:
        can_quarantine = os.path.normcase(source) == os.path.normcase(CONFIG_PATH)
        try:
            loaded, quarantined = load_json(source, {}, quarantine_corrupt=can_quarantine)
        except JsonStateError:
            # Legacy workspace configuration is read-only input. Keep it in
            # place for manual recovery and continue from validated defaults.
            loaded = {}
        if isinstance(loaded, dict):
            raw = loaded
    config = _validate(raw)
    if not config.get("save_path") or not os.path.exists(config["save_path"]):
        detected = autodetect_save()
        if detected:
            config["save_path"] = detected
    persona_sources = (PERSONA_PATH, LEGACY_PERSONA_PATH)
    for persona_path in persona_sources:
        try:
            text = Path(persona_path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            config["persona"] = text
            break
    return config, {
        "source": source,
        "using_legacy_config": source == LEGACY_CONFIG_PATH,
        "quarantined": quarantined,
        "save_exists": bool(config.get("save_path") and os.path.exists(config["save_path"])),
    }


def load() -> dict:
    return load_with_diagnostics()[0]


def save_persona(text: str) -> None:
    atomic_write_text(PERSONA_PATH, text.strip() + ("\n" if text.strip() else ""))


def save(config: dict) -> None:
    validated = _validate(config)
    keep = {key: validated[key] for key in DEFAULTS if key in validated}
    atomic_write_json(CONFIG_PATH, keep)


def provider_usage_path(config: dict) -> str:
    """Resolve local provider telemetry beside this configuration's output.

    Real installs use ``LOCAL_ROOT``.  The output-derived fallback keeps
    isolated/test configurations from touching the user's profile.
    """

    explicit = config.get("provider_usage_path")
    if isinstance(explicit, str) and explicit.strip():
        return os.path.abspath(os.path.expandvars(os.path.expanduser(explicit)))
    output_dir = config.get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        return str(Path(output_dir).resolve().parent / "provider-usage.json")
    return PROVIDER_USAGE_PATH


def public_config(config: dict) -> dict:
    try:
        gemini_status = SecretStore(SECRETS_PATH).public_status()
    except Exception:
        # A broken local secret must not make the offline application unusable.
        gemini_status = {"configured": False, "source": None}
    try:
        gemini_usage = ProviderUsageStore(provider_usage_path(config)).summary()
    except Exception:
        # Usage telemetry is advisory.  A damaged counter never blocks saves,
        # offline mode, settings, or provider configuration.
        gemini_usage = {
            "today": {}, "total": {}, "last_operation": None,
            "connection": {"status": "unavailable", "tested_at": None, "model": None},
            "scope": "this_app_only",
        }
    return {
        "save_path": config.get("save_path"),
        "save_exists": bool(config.get("save_path") and os.path.exists(config["save_path"])),
        "heat": config.get("heat"),
        "community_language_level": config.get("community_language_level"),
        "protagonist_aliases": list(config.get("protagonist_aliases") or []),
        "mode": config.get("mode"),
        "platforms": config.get("platforms"),
        "persona": config.get("persona", ""),
        "auto_narrate": config.get("auto_narrate"),
        "auto_capture": config.get("auto_capture"),
        "llm_launcher": config.get("llm_launcher"),
        "ai_provider": config.get("ai_provider", DEFAULTS["ai_provider"]),
        "gemini_model": config.get("gemini_model", DEFAULTS["gemini_model"]),
        "gemini_consent": bool(config.get("gemini_consent")),
        "notifications_enabled": bool(config.get("notifications_enabled")),
        "gemini_key_present": bool(gemini_status.get("configured")),
        "gemini_key_source": gemini_status.get("source"),
        "gemini_models": list(gemini_provider.SUPPORTED_MODELS),
        "gemini_usage": gemini_usage,
        "theme": config.get("theme", "dark"),
    }
