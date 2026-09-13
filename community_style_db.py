#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validated, derived-only community register evidence for model prompts.

The bundled database intentionally stores no source posts, comments, handles,
or screenshots. Runtime consumers receive abstract writing and interaction
features only; URLs and observation metadata remain an auditable research
ledger for maintainers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path


SCHEMA_VERSION = 1
DATABASE_PATH = Path(__file__).resolve().parent / "data" / "editorial" / "community-style-profiles-v1.json"
REQUIRED_PROFILES = (
    "dc",
    "fmk",
    "mlb",
    "x",
    "threads",
    "instagram",
    "facebook",
    "japan-translation",
    "global-translation",
)
_FORBIDDEN_STORAGE_KEYS = {
    "raw_posts",
    "raw_comments",
    "raw_text",
    "quoted_posts",
    "usernames",
    "screenshots",
    "images",
}

LANGUAGE_LEVELS = {
    1: {
        "label": "정중",
        "instruction": "욕설·비속어 없이 일상적인 야구 대화로 쓴다.",
    },
    2: {
        "label": "자연스러운 구어",
        "instruction": "초성·은어·가벼운 놀림은 허용하되 직접적인 욕설은 쓰지 않는다.",
    },
    3: {
        "label": "거친 커뮤니티",
        "instruction": "플랫폼에 맞는 거친 반말과 약한 비속어를 드물게 허용한다.",
    },
    4: {
        "label": "상스러운 현실",
        "instruction": "디시·에펨코 계열에서는 강한 비속어와 욕설도 자연스럽게 섞되 모든 댓글을 욕설로 채우지 않는다.",
    },
    5: {
        "label": "극한 커뮤니티",
        "instruction": "플랫폼 현실성에 필요한 매우 거친 욕설·조롱·감탄을 허용한다. 다만 위협·신상털기·보호대상 혐오·성적 폭력 표현은 금지한다.",
    },
}

_PLATFORM_LANGUAGE_RULES = {
    "dc": "짧은 반말·생략·초성체를 우선하며 높은 단계에서는 날것의 욕설을 일부 허용한다.",
    "fmk": "친근한 반말·밈·포텐식 공방을 우선하며 높은 단계에서도 디시 익명체를 그대로 복제하지 않는다.",
    "mlb": "높은 단계에서도 존댓말과 비교 논증을 유지한다. 수위는 날카로운 비꼼과 단정성으로 표현한다.",
    "x": "짧은 단정과 인용 반박을 유지하고 욕설은 높은 단계에서만 드물게 쓴다.",
    "threads": "대화 사슬을 유지하며 높은 단계에서도 상대 문장을 받아 반박한다.",
    "instagram": "캡션 리듬을 유지하며 상스러운 표현을 억지로 삽입하지 않는다.",
    "facebook": "완결된 구어체와 경험담을 유지하며 높은 단계는 강한 반론으로 표현한다.",
    "japan-translation": "원문 문화권의 거리감을 보존하고 한국식 욕설을 임의로 덧씌우지 않는다.",
    "global-translation": "선택한 문화권의 리듬을 보존하고 서로 다른 나라의 욕설 표식을 섞지 않는다.",
}


class CommunityStyleDatabaseError(ValueError):
    """The bundled style evidence is malformed or violates storage policy."""


