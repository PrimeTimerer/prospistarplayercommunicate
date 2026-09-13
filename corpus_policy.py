#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source authorization model and fetch gate (master plan section 10).

No network code lives here. The gate answers one question for a builder
adapter: may this source be fetched, stored, transformed, or redistributed
right now? ``publicly accessible`` never means ``licensed``. Robots, terms,
API terms, and item licenses are independent gates.

The runtime application never imports a crawler. This module is shared by
the builder (future) and by tests that prove a restricted source is blocked
before any fetch could start.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCE_CLASSES = {
    "A": {
        "label": "public domain / open license",
        "storage": "full_text",
        "runtime_text_reuse": True,
        "examples": "Project Gutenberg PD items, eligible Aozora works, Wikisource, eligible Gongu Madang items",
    },
    "B": {
        "label": "direct permission / user-authored",
        "storage": "full_text_under_grant",
        "runtime_text_reuse": True,
        "examples": "user dialogue packs, publisher or author permission",
    },
    "C": {
        "label": "official API / display license",
        "storage": "api_permitted_fields",
        "runtime_text_reuse": "as_permitted",
        "examples": "approved social or video APIs",
    },
    "D": {
        "label": "reference / metadata only",
        "storage": "metadata_only",
        "runtime_text_reuse": False,
        "examples": "ordinary news, restricted league sites, search discovery",
    },
    "E": {
        "label": "denied",
        "storage": "none",
        "runtime_text_reuse": False,
        "examples": "robots/terms denial, paywall, login-only, uncertain rights, deletion request",
    },
}

REQUIRED_SOURCE_FIELDS = (
    "source_id",
    "jurisdiction",
    "owner",
    "base_url",
    "source_kind",
    "language",
    "access_method",
    "robots_checked_at",
    "robots_snapshot_hash",
    "terms_url",
    "terms_checked_at",
    "terms_snapshot_hash",
    "item_license_method",
    "license_id",
    "license_url",
    "license_evidence_hash",
    "copyright_status",
    "allowed_storage",
    "allowed_transformation",
    "allowed_redistribution",
    "attribution_text",
    "retention_days",
    "rate_limit",
    "contact",
    "deletion_method",
    "reviewer",
    "decision",
    "decision_reason",
)

DECISIONS = ("pending", "approved", "denied", "expired", "revoked")
STORAGE_LEVELS = ("none", "metadata_only", "api_permitted_fields", "full_text_under_grant", "full_text")
PURPOSES = ("discover", "fetch_text", "fetch_metadata", "store", "transform", "redistribute")

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$")

# Media payloads are outside the text-only corpus program (section 12.7).
REJECTED_CONTENT_TYPES = ("image/", "video/", "audio/")


class SourcePolicyError(RuntimeError):
    """Raised before any fetch, store, or transform that policy does not allow."""


@dataclass
class GateDecision:
    allowed: bool
    reason: str
    source_id: str | None = None
    purpose: str | None = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "source_id": self.source_id,
            "purpose": self.purpose,
        }


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not _ISO.match(value.strip()):
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def validate_source_record(record: object) -> list[str]:
    """Return human-readable problems; an empty list means the record is complete."""
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["source record must be a JSON object"]
    for key in REQUIRED_SOURCE_FIELDS:
        if key not in record:
            problems.append(f"missing field: {key}")
    source_class = str(record.get("source_class") or "")
    if source_class not in SOURCE_CLASSES:
        problems.append("source_class must be one of A, B, C, D, E")
    decision = str(record.get("decision") or "")
    if decision not in DECISIONS:
        problems.append("decision must be one of " + ", ".join(DECISIONS))
    storage = str(record.get("allowed_storage") or "")
    if storage not in STORAGE_LEVELS:
        problems.append("allowed_storage must be one of " + ", ".join(STORAGE_LEVELS))
    for key in ("robots_checked_at", "terms_checked_at"):
        if key in record and _parse_time(record.get(key)) is None:
            problems.append(f"{key} must be an ISO 8601 timestamp")
    if decision == "approved":
        if _parse_time(record.get("policy_review_expires_at")) is None:
            problems.append("approved sources need policy_review_expires_at")
        if not str(record.get("reviewer") or "").strip():
            problems.append("approved sources need a reviewer")
        if not str(record.get("decision_reason") or "").strip():
            problems.append("approved sources need a decision_reason")
    try:
        retention = int(record.get("retention_days"))
        if retention < 0:
            problems.append("retention_days must be >= 0")
    except (TypeError, ValueError):
        problems.append("retention_days must be an integer")
    return problems


def _storage_rank(level: str) -> int:
    return STORAGE_LEVELS.index(level) if level in STORAGE_LEVELS else -1


