"""Override learning — cluster operator overrides to detect recurring patterns."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OverrideCluster:
    override_type: str
    pattern: str
    count: int
    tickers: list[str]


def cluster_overrides(overrides: list[dict]) -> list[OverrideCluster]:
    """Cluster recent operator overrides to detect patterns.

    E.g. operator keeps overriding SKIP -> BUY on growth tickers.

    Parameters
    ----------
    overrides : list[dict]
        Each dict should have ``from_verdict``, ``to_verdict``, and optionally ``ticker``.

    Returns
    -------
    list[OverrideCluster]
        One cluster per unique override direction.
    """
    clusters: dict[str, OverrideCluster] = {}
    for o in overrides:
        key = f"{o.get('from_verdict', '')}\u2192{o.get('to_verdict', '')}"
        if key not in clusters:
            clusters[key] = OverrideCluster(
                override_type=key,
                pattern=f"Operator overrides {key}",
                count=0,
                tickers=[],
            )
        clusters[key].count += 1
        if o.get("ticker"):
            clusters[key].tickers.append(o["ticker"])

    return list(clusters.values())
