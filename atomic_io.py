#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crash-safe local file helpers used by StarModeFeed state and outputs."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class JsonStateError(RuntimeError):
    """Raised when persisted JSON cannot be recovered safely."""


REPLACE_ATTEMPTS = 12
REPLACE_RETRY_SECONDS = 0.05


def _replace_with_retry(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """``os.replace`` that tolerates a transient Windows sharing violation.

    Another thread reading the target (Python opens files without
    FILE_SHARE_DELETE) makes the rename fail with PermissionError for a few
    milliseconds. Retry briefly instead of surfacing a spurious failure.
    """
    last_error: OSError | None = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(REPLACE_RETRY_SECONDS * (attempt + 1))
    assert last_error is not None
    raise last_error


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace *path* atomically with *data* using a same-directory temp file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: str | os.PathLike[str], text: str, encoding: str = "utf-8"
) -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, payload)


def _quarantine_name(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    return candidate


def load_json(
    path: str | os.PathLike[str],
    default: Any,
    *,
    quarantine_corrupt: bool = True,
) -> tuple[Any, str | None]:
    """Load JSON and return ``(value, quarantined_path)``.

    Missing files return a deep copy of *default*. Invalid files are preserved
    under a timestamped quarantine name before the default is returned.
    """
    source = Path(path)
    if not source.exists():
        return copy.deepcopy(default), None
    try:
        with source.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if not quarantine_corrupt:
            raise JsonStateError(f"invalid JSON state: {source}") from exc
        quarantined = _quarantine_name(source)
        try:
            _replace_with_retry(source, quarantined)
        except OSError as move_exc:
            raise JsonStateError(f"could not preserve invalid JSON state: {source}") from move_exc
        return copy.deepcopy(default), str(quarantined)
