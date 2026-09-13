"""Create-only intent fixture report; no world, service, network or model use."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interaction_intent as intent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    target = Path(args.output_dir).resolve()
    target.relative_to(ROOT / "artifacts")
    target.mkdir(parents=True, exist_ok=False)
    tree = ast.parse((ROOT / "tests/test_interaction_intent.py").read_text(encoding="utf-8"))
    tables = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in ("POSITIVE_CASES", "COMPOSITE_CASES", "NO_ACTION_CASES")}
    cases = [(text, "equals", beat) for text, beat in tables["POSITIVE_CASES"]]
    cases += [(text, "equals", beat) for text, beat, _ in tables["COMPOSITE_CASES"]]
    cases += [(text, "equals", None) for text in tables["NO_ACTION_CASES"]]
    for word, denied in (("농담", "joke"), ("사과", "reconcile"), ("화해", "reconcile"),
                         ("반대", "disagree"), ("거절", "refuse"), ("공개", "share")):
        for ending in ("하지 마", "하지 않을래", "하지 않았어", "는 안 할래", "할 생각은 없어", "하고 싶지 않아"):
            cases.append((word + ending, "not_equals", denied))
    samples, timings = [], []
    for text, comparison, expected in cases:
        started = time.perf_counter()
        result = intent.analyze(text)
        timings.append((time.perf_counter() - started) * 1000)
        passed = result["beat"] == expected if comparison == "equals" else result["beat"] != expected
        samples.append({"input": text, "comparison": comparison, "expected": expected, "passed": passed,
                        "deterministic": result == intent.analyze(text), "analysis": result})
    failures = [row["input"] for row in samples if not row["passed"] or not row["deterministic"]]
    report = {"ok": not failures, "cases": len(samples), "failures": failures, "parser_version": intent.VERSION,
              "latency_ms": {"median": round(statistics.median(timings), 3),
                             "p95": round(sorted(timings)[int(len(timings) * .95) - 1], 3), "max": round(max(timings), 3)},
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("interaction_intent.py", "star_interactions.py", "service.py", "backend_check.py")},
              "limits": ["Shares fixed examples with regression tests; not an independent language-accuracy benchmark.",
                         "No service, user state, game, model or network. No claim of unrestricted Korean understanding."]}
    for name, value in (("qa.json", report), ("samples.json", samples)):
        with (target / name).open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
