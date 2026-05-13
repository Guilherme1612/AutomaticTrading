"""Wizard step: verify data source connectivity.

Pings each CRITICAL data source (polygon, edgar, alpaca) via DataGateway.
CRITICAL sources must pass for the wizard to proceed.
IMPORTANT and NICE_TO_HAVE sources are checked but non-blocking.

Spec ref: Source.md §12.1 Step 6, Architecture.md §6
"""
from __future__ import annotations

import httpx

# Source criticality per config/source_criticality.toml
CRITICAL_SOURCES = ["polygon", "edgar", "alpaca"]
IMPORTANT_SOURCES = ["finnhub", "fred"]
NICE_TO_HAVE_SOURCES = ["openfda", "finra", "form4", "ir_pages", "press", "fomc", "ecb"]

# Minimal test endpoints per source
TEST_ENDPOINTS: dict[str, dict] = {
    "polygon": {
        "url": "https://api.polygon.io/v2/aggs/ticker/AAPL/prev",
        "source": "polygon",
    },
    "edgar": {
        "url": "https://efts.sec.gov/LATEST/search-index?q=%22test%22",
        "source": "edgar",
    },
    "alpaca": {
        "url": "https://paper-api.alpaca.markets/v2/account",
        "source": "alpaca_data",
    },
    "finnhub": {
        "url": "https://finnhub.io/api/v1/quote",
        "source": "finnhub",
    },
    "fred": {
        "url": "https://api.stlouisfed.org/fred/series?series_id=DGS10",
        "source": "fred",
    },
}


async def run(config: dict) -> dict:
    """Verify data source connectivity.

    For each source, makes one lightweight read query. CRITICAL sources
    must pass. IMPORTANT/NICE_TO_HAVE sources produce warnings only.

    Args:
        config: Wizard config dict (may contain API keys).

    Returns:
        Dict with:
            results: {source: {ok: bool, message: str}}
            all_ok: bool - True only if all CRITICAL sources pass
    """
    results: dict[str, dict] = {}
    all_critical_ok = True

    sources_to_check = CRITICAL_SOURCES + IMPORTANT_SOURCES + NICE_TO_HAVE_SOURCES

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for source in sources_to_check:
            endpoint = TEST_ENDPOINTS.get(source)
            if not endpoint:
                # No test endpoint defined; skip with warning
                results[source] = {
                    "ok": True,
                    "message": "Skipped (no test endpoint)",
                }
                continue

            try:
                resp = await client.get(
                    endpoint["url"],
                    params={"apiKey": "test"} if source == "polygon" else None,
                    headers={"User-Agent": "PMACS/0.1 Setup Wizard"},
                )

                # Accept 2xx and 401/403 as "connectivity works, auth needed"
                if resp.status_code in (401, 403):
                    results[source] = {
                        "ok": True,
                        "message": f"Reachable (auth required, HTTP {resp.status_code})",
                    }
                elif 200 <= resp.status_code < 300:
                    results[source] = {
                        "ok": True,
                        "message": f"OK (HTTP {resp.status_code})",
                    }
                elif resp.status_code == 429:
                    # Rate limited = service reachable
                    results[source] = {
                        "ok": True,
                        "message": "Reachable (rate limited)",
                    }
                else:
                    results[source] = {
                        "ok": False,
                        "message": f"HTTP {resp.status_code}",
                    }
                    if source in CRITICAL_SOURCES:
                        all_critical_ok = False

            except httpx.ConnectError:
                results[source] = {
                    "ok": False,
                    "message": "Connection refused",
                }
                if source in CRITICAL_SOURCES:
                    all_critical_ok = False
            except httpx.TimeoutException:
                results[source] = {
                    "ok": False,
                    "message": "Connection timed out",
                }
                if source in CRITICAL_SOURCES:
                    all_critical_ok = False
            except Exception as exc:
                results[source] = {
                    "ok": False,
                    "message": str(exc)[:100],
                }
                if source in CRITICAL_SOURCES:
                    all_critical_ok = False

    return {
        "results": results,
        "all_ok": all_critical_ok,
    }
