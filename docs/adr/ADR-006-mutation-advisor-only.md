# ADR-006: Mutation Engine is Advisor-Only, Never Auto-Applies

## Status

Accepted

## Context

PMACS includes a Mutation Engine (`pmacs-mutation`) that is the active component of the system's learning flywheel. It monitors system performance, detects degradation patterns, hypothesizes improvements, and runs A/B tests. It operates across five mutation dimensions: prompts, source weights, thresholds, persona affinity, and universe flags (Architecture.md §10.2).

The question is whether the Mutation Engine should auto-apply changes that meet statistical significance thresholds (p < 0.05, Cohen's d > 0.20, n >= 20). Auto-apply would make the system self-improving without operator intervention. But PMACS trades real capital (eventually), and an auto-applied mutation could degrade performance before detection.

The Mutation Engine activates after 50 PAPER cycles (enough data to establish a meaningful baseline). Candidate mutations run in SHADOW mode only, never affecting PAPER directly. The operator must explicitly promote or reject each candidate.

## Decision

The Mutation Engine is an advisor, not an actor. It detects, hypothesizes, A/B tests, and recommends. It never auto-applies mutations to production configuration. ALL mutations require operator confirmation to apply. No exceptions.

The mutation process cannot write to production config files. It proposes candidates, runs them in SHADOW A/B tests, accumulates statistics, and surfaces recommendations to the operator through the dashboard. The operator reviews the recommendation, confirms, and the mutation is applied.

Auto-rollback on regression remains as a safety net: if an operator-approved mutation regresses within 50 cycles (probation window), the system auto-rolls back to the previous configuration.

## Consequences

**Positive:**

- The operator maintains full control over system evolution. No change to prompts, weights, thresholds, or persona affinity can happen without explicit approval.
- Prevents the flywheel from degrading the base system through compounding small changes that individually pass statistical thresholds but collectively drift behavior.
- Auto-rollback on regression provides a safety net for approved mutations that underperform. The system can recover from a bad decision without operator intervention.
- The five mutation dimensions are structurally isolated: the mutation process cannot write production config (structural, not procedural constraint).

**Negative:**

- System improvement pace is bottlenecked by operator availability. If the operator is away for a week, recommended mutations queue up.
- The operator-confirmation step adds friction. For high-frequency mutation recommendations (e.g., source weight adjustments), the operator may experience approval fatigue.
- The 50-cycle activation delay means no mutation feedback for the first ~50 days of operation (at 1 cycle/day).

**References:** spec/Architecture.md §10 (Mutation Engine process), §10.2 (five mutation dimensions), §16 (anti-patterns: no auto-apply, no production writes), spec/Agents.md §17 (candidate generation rules), spec/Source.md §10 (flywheel).
