# ADR-009: Operator-Curated Universe, No Screener-Driven Expansion

## Status

Accepted

## Context

PMACS analyzes a universe of equities and produces catalyst-driven trade decisions. The universe must be defined somehow. Options include:

1. **Screener-driven:** Automatically pull in names matching growth-tech criteria (market cap, revenue growth, sector, etc.) from data sources like Polygon or FINRA.
2. **Index-based:** Use a fixed index like Nasdaq-100 as the universe.
3. **Operator-curated:** The operator manually selects and maintains the list of tickers to track.

The operator's edge in this system is thesis quality on names they understand deeply. The holding philosophy is thesis-bound (hold while thesis is valid), not statistical (hold based on backtested patterns). A screener would add names the operator has no thesis on, diluting the system's core value proposition.

## Decision

The universe is operator-curated. The operator selects ~16 growth-tech tickers across Nasdaq and NYSE that they follow closely and can form theses about.

An optional Nasdaq-100 overlay is available via Settings (Index Overlay = on), but it is opt-in and supplementary, not the primary universe.

No automatic universe expansion via screener in v1. The operator may add or remove tickers through the Settings page (TOTP-gated write through nervous).

`pmacs-mutation` can flag tickers with chronic uncertainty (universe_flags dimension) for operator review, but cannot add or remove tickers directly.

## Consequences

**Positive:**

- The operator's thesis quality on curated names is the system's edge. A screener would dilute that into generic "growth at this market cap" averaging.
- Smaller universe means deeper analysis per ticker. With ~16 tickers and 9 personas, each ticker gets thorough coverage within cycle time budgets.
- The operator understands why each ticker is in the universe and can contextualize persona outputs accordingly.
- The Nasdaq-100 overlay provides breadth when the operator wants it, without changing the curated core.

**Negative:**

- The universe is limited to what the operator knows. Emerging opportunities outside the operator's awareness are missed.
- Adding a new ticker requires manual action (Settings page + TOTP). If the operator is slow to add a name that a screener would have caught, the system misses early entry opportunities.
- The ~16-ticker seed is small. For meaningful diversification across catalyst types, the operator must actively maintain and refresh the list.

**References:** spec/Source.md §8 (universe management), §8.4 (no screener), spec/Architecture.md §10.2 (universe_flags mutation dimension).
