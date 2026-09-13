"""Create-only synthetic QA for four-action emotional and public continuity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import editorial_engine as editorial
import interaction_voice as voice
import narrative_contracts as nc
import realism_gate
import star_interactions as interactions

STEPS = [
    ("joke", "농담을 건네고 싶어"), ("joke", "웃긴 얘기를 해 볼게"),
    ("confide", "솔직히 요즘은 좀 걱정돼"), ("disagree", "그 생각에는 반대야"),
    ("joke", "그래도 농담을 하나 해 볼까"), ("reconcile", "내가 지나쳤어. 미안해"),
    ("agree", "좋아, 그 부분은 동의해"), ("promise", "다음 홈런 치면 커피를 사줄게 약속"),
    ("continue", "더 이야기하자"), ("pause", "잠시 멈추자"),
    ("resume", "다시 이어가자"), ("change", "약속을 취소할게"),
    ("close", "오늘 대화는 마무리하자"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    target = Path(args.output_dir).resolve()
    target.relative_to(ROOT / "artifacts")
    target.mkdir(parents=True, exist_ok=False)
    snapshot = {"player": {"id": 123, "name": "가상 주인공", "team": "가상 구단"},
                "date": {"year": 2027, "month": 7, "day": 24}, "stats": {}, "content_hash": "voice-cohort-fixture"}
    cases, checks, timings = [], [], []

    def check(name: str, condition: bool):
        checks.append({"name": name, "passed": bool(condition)})

    for action in interactions.pack()["actions"]:
        for role in interactions.ROLES:
            if action in ("learning", "player_exchange") and role not in ("teammate", "rookie", "rival"):
                continue
            state, scenes = {}, []
            case_id, game_date = f"{action}:{role}", "2027-07-24"
            world_id = f"synthetic:{case_id}"
            topic = "커브를 설명하는 감각" if action == "learning" else "햄버거" if action == "player_exchange" else "야구장 밖의 시간"
            proposed = interactions.start_plan({"situation": action, "target": role,
                                               "participant_name": "" if role == "self" else interactions.ROLES[role],
                                               "interaction_topic": topic}, snapshot=snapshot, game_date=game_date,
                                              universe_id=world_id, state=state)
            ident = proposed["interaction_id"]
            for index, (expected, text) in enumerate([("start", ""), *STEPS]):
                before = copy.deepcopy(state)
                started = time.perf_counter()
                if index:
                    response = interactions.chat_plan(state, {"interaction_id": ident, "user_text": text},
                                                      snapshot=snapshot, game_date=game_date, universe_id=world_id, sequence=index)
                    check(f"{case_id}:{index}:preview-pure", before == state)
                    check(f"{case_id}:{index}:proposal-present", bool(response["proposed_events"]))
                    if not response["proposed_events"]:
                        break
                    proposed = response["proposed_events"][0]
                    check(f"{case_id}:{index}:intent", proposed["beat"] == expected)
                result = interactions.realized_event(state, proposed, snapshot=snapshot, game_date=game_date,
                                                     universe_id=world_id, spotlight={})
                timings.append((time.perf_counter() - started) * 1000)
                if index:
                    check(f"{case_id}:{index}:preview-equals-commit", response["reply"]["blocks"][1:] == result["blocks"])
                check(f"{case_id}:{index}:blocks", not nc.validate_blocks(result["blocks"]) and all(len(b["text"]) <= 650 for b in result["blocks"]))
                check(f"{case_id}:{index}:private", not any(result["reactions"].get(k) for k in ("media", "boards", "waves", "foreign")))
                check(f"{case_id}:{index}:protagonist", snapshot["player"]["name"] in result["text"])
                check(f"{case_id}:{index}:not-immediate-clone", not scenes or result["text"] != scenes[-1]["text"])
                for block in result["blocks"]:
                    if block.get("template_id"):
                        _pack, path = block["template_id"].split(":", 1)
                        check(f"{case_id}:{index}:template:{path}", isinstance(voice.node(interactions.pack(), path), str))
                interactions.commit(state, proposed, snapshot=snapshot, game_date=game_date, universe_id=world_id,
                                    event_id=f"synthetic-event:{case_id}:{index}")
                check(f"{case_id}:{index}:old-events-unchanged", before.get(interactions.EVENTS, []) == state[interactions.EVENTS][:-1])
                scenes.append({"beat": expected, "text": result["text"], "blocks": result["blocks"],
                               "expression": result["provenance"]["expression"]})
            cases.append({"case": case_id, "scenes": scenes})
    check("complete-scene-matrix", sum(len(case["scenes"]) for case in cases) == 280)

    publications = []
    for action in editorial.packs()[0]["public_actions"]:
        memory = {}
        for index in range(8):
            publication = editorial.build({"snapshot": snapshot, "kind": "STORY", "role": "no_appearance", "delta": {}},
                                          budget={"media": 6, "boards": 8, "comments": 40}, universe_id="synthetic-public",
                                          story={"situation": action, "visibility": "public", "user_text": "PRIVATE_SECRET"},
                                          story_id=f"{action}:{index}", memory=memory)
            check(f"{action}:{index}:public-audit", realism_gate.feed_is_clean(publication["editorial"]["audit"]))
            check(f"{action}:{index}:privacy", "PRIVATE_SECRET" not in json.dumps(publication, ensure_ascii=False))
            publications.append({"action": action, "sequence": index, "feed": publication})
            editorial.remember(memory, publication)

    expression_strings = []

    def collect(value):
        if isinstance(value, str):
            expression_strings.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(interactions.pack()["continuity"])
    report = {
        "ok": all(row["passed"] for row in checks), "fixture_only": True,
        "scene_cases": len(cases), "scene_count": sum(len(case["scenes"]) for case in cases),
        "checks": len(checks), "failures": [row for row in checks if not row["passed"]],
        "finite_reuse_scenes": sum(bool(scene["expression"]["reused_groups"]) for case in cases for scene in case["scenes"]),
        "new_authored_scene_strings": len(expression_strings),
        "publications": len(publications), "public_articles": sum(len(row["feed"]["media"]) for row in publications),
        "public_comments": sum(len(b["comments"]) for row in publications for b in row["feed"]["boards"]),
        "capacity_limited_publications": sum(row["feed"]["editorial"]["capacity_limited"] for row in publications),
        "private_plan_and_render_ms": {"median": round(statistics.median(timings), 3),
                                       "p95": round(sorted(timings)[int(len(timings) * .95) - 1], 3), "max": round(max(timings), 3)},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                          ("interaction_voice.py", "star_interactions.py", "editorial_engine.py", "story_engine.py", "service.py")},
        "interaction_manifest": interactions.manifest_hash(), "editorial_manifest": editorial.packs()[2],
        "limits": ["Finite authored strings; reuse/capacity is reported, not padded.",
                   "No live save, user state, network, model, OCR, GUI or game was used.",
                   "Measured pure synthetic planning/rendering, not end-to-end app latency or unrestricted language quality."],
    }
    for name, value in (("qa.json", report), ("samples.json", {"cases": cases, "publications": publications})):
        with (target / name).open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
