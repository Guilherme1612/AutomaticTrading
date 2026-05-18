# ADR-008: Boot-Driven Cycles, Not Scheduled

## Status

Accepted

## Context

PMACS runs daily analysis cycles that fetch EOD market data, run persona analysis, arbitrate conviction, and produce trade decisions. The cycle needs fresh EOD data (available ~16:30 ET after US market close) and takes 3-4 minutes of compute.

Previous versions used scheduled execution: an EOD auto-run at 16:30 ET via cron or launchd. This assumes the machine is always on and the operator is always present to review results. For a single-operator system running on a laptop, this assumption does not hold. The operator may not open the laptop until evening, may be traveling, or may take days off.

Running a scheduled cycle when the operator is absent creates risk: trade decisions are produced without operator review, and if the kill switch fires, there is no one to respond.

## Decision

Cycles are boot-driven, not scheduled. When `pmacs-cortex` starts, the boot detector (`cortex/boot_detector.py`) checks:

1. Was the last cycle completed more than 24 hours ago?
2. Is today a US trading day (NYSE calendar via pandas_market_calendars)?
3. Is it past the EOD data availability window (~16:30 ET)?

If all three conditions are met, cortex triggers a new cycle via `nervous.initiate_cycle(trigger="BOOT_DETECTED")`. No catch-up cycles for missed days; each boot produces at most one cycle for the most recent trading day.

If the gap exceeds 7 days (168 hours), a WARN-level debug event is logged (`RESUME_GAP`) but the cycle proceeds normally.

## Consequences

**Positive:**

- Matches the operator's natural rhythm. Cycles run when the operator opens the laptop, ensuring someone is present to review results and respond to alerts.
- No background daemon reliability requirements. The system does not need to stay running overnight or through weekends. Boot-up is the trigger.
- Simpler deployment. No cron jobs or scheduled launchd intervals. The operator just opens the laptop.
- Missed days are handled gracefully: the cycle processes the most recent trading day's data, with a WARN if the gap is unusually long.

**Negative:**

- If the operator does not open the laptop for several trading days, no cycles run. This is acceptable because the operator's thesis-driven holding philosophy does not require daily monitoring.
- EOD data freshness depends on boot timing. If the operator boots at 15:00 ET (before market close), the boot detector will skip until the next boot after 16:30 ET.
- Manual single-ticker re-runs are supported from the UI (Source.md §22) for cases where the operator wants to re-analyze after the boot cycle.

**References:** spec/Architecture.md §4.5 (boot-driven cycle initiation), spec/Source.md §22 (daily workflow), §9 (mode ladder cadence).
