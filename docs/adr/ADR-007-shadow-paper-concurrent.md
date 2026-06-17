# ADR-007: SHADOW and PAPER Concurrent from Day 1

## Status

Accepted

## Context

PMACS operates across six modes: SHADOW, PAPER, PAPER_VALIDATED, LIVE_EARLY, LIVE_STANDARD, and LIVE_EXPANDED (Source.md §9.1). Previous versions had separate sequential phases: a TRAINING/SHADOW phase that ran without execution, followed by a PAPER phase with simulated execution.

The sequential approach delays paper trading until SHADOW is deemed "ready." But the operator wants to see paper trading results from day 1. SHADOW and PAPER serve different purposes: SHADOW validates the evidence pipeline, agent outputs, and math gate against real market data without execution noise; PAPER provides simulated execution and resolution data for source maturation.

There is no technical conflict between running both simultaneously. SHADOW captures signals without recording trades; PAPER records trades against simulated capital.

## Decision

Run SHADOW and PAPER concurrently from day 1. SHADOW provides math-gate audit with zero capital risk. PAPER provides $5,000 simulated execution against the Alpaca paper API.

SHADOW continues running even after PAPER_VALIDATED promotion, providing a consistent audit baseline throughout the system's lifetime.

The mode ladder progresses as:
1. Day 1: SHADOW + PAPER concurrent
2. ~50 PAPER cycles: Mutation Engine activates (SHADOW A/B testing)
3. ~90 PAPER cycles + gates pass: Operator promotes to PAPER_VALIDATED
4. ~180 PAPER_VALIDATED + gates pass: Operator promotes to LIVE_EARLY

## Consequences

**Positive:**

- Operator sees paper trading results immediately, building confidence and engagement from day 1.
- SHADOW and PAPER produce complementary data. SHADOW validates that the pipeline is mathematically sound; PAPER validates that execution assumptions (slippage, fill timing) are realistic.
- SHADOW's continued operation after PAPER promotion provides a permanent no-execution baseline for comparison, useful for attributing performance differences to execution vs. analysis.
- No arbitrary gate between SHADOW and PAPER. The sequential approach required a subjective "SHADOW is ready" decision.

**Negative:**

- Doubles the computation per cycle (two execution paths). With 3-slot inference parallelism, this is manageable but adds cycle time.
- Two sets of metrics to track and display in the dashboard. The UI must clearly distinguish SHADOW (audit-only) from PAPER (simulated execution) data.
- If SHADOW and PAPER diverge significantly (e.g., SHADOW would have entered but PAPER's bootstrap haircut blocked it), the operator must understand why. This adds cognitive load.

**References:** spec/Source.md §9 (mode ladder), spec/Architecture.md §4.5 (boot-driven cycles), spec/Phases.md §3 (promotion gates).