def evaluate(record: object, purpose: str, *, now: datetime | None = None, tombstoned=()) -> GateDecision:
    """Evaluate one purpose against one source record without side effects."""
    now = now or datetime.now(timezone.utc)
    source_id = str((record or {}).get("source_id") or "") if isinstance(record, dict) else None
    if purpose not in PURPOSES:
        return GateDecision(False, f"unknown purpose: {purpose}", source_id, purpose)
    problems = validate_source_record(record)
    if problems:
        return GateDecision(False, "incomplete source record: " + "; ".join(problems[:3]), source_id, purpose)
    assert isinstance(record, dict)
    if source_id in set(tombstoned):
        return GateDecision(False, "source is tombstoned", source_id, purpose)
    source_class = str(record.get("source_class"))
    decision = str(record.get("decision"))
    if decision != "approved":
        return GateDecision(False, f"decision is {decision}, not approved", source_id, purpose)
    expires = _parse_time(record.get("policy_review_expires_at"))
    if expires is None or expires <= now:
        return GateDecision(False, "policy review expired; source is frozen", source_id, purpose)
    if source_class == "E":
        return GateDecision(False, "class E sources are never fetched", source_id, purpose)
    for key in ("robots_checked_at", "terms_checked_at"):
        checked = _parse_time(record.get(key))
        if checked is None or checked > now:
            return GateDecision(False, f"{key} is missing or in the future", source_id, purpose)
    storage = str(record.get("allowed_storage"))
    if purpose == "discover":
        return GateDecision(True, "discovery permitted", source_id, purpose)
    if purpose == "fetch_metadata":
        if _storage_rank(storage) >= _storage_rank("metadata_only"):
            return GateDecision(True, "metadata fetch permitted", source_id, purpose)
        return GateDecision(False, "storage level forbids metadata retention", source_id, purpose)
    if purpose == "fetch_text":
        if source_class == "D":
            return GateDecision(False, "class D sources supply metadata only, never prose", source_id, purpose)
        if _storage_rank(storage) >= _storage_rank("api_permitted_fields"):
            return GateDecision(True, "text fetch permitted within allowed_storage", source_id, purpose)
        return GateDecision(False, "allowed_storage does not include text", source_id, purpose)
    if purpose == "store":
        if storage == "none":
            return GateDecision(False, "storage is not allowed", source_id, purpose)
        return GateDecision(True, f"storage permitted at level {storage}", source_id, purpose)
    if purpose == "transform":
        if source_class in ("A", "B") and bool(record.get("allowed_transformation")):
            return GateDecision(True, "transformation permitted by license", source_id, purpose)
        return GateDecision(False, "transformation is not licensed", source_id, purpose)
    if purpose == "redistribute":
        if source_class in ("A", "B") and bool(record.get("allowed_redistribution")):
            return GateDecision(True, "redistribution permitted by license", source_id, purpose)
        return GateDecision(False, "redistribution is not licensed", source_id, purpose)
    return GateDecision(False, "no rule matched", source_id, purpose)


def reject_media_payload(content_type: object) -> None:
    """Text-only acquisition contract: media payloads are rejected outright."""
    value = str(content_type or "").lower()
    if any(value.startswith(prefix) for prefix in REJECTED_CONTENT_TYPES):
        raise SourcePolicyError(f"media payload rejected by text-only contract: {value}")


def tombstone(record: dict, reason: str, *, affected_pack_ids=(), now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "source_id": str(record.get("source_id")),
        "reason": str(reason),
        "affected_pack_ids": list(affected_pack_ids),
        "tombstoned_at": now.replace(microsecond=0).isoformat(),
        "previous_decision": record.get("decision"),
        "rebuild_required": True,
    }


class PolicyGate:
    """Registry-backed gate. ``registry_dir`` is ``corpus/registry``."""

    def __init__(self, registry_dir: str | Path, tombstone_dir: str | Path | None = None):
        self.registry_dir = Path(registry_dir)
        self.tombstone_dir = Path(tombstone_dir) if tombstone_dir else self.registry_dir.parent / "tombstones"
        self.log: list[dict] = []

    def _load_json(self, path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def sources(self) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        directory = self.registry_dir / "sources"
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                value = self._load_json(path)
                if isinstance(value, dict) and value.get("source_id"):
                    rows[str(value["source_id"])] = value
        return rows

    def tombstoned_ids(self) -> set[str]:
        ids: set[str] = set()
        if self.tombstone_dir.is_dir():
            for path in self.tombstone_dir.glob("*.json"):
                value = self._load_json(path)
                if isinstance(value, dict) and value.get("source_id"):
                    ids.add(str(value["source_id"]))
        return ids

    def check(self, source_id: str, purpose: str, *, now: datetime | None = None) -> GateDecision:
        record = self.sources().get(str(source_id))
        if record is None:
            decision = GateDecision(False, "source is not registered", str(source_id), purpose)
        else:
            decision = evaluate(record, purpose, now=now, tombstoned=self.tombstoned_ids())
        self.log.append({**decision.as_dict(), "checked_at": (now or datetime.now(timezone.utc)).isoformat()})
        return decision

    def assert_allowed(self, source_id: str, purpose: str, *, now: datetime | None = None) -> GateDecision:
        decision = self.check(source_id, purpose, now=now)
        if not decision.allowed:
            raise SourcePolicyError(f"{purpose} blocked for {source_id}: {decision.reason}")
        return decision
