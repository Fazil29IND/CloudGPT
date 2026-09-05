"""
CloudGPT Model-Capability Evaluation Slice.

Tests raw LLM output quality with NO retrieval, NO web search, and NO tools.
Only the model, a minimal system prompt, and the question.

Purpose: establish the model's standalone reasoning ceiling on comparative
cloud-service questions before pipeline-level optimization obscures it.

Usage:
    python evaluation/model_eval.py              # full sample
    python evaluation/model_eval.py --tier Max   # specific tier
    python evaluation/model_eval.py --dry-run    # print questions only, no LLM calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_settings
from llm.provider import get_llm_provider
from llm.system_prompts import CLOUD_AGENT_SYSTEM_PROMPT

logger = logging.getLogger("evaluation.model_eval")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Minimum accepted accuracy on the first run (baseline-setting pass uses 0.0).
# Once a baseline is set in config, runs that drop > 5 pp below it are flagged.
REGRESSION_MARGIN = 0.05


def _load_golden_set(settings) -> list[dict]:
    """Load and validate the golden set. Returns questions with ground truth answer or expected keywords."""
    path = BASE_DIR / settings.model_eval_golden_set_path
    if not path.exists():
        raise FileNotFoundError(f"Golden set not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    labeled = [
        q for q in data 
        if q.get("answer") or q.get("expected_answer") or q.get("expected_keywords")
    ]
    if not labeled:
        raise ValueError(
            "Golden set has no entries with 'answer' or 'expected_answer' or 'expected_keywords' fields."
        )
    return labeled


def _sample_questions(questions: list[dict], sample_size: int) -> list[dict]:
    """Deterministic sample by stride so results are reproducible across runs."""
    if len(questions) <= sample_size:
        return questions
    stride = len(questions) // sample_size
    return [questions[i] for i in range(0, len(questions), stride)][:sample_size]


def _score_answer(predicted: str, ground_truth: str | list[str]) -> float:
    """
    Lightweight accuracy scorer.

    Returns 1.0 if the ground-truth label (or key terms from it) appears in the
    predicted answer. Returns 0.0 otherwise.
    """
    pred_lower = predicted.lower()
    if isinstance(ground_truth, list):
        if not ground_truth:
            return 1.0
        hits = sum(1 for kw in ground_truth if kw.lower() in pred_lower)
        return 1.0 if (hits / len(ground_truth)) >= 0.5 else 0.0

    truth_lower = ground_truth.lower()

    # Exact substring match
    if truth_lower in pred_lower:
        return 1.0

    # Key-term match: split ground truth into tokens and check coverage
    tokens = [t for t in truth_lower.split() if len(t) > 3]
    if not tokens:
        return 1.0 if truth_lower in pred_lower else 0.0
    matches = sum(1 for t in tokens if t in pred_lower)
    coverage = matches / len(tokens)
    return 1.0 if coverage >= 0.6 else 0.0


async def _evaluate_question(
    llm,
    question: dict,
    tier: str,
) -> dict[str, Any]:
    """Run one question through the model and score the result."""
    query = question.get("query") or question.get("question", "")
    ground_truth = question.get("answer") or question.get("expected_answer") or question.get("expected_keywords", "")
    category = question.get("category", "unknown")

    messages = [
        {"role": "system", "content": CLOUD_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    t0 = time.perf_counter()
    try:
        raw = await asyncio.wait_for(
            llm.generate(messages=messages, stream=False, temperature=0.0),
            timeout=30.0,
        )
        predicted = raw if isinstance(raw, str) else ""
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        score = _score_answer(predicted, ground_truth)
        return {
            "query": query[:120],
            "category": category,
            "score": score,
            "latency_ms": latency_ms,
            "error": None,
        }
    except asyncio.TimeoutError:
        return {"query": query[:120], "category": category, "score": 0.0,
                "latency_ms": 30_000.0, "error": "timeout"}
    except Exception as exc:
        return {"query": query[:120], "category": category, "score": 0.0,
                "latency_ms": 0.0, "error": str(exc)}


async def run_model_eval(tier: str = "Pro", dry_run: bool = False) -> dict[str, Any]:
    """Run the model-capability evaluation slice."""
    settings = get_settings()

    questions = _load_golden_set(settings)
    sample = _sample_questions(questions, settings.model_eval_sample_size)

    print(f"\n{'='*60}")
    print(f"  Model-Capability Eval  |  tier={tier}  |  n={len(sample)}")
    print(f"  Model: {settings.gemini_model_apex if tier in ('Max', 'Developer') else settings.gemini_model_core}")
    print(f"  Mode: {'DRY RUN — no LLM calls' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    if dry_run:
        for i, q in enumerate(sample):
            print(f"  [{i+1:3d}] {q.get('query', q.get('question', ''))[:100]}")
        print(f"\n{len(sample)} questions would be evaluated.")
        return {"sample_size": len(sample), "dry_run": True}

    llm = get_llm_provider("main", tier)

    results = []
    for i, question in enumerate(sample):
        result = await _evaluate_question(llm, question, tier)
        results.append(result)
        status = "✓" if result["score"] == 1.0 else ("!" if result["error"] else "✗")
        print(f"  [{i+1:3d}] {status}  {result['query'][:80]}")

    # Aggregate
    scored = [r for r in results if r["error"] is None]
    accuracy = sum(r["score"] for r in scored) / len(scored) if scored else 0.0
    avg_latency = sum(r["latency_ms"] for r in scored) / len(scored) if scored else 0.0

    by_category: dict[str, dict] = {}
    for r in scored:
        cat = r["category"]
        if cat not in by_category:
            by_category[cat] = {"correct": 0, "total": 0}
        by_category[cat]["total"] += 1
        by_category[cat]["correct"] += int(r["score"] == 1.0)
    for cat, stats in by_category.items():
        stats["accuracy"] = round(stats["correct"] / stats["total"], 3)

    baseline = settings.model_eval_accuracy_baseline
    regression_flag = baseline > 0.0 and (accuracy < baseline - REGRESSION_MARGIN)

    print(f"\n{'='*60}")
    print(f"  Accuracy:      {accuracy:.1%}  ({sum(r['score'] == 1.0 for r in scored)}/{len(scored)})")
    print(f"  Avg latency:   {avg_latency:.0f} ms")
    print(f"  Errors:        {sum(1 for r in results if r['error'])}")
    if baseline > 0.0:
        delta = accuracy - baseline
        flag = " ⚠ REGRESSION" if regression_flag else " ✓"
        print(f"  vs baseline:   {delta:+.1%}{flag}")
    print("\n  By category:")
    for cat, stats in sorted(by_category.items()):
        print(f"    {cat:<25} {stats['accuracy']:.1%}  ({stats['correct']}/{stats['total']})")
    print(f"{'='*60}\n")

    if baseline == 0.0:
        print(
            "  ℹ  No baseline set. This run establishes the first baseline.\n"
            "     Set MODEL_EVAL_ACCURACY_BASELINE in .env to lock it in.\n"
        )

    return {
        "accuracy": round(accuracy, 4),
        "sample_size": len(sample),
        "scored": len(scored),
        "avg_latency_ms": round(avg_latency, 1),
        "by_category": by_category,
        "regression_flag": regression_flag,
        "baseline": baseline,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CloudGPT model-capability evaluation")
    parser.add_argument("--tier", default="Pro", choices=["Free", "Pro", "Max"],
                        help="Tier to evaluate (selects generation model)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print questions without making LLM calls")
    args = parser.parse_args()

    result = asyncio.run(run_model_eval(tier=args.tier, dry_run=args.dry_run))
    if result.get("regression_flag"):
        sys.exit(1)
