"""Conservative save-delta to narrative-event bridge; no persistence or I/O."""

from __future__ import annotations

import copy
from datetime import date

import dominant_event
import narrative_contracts as nc
import story_engine

VERSION = "1.0.0"


def plan(event: dict, previous: dict | None, *, universe_id: str) -> dict | None:
    if event.get("kind") not in ("NEW_GAME", "SEASON_UPDATE") or event.get("baseline_only") or not previous:
        return None
    snapshot = event["snapshot"]
    if (snapshot.get("player") or {}).get("id") != (previous.get("player") or {}).get("id"):
        return None
    current_date = story_engine.date_key(snapshot)
    previous_date = story_engine.date_key(previous)
    gap = (date.fromisoformat(current_date) - date.fromisoformat(previous_date)).days
    # An interval spanning multiple dates does not identify a specific game.
    # Keep the legacy cumulative feed, but do not invent a dated callback.
    if gap not in (0, 1) or current_date[:4] != previous_date[:4]:
        return None
    if (snapshot.get("date") or {}).get("career_year") != (previous.get("date") or {}).get("career_year"):
        return None
    delta = event.get("delta") or {}
    if not delta or any(value < 0 for value in delta.values()) or event.get("role") not in ("batting", "pitching", "two_way"):
        return None
    if not snapshot.get("content_hash") or not previous.get("content_hash"):
        return None
    if any((snapshot.get("stats") or {}).get(key) is None or (previous.get("stats") or {}).get(key) is None for key in delta):
        return None
    required = []
    if event["role"] in ("pitching", "two_way"):
        required.extend(("pit_IP", "pit_TBF", "pit_H", "pit_K"))
    if event["role"] in ("batting", "two_way"):
        required.extend(("bat_AB", "bat_H", "bat_HR", "bat_RBI"))
    if any((snap.get("stats") or {}).get(key) is None for snap in (previous, snapshot) for key in required):
        return None
    if delta.get("bat_AB", 0) and not 0 <= delta.get("bat_HR", 0) <= delta.get("bat_H", 0) <= delta["bat_AB"]:
        return None
    delta_event = dict(event, delta_only=True)
    candidates = dominant_event.extract_game_candidates(delta_event)
    if not candidates:
        return None
    candidates.append(dominant_event.candidate("SP.GAME.POST.RECAP", "새로 확인된 경기 기록", verified=True, domain_role="none"))
    source_ids = [f"snapshot:{previous['content_hash']}", f"snapshot:{snapshot['content_hash']}"]
    delta_event["source_facts"] = source_ids
    instance_id = nc.stable_id("save-delta-narrative", VERSION, universe_id, *source_ids)
    events = []
    for candidate in candidates:
        event_type = candidate["event_type"]
        label = candidate["label"]
        events.append(nc.new_event(
            event_type=event_type, game_date=current_date, universe_id=universe_id,
            protagonist_id=(snapshot.get("player") or {}).get("id"), actor="protagonist",
            visibility="national", evidence_class="derived_analysis", protagonist_relation="direct_actor",
            source_facts=source_ids + list(candidate.get("fact_ids") or []),
            event_id=nc.stable_id(instance_id, event_type, label),
            payload={"label": label, "delta": copy.deepcopy(delta), "source_hash": snapshot["content_hash"], "previous_hash": previous["content_hash"], "observed_from": previous_date, "observed_on": current_date, "formula": f"verified_snapshot_delta/{VERSION}", "instance_id": instance_id},
        ))
    return {"instance_id": instance_id, "game_date": current_date, "source_hash": snapshot["content_hash"], "event": delta_event, "events": events}


def overlay(feed: dict, bundle: dict | None) -> dict:
    """Add the new reaction layer without removing legacy feed contracts."""
    if not bundle:
        return feed
    result = copy.deepcopy(feed)
    result["game_narrative"] = copy.deepcopy(bundle)
    editorial = result.get("editorial") or {}
    binding = editorial.get("binding") or {}
    if binding and any(binding.get(key) != str(bundle.get(key)) for key in ("universe_id", "protagonist_id", "game_date")):
        # A bundle from another protagonist/date must never enter this feed.
        return copy.deepcopy(feed)
    if (bundle.get("instance_id") and editorial.get("game_instance_id") == bundle["instance_id"]
            and binding.get("universe_id") == bundle.get("universe_id")
            and binding.get("protagonist_id") == str(bundle.get("protagonist_id"))
            and binding.get("game_date") == bundle.get("game_date")):
        # New publication has already allocated this game's dominant evidence.
        # Keep its original bundle for provenance/callbacks without publishing
        # a second set of parallel recaps. Legacy feeds still use the overlay.
        return result
    for key in ("boards", "media"):
        result[key] = copy.deepcopy((bundle.get("reactions") or {}).get(key) or []) + result.get(key, [])
    return result
