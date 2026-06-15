#!/usr/bin/env python3
"""Token budget optimization test harness.

Runs a single persona (GrowthHunter by default) on the same ticker
at different max_tokens budgets, multiple runs each, and measures:
  - Actual tokens consumed (prompt + completion)
  - Output quality (field completeness, evidence citations, reasoning depth)
  - Variance across runs (probability drift, verdict stability)
  - Cost per run

Usage:
    python scripts/token_budget_test.py --ticker NBIS --persona growth_hunter --runs 3
    python scripts/token_budget_test.py --ticker PLTR --persona forensics --runs 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pmacs.data.evidence_router import fetch_evidence_for_ticker
from pmacs.schemas.data import EvidencePacket


# ── Persona registry ────────────────────────────────────────────────────────
PERSONA_RUNNERS = {
    "growth_hunter": "pmacs.agents.growth_hunter:GrowthHunterRunner",
    "macro_regime": "pmacs.agents.macro_regime:MacroRegimeRunner",
    "forensics": "pmacs.agents.forensics:ForensicsRunner",
    "moat_analyst": "pmacs.agents.moat_analyst:MoatAnalystRunner",
    "catalyst_summarizer": "pmacs.agents.catalyst_summarizer:CatalystSummarizerRunner",
    "insider_activity": "pmacs.agents.insider_activity:InsiderActivityRunner",
    "short_interest": "pmacs.agents.short_interest:ShortInterestRunner",
    "crucible": "pmacs.agents.crucible:CrucibleRunner",
    "memo_writer": "pmacs.agents.memo_writer:MemoWriterRunner",
}

# These are the LOGICAL budget (what we set on the runner).
# For thinking models (Qwen3.6), the actual API call uses max_tokens * multiplier.
# The multiplier is read from model_registry.json extra_params.max_tokens_multiplier.
TOKEN_BUDGETS = [2048, 3072, 5120, 8192, 10240, 15360]


def _load_runner_class(persona: str):
    """Dynamically import a persona runner class."""
    module_path, class_name = PERSONA_RUNNERS[persona].rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def _measure_output_quality(raw_output: str, persona: str) -> dict:
    """Score output quality across multiple dimensions."""
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return {"parse_ok": False, "total_score": 0}

    scores = {"parse_ok": True}

    # 1. Field completeness — how many optional fields are populated?
    all_keys = list(data.keys())
    non_null = [k for k, v in data.items() if v is not None and v != "" and v != []]
    scores["field_count"] = len(all_keys)
    scores["populated_fields"] = len(non_null)
    scores["completeness_pct"] = round(len(non_null) / max(len(all_keys), 1) * 100, 1)

    # 2. Evidence citations — count evidence_id references
    raw = json.dumps(data)
    ev_count = raw.count("ev-") + raw.count("ev_") + raw.count("evidence")
    scores["evidence_citations"] = ev_count

    # 3. Reasoning depth — total characters in text fields
    text_fields = []
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 20:
            text_fields.append((k, len(v)))
    scores["reasoning_chars"] = sum(c for _, c in text_fields)
    scores["reasoning_fields"] = len(text_fields)

    # 4. Numeric specificity — count specific numbers in text
    import re
    numbers_in_text = re.findall(r'\d+\.?\d*%|\$[\d,.]+[BMK]?|\d+\.\d{1,2}x', raw)
    scores["numeric_references"] = len(numbers_in_text)

    # 5. Probabilities present?
    scores["has_probs"] = all(k in data for k in ("p_up", "p_flat", "p_down"))
    if scores["has_probs"]:
        scores["p_up"] = data["p_up"]
        scores["p_flat"] = data["p_flat"]
        scores["p_down"] = data["p_down"]

    # Total quality score (weighted)
    total = (
        scores["completeness_pct"] * 0.2 +
        min(scores["evidence_citations"], 20) * 3 +
        min(scores["reasoning_chars"], 2000) / 50 +
        min(scores["numeric_references"], 15) * 2 +
        (10 if scores["has_probs"] else 0)
    )
    scores["total_score"] = round(total, 1)

    return scores


def run_single_test(
    runner_cls,
    evidence: list[EvidencePacket],
    max_tokens: int,
    persona: str,
    run_id: int,
) -> dict:
    """Run a single persona call and return metrics."""
    cycle_id = f"token_test_{max_tokens}_{run_id}"

    import inspect
    sig = inspect.signature(runner_cls.__init__)
    if "cycle_id" in sig.parameters:
        runner = runner_cls(cycle_id=cycle_id)
    else:
        runner = runner_cls()
        runner.cycle_id = cycle_id
    runner.max_tokens = max_tokens  # Override the default

    t0 = time.monotonic()
    result = runner.run(evidence)
    elapsed = time.monotonic() - t0

    usage = runner._last_call_usage or {}

    metrics = {
        "max_tokens": max_tokens,
        "run_id": run_id,
        "success": result is not None,
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
    }

    if result is not None:
        quality = _measure_output_quality(result.raw_output, persona)
        metrics.update(quality)
    else:
        metrics["parse_ok"] = False
        metrics["total_score"] = 0

    return metrics


def analyze_results(results: list[dict], persona: str):
    """Analyze results across all budget levels and print report."""
    from collections import defaultdict

    by_budget = defaultdict(list)
    for r in results:
        by_budget[r["max_tokens"]].append(r)

    print("\n" + "=" * 90)
    print(f"  TOKEN BUDGET OPTIMIZATION REPORT — {persona.upper()}")
    print("=" * 90)

    print(f"\n{'Budget':>8} | {'Success':>7} | {'Comp.Tok':>8} | {'Quality':>7} | "
          f"{'Chars':>6} | {'Evid':>4} | {'Nums':>4} | {'p_up var':>8} | {'Time':>6} | {'Verdict'}")
    print("-" * 90)

    best_budget = None
    best_efficiency = 0

    for budget in TOKEN_BUDGETS:
        runs = by_budget.get(budget, [])
        if not runs:
            continue

        successes = [r for r in runs if r.get("success")]
        success_rate = len(successes) / len(runs) * 100

        if not successes:
            print(f"{budget:>8} | {success_rate:>6.0f}% | {'N/A':>8} | {'N/A':>7} | "
                  f"{'N/A':>6} | {'N/A':>4} | {'N/A':>4} | {'N/A':>8} | {'N/A':>6} | FAILED")
            continue

        avg_comp = sum(r.get("completion_tokens", 0) for r in successes) / len(successes)
        avg_quality = sum(r.get("total_score", 0) for r in successes) / len(successes)
        avg_chars = sum(r.get("reasoning_chars", 0) for r in successes) / len(successes)
        avg_evid = sum(r.get("evidence_citations", 0) for r in successes) / len(successes)
        avg_nums = sum(r.get("numeric_references", 0) for r in successes) / len(successes)
        avg_time = sum(r.get("elapsed_s", 0) for r in successes) / len(successes)

        # Probability variance across runs
        p_ups = [r.get("p_up", 0) for r in successes if r.get("has_probs")]
        if len(p_ups) >= 2:
            p_var = max(p_ups) - min(p_ups)
            p_var_str = f"±{p_var:.2f}"
        else:
            p_var = 0
            p_var_str = "N/A"

        # Efficiency = quality per 1K completion tokens
        efficiency = avg_quality / max(avg_comp / 1000, 0.1)

        # Determine verdict
        if avg_quality < 30:
            verdict = "TOO LOW"
        elif efficiency > best_efficiency and success_rate >= 66:
            best_efficiency = efficiency
            best_budget = budget
            verdict = "BEST*" if p_var <= 0.10 else "GOOD"
        elif success_rate < 66:
            verdict = "UNRELIABLE"
        else:
            verdict = "OK"

        print(f"{budget:>8} | {success_rate:>6.0f}% | {avg_comp:>8.0f} | {avg_quality:>7.1f} | "
              f"{avg_chars:>6.0f} | {avg_evid:>4.0f} | {avg_nums:>4.0f} | {p_var_str:>8} | "
              f"{avg_time:>5.1f}s | {verdict}")

    print("-" * 90)

    if best_budget:
        best_runs = [r for r in by_budget[best_budget] if r.get("success")]
        avg_comp = sum(r.get("completion_tokens", 0) for r in best_runs) / len(best_runs)
        waste_pct = (1 - avg_comp / best_budget) * 100

        print(f"\n  RECOMMENDATION: max_tokens = {best_budget}")
        print(f"  Average completion: {avg_comp:.0f} tokens ({waste_pct:.0f}% headroom)")
        print(f"  Quality score: {sum(r.get('total_score',0) for r in best_runs)/len(best_runs):.1f}")

        # Check if we can go lower
        lower_budgets = [b for b in TOKEN_BUDGETS if b < best_budget]
        if lower_budgets:
            lower = lower_budgets[-1]
            lower_runs = [r for r in by_budget.get(lower, []) if r.get("success")]
            if lower_runs:
                lower_quality = sum(r.get("total_score", 0) for r in lower_runs) / len(lower_runs)
                best_quality = sum(r.get("total_score", 0) for r in best_runs) / len(best_runs)
                quality_drop = (best_quality - lower_quality) / best_quality * 100
                print(f"  Dropping to {lower} loses {quality_drop:.1f}% quality")

    # Diminishing returns analysis
    print("\n  DIMINISHING RETURNS ANALYSIS:")
    prev_quality = None
    for budget in TOKEN_BUDGETS:
        runs = [r for r in by_budget.get(budget, []) if r.get("success")]
        if not runs:
            continue
        avg_q = sum(r.get("total_score", 0) for r in runs) / len(runs)
        if prev_quality is not None:
            delta = avg_q - prev_quality
            delta_pct = delta / max(prev_quality, 0.1) * 100
            indicator = ">>>" if delta_pct > 10 else ">>" if delta_pct > 5 else ">" if delta_pct > 0 else "="
            print(f"    {budget:>6}: quality {avg_q:>6.1f}  {indicator} +{delta_pct:>5.1f}% vs previous")
        else:
            print(f"    {budget:>6}: quality {avg_q:>6.1f}  (baseline)")
        prev_quality = avg_q

    print("\n" + "=" * 90)

    return best_budget


def main():
    parser = argparse.ArgumentParser(description="Token budget optimization test")
    parser.add_argument("--ticker", default="NBIS", help="Ticker to test")
    parser.add_argument("--persona", default="growth_hunter",
                        choices=list(PERSONA_RUNNERS.keys()),
                        help="Persona to test")
    parser.add_argument("--runs", type=int, default=3, help="Runs per budget level")
    parser.add_argument("--budgets", type=str, default=None,
                        help="Comma-separated budget list (default: 2048,3072,5120,8192,10240,15360)")
    args = parser.parse_args()

    global TOKEN_BUDGETS
    if args.budgets:
        TOKEN_BUDGETS = [int(x.strip()) for x in args.budgets.split(",")]

    runner_cls = _load_runner_class(args.persona)

    # Fetch evidence ONCE — reuse across all runs for fair comparison
    print(f"\nFetching evidence for {args.ticker}...")
    evidence_packet = fetch_evidence_for_ticker(args.ticker, "token_budget_test")
    evidence = [evidence_packet]
    print(f"  Evidence items: {len(evidence_packet.evidence)}")
    print(f"  Sources: {len(set(e.source for e in evidence_packet.evidence))}")

    all_results = []

    for budget in TOKEN_BUDGETS:
        print(f"\n--- Testing max_tokens={budget} ({args.runs} runs) ---")
        for run_id in range(1, args.runs + 1):
            print(f"  Run {run_id}/{args.runs}...", end=" ", flush=True)
            try:
                metrics = run_single_test(runner_cls, evidence, budget, args.persona, run_id)
                all_results.append(metrics)
                status = "OK" if metrics["success"] else "FAIL"
                comp = metrics.get("completion_tokens", 0)
                score = metrics.get("total_score", 0)
                print(f"{status} | {comp} comp tokens | quality={score:.1f} | {metrics['elapsed_s']}s")
            except Exception as exc:
                print(f"ERROR: {exc}")
                all_results.append({
                    "max_tokens": budget,
                    "run_id": run_id,
                    "success": False,
                    "total_score": 0,
                    "error": str(exc),
                })

    # Save raw results
    results_path = Path(__file__).parent / f"token_test_{args.ticker}_{args.persona}.json"
    results_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nRaw results saved to {results_path}")

    # Analyze
    best = analyze_results(all_results, args.persona)

    if best:
        print(f"\nTo apply: edit pmacs/agents/{args.persona}.py and set max_tokens={best}")


if __name__ == "__main__":
    main()