def _stable_index(selector: object, size: int) -> int:
    if size <= 1:
        return 0
    digest = hashlib.sha256(str(selector or "default").encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % size


def _validate_string_list(value: object, label: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise CommunityStyleDatabaseError(f"{label} must contain at least {minimum} entries")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CommunityStyleDatabaseError(f"{label} contains a blank or non-string entry")
        result.append(item.strip())
    return result


def _validate(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise CommunityStyleDatabaseError("community style database must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise CommunityStyleDatabaseError("unsupported community style database schema")
    policy = raw.get("storage_policy")
    if not isinstance(policy, dict) or policy.get("mode") != "derived_features_only":
        raise CommunityStyleDatabaseError("community style database must be derived-features-only")
    for key in ("raw_posts_stored", "usernames_stored", "images_stored"):
        if policy.get(key) is not False:
            raise CommunityStyleDatabaseError(f"storage policy must disable {key}")

    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CommunityStyleDatabaseError("community style database has no source ledger")
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise CommunityStyleDatabaseError("source ledger rows must be objects")
        source_id = str(source.get("id") or "").strip()
        url = str(source.get("url") or "").strip()
        if not source_id or source_id in source_ids:
            raise CommunityStyleDatabaseError("source IDs must be non-empty and unique")
        if not url.startswith("https://"):
            raise CommunityStyleDatabaseError(f"source {source_id} must use an HTTPS URL")
        _validate_string_list(source.get("scope"), f"source {source_id} scope")
        if _FORBIDDEN_STORAGE_KEYS.intersection(source):
            raise CommunityStyleDatabaseError(f"source {source_id} contains raw-content storage")
        source_ids.add(source_id)

    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise CommunityStyleDatabaseError("community style profiles must be an object")
    missing = [code for code in REQUIRED_PROFILES if code not in profiles]
    if missing:
        raise CommunityStyleDatabaseError(f"missing community style profiles: {', '.join(missing)}")
    for code, profile in profiles.items():
        if not isinstance(profile, dict):
            raise CommunityStyleDatabaseError(f"profile {code} must be an object")
        evidence = _validate_string_list(
            profile.get("evidence_source_ids"), f"profile {code} evidence_source_ids"
        )
        unknown = sorted(set(evidence) - source_ids)
        if unknown:
            raise CommunityStyleDatabaseError(f"profile {code} references unknown sources: {unknown}")
        register = profile.get("register")
        if not isinstance(register, dict) or len(register) < 5:
            raise CommunityStyleDatabaseError(f"profile {code} needs a detailed register")
        if _FORBIDDEN_STORAGE_KEYS.intersection(register):
            raise CommunityStyleDatabaseError(f"profile {code} contains raw-content storage")
        _validate_string_list(profile.get("ui_cues"), f"profile {code} ui_cues")
        variants = profile.get("variants", [])
        if not isinstance(variants, list):
            raise CommunityStyleDatabaseError(f"profile {code} variants must be a list")
        for variant in variants:
            if not isinstance(variant, dict) or not all(
                isinstance(variant.get(key), str) and variant[key].strip()
                for key in ("id", "label", "rhythm")
            ):
                raise CommunityStyleDatabaseError(f"profile {code} has an invalid variant")
    return raw


@lru_cache(maxsize=1)
def load() -> dict:
    raw_bytes = DATABASE_PATH.read_bytes()
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommunityStyleDatabaseError(f"cannot read community style database: {exc}") from None
    value = _validate(parsed)
    value["manifest_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    return value


def manifest() -> dict:
    value = load()
    return {
        "schema_version": value["schema_version"],
        "dataset_version": str(value.get("dataset_version") or ""),
        "updated_at": str(value.get("updated_at") or ""),
        "source_count": len(value["sources"]),
        "profile_count": len(value["profiles"]),
        "storage_mode": value["storage_policy"]["mode"],
        "language_level_count": len(LANGUAGE_LEVELS),
        "manifest_sha256": value["manifest_sha256"],
    }


def language_policy(level: object, code: str = "") -> dict:
    """Return the explicit vocabulary policy for one platform.

    The setting is a ceiling and register selector, not permission to invent
    facts or target protected groups. Platform rhythm still wins: a high-level
    MLBPARK response stays formally sharp while a DC response may be raw.
    """

    try:
        normalized = max(1, min(5, int(level)))
    except (TypeError, ValueError):
        normalized = 2
    return {
        "level": normalized,
        "label": LANGUAGE_LEVELS[normalized]["label"],
        "instruction": LANGUAGE_LEVELS[normalized]["instruction"],
        "platform_rule": _PLATFORM_LANGUAGE_RULES.get(
            str(code),
            "해당 표면의 고유한 문장 길이와 사회적 거리감을 우선한다.",
        ),
        "applies_to": "community_and_social_only",
        "never_allowed": ["보호대상 혐오", "현실 위해·협박", "신상털기", "성적 폭력 표현"],
    }


def source_ledger() -> list[dict]:
    """Return a copy for maintenance reports; runtime prompts never call this."""

    return copy.deepcopy(load()["sources"])


def prompt_profile(code: str, *, selector: object = "", language_level: object = 2) -> dict:
    """Return a compact derived profile safe to include in either LLM prompt."""

    database = load()
    profile = database["profiles"].get(str(code))
    if profile is None:
        return {
            "database_version": database["dataset_version"],
            "code": str(code),
            "display_name": "일반 야구 커뮤니티",
            "surface": "unknown",
            "register": {
                "syntax": "해당 표면의 문장 길이와 대화 관습을 유지한다.",
                "turn_shape": "감탄, 질문, 근거, 이견을 서로 다른 목소리로 배치한다.",
                "avoid": ["기사 요약체의 반복", "모든 댓글의 동일한 길이와 결론"],
            },
            "language_policy": language_policy(language_level, str(code)),
        }
    result = {
        "database_version": database["dataset_version"],
        "code": str(code),
        "display_name": profile.get("display_name"),
        "surface": profile.get("surface"),
        "locale": profile.get("locale"),
        "confidence": profile.get("confidence"),
        "register": copy.deepcopy(profile.get("register") or {}),
        "ui_cues": copy.deepcopy(profile.get("ui_cues") or []),
        "language_policy": language_policy(language_level, str(code)),
    }
    variants = profile.get("variants") or []
    if variants:
        result["selected_origin_register"] = copy.deepcopy(
            variants[_stable_index(selector, len(variants))]
        )
    return result
