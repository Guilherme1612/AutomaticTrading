#!/usr/bin/env python3
"""
Hidden token usage reader — for operator/Claude use only.
Not linked anywhere in the UI. Run manually or ask Claude to show you.

Usage:
    python ops/token_usage.py              # full summary
    python ops/token_usage.py --tail 20   # last 20 calls
    python ops/token_usage.py --ticker OUST  # filter by ticker (if tagged)
    python ops/token_usage.py --cycle     # group by cycle
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "data" / ".token_ledger.jsonl"

# Approximate cost per 1M tokens (update as models change)
COST_PER_1M = {
    "default": 0.14,  # deepseek-v4-flash blended
}


def cost(tokens: int, model: str) -> float:
    rate = next((v for k, v in COST_PER_1M.items() if k in model), COST_PER_1M["default"])
    return tokens / 1_000_000 * rate


def load(tail: int | None = None) -> list[dict]:
    if not LEDGER.exists():
        return []
    lines = LEDGER.read_text().strip().splitlines()
    if tail:
        lines = lines[-tail:]
    return [json.loads(l) for l in lines if l.strip()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tail", type=int, default=None)
    p.add_argument("--ticker", default=None)
    p.add_argument("--cycle", action="store_true")
    args = p.parse_args()

    rows = load(args.tail)
    if not rows:
        print("No token data yet. Run a cycle with OpenRouter to populate.")
        return

    if args.ticker:
        rows = [r for r in rows if args.ticker.upper() in r.get("ticker", "").upper()]

    total_prompt = sum(r["prompt_tokens"] for r in rows)
    total_comp   = sum(r["completion_tokens"] for r in rows)
    total_all    = sum(r["total_tokens"] for r in rows)
    total_cost   = sum(cost(r["total_tokens"], r["model"]) for r in rows)

    print(f"\n{'─'*60}")
    print(f"  TOKEN LEDGER  ({len(rows)} calls)")
    print(f"{'─'*60}")
    print(f"  Prompt tokens:      {total_prompt:>10,}")
    print(f"  Completion tokens:  {total_comp:>10,}")
    print(f"  Total tokens:       {total_all:>10,}")
    print(f"  Est. cost (USD):    ${total_cost:>9.4f}")
    print(f"{'─'*60}")

    # Per-model breakdown
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "total": 0})
    for r in rows:
        m = r["model"]
        by_model[m]["calls"] += 1
        by_model[m]["total"] += r["total_tokens"]

    print("\n  BY MODEL")
    for model, d in sorted(by_model.items(), key=lambda x: -x[1]["total"]):
        print(f"    {model:<45} {d['calls']:>4} calls  {d['total']:>8,} tokens  ${cost(d['total'], model):.4f}")

    # Last 10 calls
    print(f"\n  LAST {min(10, len(rows))} CALLS")
    for r in rows[-10:]:
        ts = r["ts"][:19].replace("T", " ")
        ticker = r.get("ticker", "—")
        print(f"    {ts}  {ticker:<6}  {r['caller']:<25}  {r['total_tokens']:>6} tok  ${cost(r['total_tokens'], r['model']):.5f}")

    print(f"\n  Ledger: {LEDGER}\n")


if __name__ == "__main__":
    main()
