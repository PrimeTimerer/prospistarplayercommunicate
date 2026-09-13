#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Privacy-bounded local accounting for optional remote text providers.

Only numeric API metadata, model identifiers, operation names, timestamps and
bounded error categories are persisted.  Prompts, responses, API keys, player
identities, save paths and world identifiers never enter this store.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from pathlib import Path

from atomic_io import atomic_write_json, load_json


SCHEMA_VERSION = 1
_LOCK = threading.RLock()
_COUNT_KEYS = (
    "calls",
    "completed_generations",
    "failed_operations",
    "prompt_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "thoughts_tokens",
    "request_bytes",
    "response_bytes",
    "metered_responses",
)


def _counter() -> dict:
    return {key: 0 for key in _COUNT_KEYS}


def _empty() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": "gemini",
        "totals": _counter(),
        "days": {},
        "last_operation": None,
        "connection": {"status": "untested", "tested_at": None, "model": None},
    }


def _safe_count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalize_counter(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {key: _safe_count(raw.get(key)) for key in _COUNT_KEYS}


def _normalize(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    state = _empty()
    state["totals"] = _normalize_counter(raw.get("totals"))
    days = raw.get("days") if isinstance(raw.get("days"), dict) else {}
    state["days"] = {
        str(day): _normalize_counter(counter)
        for day, counter in days.items()
        if isinstance(day, str) and len(day) == 10
    }
    if isinstance(raw.get("last_operation"), dict):
        state["last_operation"] = {
            key: raw["last_operation"].get(key)
            for key in ("at", "operation", "model", "status", "error_code")
        }
    if isinstance(raw.get("connection"), dict):
        status = str(raw["connection"].get("status") or "untested")
        state["connection"] = {
            "status": status if status in ("untested", "verified", "failed") else "untested",
            "tested_at": raw["connection"].get("tested_at"),
            "model": raw["connection"].get("model"),
        }
    return state


def _stamp(now: datetime | None = None) -> tuple[str, str]:
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.date().isoformat(), moment.isoformat(timespec="seconds")


def connection_is_recent(
    summary: object,
    *,
    model: object,
    status: str,
    max_age_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Return whether the bounded connection result still applies.

    This helper is read-only.  It lets startup reuse a recent metadata-only
    probe instead of spending a network request on every browser refresh.
    """

    value = summary if isinstance(summary, dict) else {}
    connection = value.get("connection") if isinstance(value.get("connection"), dict) else {}
    if connection.get("status") != status or str(connection.get("model") or "") != str(model or ""):
        return False
    try:
        tested = datetime.fromisoformat(str(connection.get("tested_at") or ""))
    except (TypeError, ValueError):
        return False
    if tested.tzinfo is None:
        tested = tested.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age = (moment.astimezone(timezone.utc) - tested.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= max(0.0, float(max_age_seconds))


class ProviderUsageStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _load(self) -> dict:
        value, _quarantined = load_json(self.path, _empty(), quarantine_corrupt=True)
        return _normalize(value)

    def summary(self, *, now: datetime | None = None) -> dict:
        day, _at = _stamp(now)
        with _LOCK:
            state = self._load()
        return {
            "schema_version": SCHEMA_VERSION,
            "today_key": day,
            "today": copy.deepcopy(state["days"].get(day, _counter())),
            "total": copy.deepcopy(state["totals"]),
            "last_operation": copy.deepcopy(state.get("last_operation")),
            "connection": copy.deepcopy(state["connection"]),
            "scope": "this_app_only",
        }

    def record_generation(self, result, *, operation: str, now: datetime | None = None) -> dict:
        day, at = _stamp(now)
        delta = {
            "calls": max(1, _safe_count(getattr(result, "request_count", 1))),
            "completed_generations": 1,
            "failed_operations": 0,
            "prompt_tokens": _safe_count(getattr(result, "prompt_tokens", 0)),
            "output_tokens": _safe_count(getattr(result, "output_tokens", 0)),
            "total_tokens": _safe_count(getattr(result, "total_tokens", 0)),
            "cached_tokens": _safe_count(getattr(result, "cached_tokens", 0)),
            "thoughts_tokens": _safe_count(getattr(result, "thoughts_tokens", 0)),
            "request_bytes": _safe_count(getattr(result, "request_bytes", 0)),
            "response_bytes": _safe_count(getattr(result, "response_bytes", 0)),
            "metered_responses": _safe_count(getattr(result, "metered_responses", 0)),
        }
        with _LOCK:
            state = self._load()
            today = state["days"].setdefault(day, _counter())
            for key in _COUNT_KEYS:
                state["totals"][key] += delta[key]
                today[key] += delta[key]
            model = str(getattr(result, "model", "") or "") or None
            state["last_operation"] = {
                "at": at,
                "operation": str(operation or "generation"),
                "model": model,
                "status": "success",
                "error_code": None,
            }
            state["connection"] = {"status": "verified", "tested_at": at, "model": model}
            atomic_write_json(self.path, state)
        return self.summary(now=now)

    def record_failure(
        self,
        *,
        operation: str,
        model: object = None,
        error_code: object = None,
        calls: int = 1,
        request_bytes: int = 0,
        response_bytes: int = 0,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        thoughts_tokens: int = 0,
        metered_responses: int = 0,
        now: datetime | None = None,
    ) -> dict:
        day, at = _stamp(now)
        delta = _counter()
        delta.update(
            {
                "calls": max(1, _safe_count(calls)),
                "failed_operations": 1,
                "request_bytes": _safe_count(request_bytes),
                "response_bytes": _safe_count(response_bytes),
                "prompt_tokens": _safe_count(prompt_tokens),
                "output_tokens": _safe_count(output_tokens),
                "total_tokens": _safe_count(total_tokens),
                "cached_tokens": _safe_count(cached_tokens),
                "thoughts_tokens": _safe_count(thoughts_tokens),
                "metered_responses": _safe_count(metered_responses),
            }
        )
        with _LOCK:
            state = self._load()
            today = state["days"].setdefault(day, _counter())
            for key in _COUNT_KEYS:
                state["totals"][key] += delta[key]
                today[key] += delta[key]
            state["last_operation"] = {
                "at": at,
                "operation": str(operation or "generation"),
                "model": str(model or "") or None,
                "status": "failed",
                "error_code": str(error_code or "REMOTE_ERROR")[:80],
            }
            atomic_write_json(self.path, state)
        return self.summary(now=now)

    def record_connection(
        self,
        *,
        connected: bool,
        model: object = None,
        now: datetime | None = None,
    ) -> dict:
        _day, at = _stamp(now)
        with _LOCK:
            state = self._load()
            state["connection"] = {
                "status": "verified" if connected else "failed",
                "tested_at": at,
                "model": str(model or "") or None,
            }
            atomic_write_json(self.path, state)
        return self.summary(now=now)
