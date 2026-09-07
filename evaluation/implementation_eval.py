"""Implementation-capability evaluation: per-tier deployability rate.

Measures how often generated IaC artifacts pass the layered validator stack,
per tier, over the implementation golden set. Requires live LLM access —
skipped in the hermetic suite (LIVE_API_TESTS gate).

Usage:
    LIVE_API_TESTS=1 python evaluation/implementation_eval.py --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import os

if os.getenv("LIVE_API_TESTS") != "1":
    print("Live evaluation requires LIVE_API_TESTS=1 (real LLM + validator binaries).")
    sys.exit(0)


async def evaluate(limit: int | None, tiers: list[str]) -> dict:
    from api.chat_routes import execute_agent_pipeline
    from tools.iac_validator import detect_artifact_type, validate_artifact

    tasks = json.loads((BASE_DIR / "evaluation" / "implementation_golden_set.json").read_text(encoding="utf-8"))
    results: dict[str, dict] = {}

    for tier in tiers:
        tier_tasks = [t for t in tasks if t["tier_target"].lower() == tier.lower()]
        if limit:
            tier_tasks = tier_tasks[:limit]
        passed = 0
        records = []
        for t in tier_tasks:
            started = time.perf_counter()
            try:
                result = await execute_agent_pipeline(
                    query=t["task"],
                    tier=tier,
                    stream=False,
                )
                from api.artifacts import extract_artifacts_from_text

                arts = extract_artifacts_from_text(result.answer or "")
                validations = [
                    validate_artifact(a["content"], a.get("artifact_type") or detect_artifact_type(a["content"]))
                    for a in arts
                ]
                all_valid = bool(validations) and all(v.valid for v in validations)
                elapsed = time.perf_counter() - started
                passed += 1 if all_valid else 0
                records.append({
                    "id": t["id"], "valid": all_valid, "artifacts": len(arts),
                    "findings": [f.to_dict() for v in validations for f in v.findings][:10],
                    "seconds": round(elapsed, 1),
                })
            except Exception as exc:
                records.append({"id": t["id"], "valid": False, "error": str(exc)[:200]})
        results[tier] = {
            "total": len(tier_tasks),
            "validator_passed": passed,
            "deployability_rate": round(passed / len(tier_tasks) * 100, 1) if tier_tasks else 0.0,
            "records": records,
        }
    return {
        "measured_at": datetime.utcnow().isoformat(),
        "note": "Deployability = artifact passes the available validator layers (missing binaries report skipped, never passed)",
        "tiers": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-tier IaC deployability evaluation")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tiers", default="Apex,Core,Lite")
    args = parser.parse_args()

    report = asyncio.run(evaluate(args.limit, args.tiers.split(",")))
    out = BASE_DIR / "evaluation" / "implementation_eval_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for tier, stats in report["tiers"].items():
        print(f"{tier}: {stats['validator_passed']}/{stats['total']} validator-passed "
              f"({stats['deployability_rate']}% deployability)")
    print(f"Full report: {out}")


if __name__ == "__main__":
    main()
