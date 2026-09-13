"""Generate isolated, reproducible editorial QA evidence; never open a save.

Usage: python scripts/verify_editorial.py --output-dir artifacts/<new-run>
All player numbers in this report are synthetic fixtures, not user history.
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import community
import editorial_engine as editorial
import provider_feed
import realism_gate
import spotlight_engine
import story_engine
from tests.test_editorial import CONFIG, all_text, event_for, snapshot, spotlight


def check_report(publication):
    return realism_gate.audit_feed(publication, names=["Paul Skenes", "Skenes"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    target = Path(args.output_dir).resolve()
    target.relative_to(ROOT / "artifacts")
    target.mkdir(parents=True, exist_ok=True)
    samples = []
    cases = [
        ("quiet-established-star", event_for(snapshot())),
        ("pitching-and-hitless-batting", event_for(snapshot(), kind="NEW_GAME", role="two_way", delta={"pit_IP": 9, "pit_K": 27, "pit_TBF": 27, "pit_H": 0, "bat_AB": 4, "bat_H": 0, "bat_HR": 0})),
        ("batting-only", event_for(snapshot(), kind="NEW_GAME", role="batting", delta={"bat_AB": 5, "bat_H": 5, "bat_HR": 5, "bat_RBI": 7})),
    ]
    for label, event in cases:
        samples.append({"case": label, "feed": community.build_feed(event, CONFIG, {}, spotlight=spotlight(event), universe_id="fixture-world")})
    for visibility, category, situation in (("private", "personal", "call_family"), ("public", "media", "praise_team")):
        snap = snapshot()
        selection = story_engine.normalize({"category": category, "situation": situation, "target": "family" if visibility == "private" else "reporter", "visibility": visibility, "user_text": "PRIVATE_FIXTURE_SECRET" if visibility == "private" else "동료 덕분이다"})
        turn = story_engine.build_event(snap, 1, selection, spotlight=spotlight(event_for(snap)), universe_id="fixture-world")
        samples.append({"case": f"{visibility}-intervention", "feed": turn["reactions"]})
    rookie = event_for(snapshot(stats={"bat_AB": 8, "bat_H": 1, "bat_HR": 0}))
    samples.append({"case": "unknown-rookie", "feed": community.build_feed(rookie, CONFIG, {}, spotlight=spotlight_engine.evaluate(rookie["snapshot"], {}, rookie, CONFIG), universe_id="fixture-rookie")})

    memory, cohort, elapsed, old = {}, [], [], []
    old_payload = copy.deepcopy(samples)
    for index in range(60):
        snap = snapshot(digest=f"cohort-{index}")
        day = date(2027, 5, 1) + timedelta(days=index)
        snap["date"].update(year=day.year, month=day.month, day=day.day)
        snap["stats"]["pit_K"] += index
        snap["stats"]["pit_TBF"] += index * 3
        snap["stats"]["pit_IP"] += index
        event = event_for(snap)
        start = time.perf_counter()
        publication = editorial.build(event, universe_id="cohort-world", budget={"media": 6, "boards": 7, "comments": 38}, memory=memory, heat=10, platforms=("dc", "fmk", "mlb"))
        elapsed.append((time.perf_counter() - start) * 1000)
        prior = memory.get(editorial.MEMORY_KEY, {}).get("articles", [])
        violations = []
        for article in publication["media"]:
            if any(article["headline_skeleton"] == row["skeleton"] for row in prior):
                violations.append("recent_headline_skeleton")
            if any(editorial._near_body(article["body_signature"], row["body_signature"]) for row in prior):
                violations.append("recent_body_clone")
        audit = check_report(publication)
        if not realism_gate.feed_is_clean(audit):
            violations.append("public_prose_gate")
        violations.extend(provider_feed.platform_register_violations(publication))
        cohort.append({"date": day.isoformat(), "articles": len(publication["media"]), "boards": len(publication["boards"]), "comments": sum(len(b["comments"]) for b in publication["boards"]), "purposes": [a["editorial_purpose"] for a in publication["media"]], "capacity_limited": publication["editorial"]["capacity_limited"], "violations": violations})
        old.append(copy.deepcopy(publication))
        editorial.remember(memory, publication)
    press, discussion, manifest = editorial.packs()
    report = {
        "fixture_only": True, "renderer_version": editorial.VERSION, "corpus_manifest": manifest,
        "samples": [{"case": s["case"], "audit": check_report(s["feed"]),
                     "platform_register_violations": provider_feed.platform_register_violations(s["feed"])}
                    for s in samples],
        "privacy_secret_absent": "PRIVATE_FIXTURE_SECRET" not in all_text(samples[3]["feed"]),
        "original_samples_unchanged": samples == old_payload,
        "cohort": cohort,
        "cohort_totals": {"requested_articles": 360, "produced_articles": sum(r["articles"] for r in cohort), "requested_comments": 2280, "produced_comments": sum(r["comments"] for r in cohort), "capacity_limited_publications": sum(r["capacity_limited"] for r in cohort), "quality_failures": sum(bool(r["violations"]) for r in cohort)},
        "latency_ms": {"median": round(statistics.median(elapsed), 2), "p95": round(sorted(elapsed)[int(len(elapsed) * .95) - 1], 2), "max": round(max(elapsed), 2)},
        "authored_pack": {"purposes": len(press["purposes"]), "headlines": sum(len(p["headlines"]) for p in press["purposes"]), "korean_move_variants": sum(len(p[k]) + len(p["public_moves"][k]) for p in press["purposes"] if p["lang"] == "ko" for k in ("opening", "reading", "caution", "next")), "bilingual_pairs": sum(len(group) for p in press["purposes"] if p["lang"] != "ko" for group in p["pair_variants"]), "reply_variants": sum(len(v) for v in discussion["replies"].values()) + sum(len(v) for voice in discussion["voices"].values() for v in voice.values()) + sum(len(v) for voice in discussion.get("hot_voices", {}).values() for v in voice.values()), "platform_titles": sum(len(v) for v in discussion["platform_titles"].values()), "platform_openers": sum(len(v) for v in discussion["platform_openers"].values())},
        "routing_memory": {"articles": len(memory[editorial.MEMORY_KEY]["articles"]), "phrases": len(memory[editorial.MEMORY_KEY]["phrases"]), "bytes": len(json.dumps(memory, ensure_ascii=False).encode())},
        "limitations": ["Finite authored corpus; exhausted candidates are not padded.", "Public-button foreign translation is deferred until action-specific pairs exist.", "This gate does not certify full Phase 5/6, unrestricted chat, or literary quality.", "No network, models, OCR, real saves, or user archives are touched."],
    }
    report["ok"] = (all(realism_gate.feed_is_clean(s["audit"]) and not s["platform_register_violations"] for s in report["samples"])
                    and report["privacy_secret_absent"] and report["original_samples_unchanged"]
                    and report["cohort_totals"]["quality_failures"] == 0)
    (target / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Editorial QA: synthetic fixtures", "", "These are original fictional publications, not actual reports or user history.", ""]
    for sample in samples:
        lines += [f"## {sample['case']}", ""]
        for article in sample["feed"]["media"]:
            lines += [f"### {article['title']}", "", f"{article['outlet']} · {article['editorial_purpose']}", "", *[p + "\n" for p in article["body"]]]
        for board in sample["feed"]["boards"][:2]:
            lines += [f"### {board['title']}", ""]
            lines += [f"- {p['author']}: {p['text']}\n" for p in board["comments"]]
    (target / "sample-preview.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("ok", "cohort_totals", "latency_ms", "authored_pack", "routing_memory")}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
