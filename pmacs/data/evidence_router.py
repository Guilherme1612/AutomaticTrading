"""Evidence fetching pipeline -- wires 13 data source modules into the cycle.

Spec ref: Architecture.md S6.2, Phases S2 exit test 3.

For each ticker in the cycle, this module:
  1. Creates a DataGateway (rate-limited HTTP client).
  2. Reads API keys from macOS Keychain (graceful fallback if missing).
  3. Calls all relevant data source modules for the ticker.
  4. Merges EvidencePackets, deduplicates by evidence id, checks staleness.
  5. Returns a single EvidencePacket with all evidence.

Error handling:
  - Individual source failures are logged and skipped (partial evidence is OK).
  - CRITICAL source failures set has_stale_data=True on the packet.
  - API key lookup failures are logged and the source is skipped.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from typing import Any

from pmacs.data.gateway import DataGateway
from pmacs.data.staleness import check_all_freshness
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket
from pmacs.schemas.freshness import CriticalityLevel
from pmacs.storage.sqlite import connect as _sql_connect

# ---------------------------------------------------------------------------
# Keychain service name mapping (source -> dotted keychain name)
# Sources not listed here don't require an API key.
# ---------------------------------------------------------------------------
_KEYCHAIN_MAP: dict[DataSource, str] = {
    DataSource.POLYGON: "pmacs.data.polygon.api_key",
    DataSource.FINNHUB: "pmacs.data.finnhub.api_key",
    DataSource.ALPACA_DATA: "pmacs.api.alpaca_key.gui",
    DataSource.FUNDAMENTALS: "pmacs.data.finnhub.api_key",  # reuses Finnhub key
    DataSource.TECHNICAL: "pmacs.data.polygon.api_key",      # reuses Polygon key
    # YAHOO needs no API key
}

# Source criticality (mirrors config/source_criticality.toml)
_SOURCE_CRITICALITY: dict[DataSource, CriticalityLevel] = {
    DataSource.EDGAR: CriticalityLevel.CRITICAL,
    DataSource.POLYGON: CriticalityLevel.IMPORTANT,
    DataSource.FINNHUB: CriticalityLevel.IMPORTANT,
    DataSource.ALPACA_DATA: CriticalityLevel.IMPORTANT,
    DataSource.OPENFDA: CriticalityLevel.IMPORTANT,
    DataSource.FINRA: CriticalityLevel.IMPORTANT,
    DataSource.FORM4: CriticalityLevel.IMPORTANT,
    DataSource.IR_PAGES: CriticalityLevel.IMPORTANT,
    DataSource.PRESS: CriticalityLevel.IMPORTANT,
    DataSource.FOMC: CriticalityLevel.NICE_TO_HAVE,
    DataSource.FRED: CriticalityLevel.NICE_TO_HAVE,
    DataSource.ECB: CriticalityLevel.NICE_TO_HAVE,
    DataSource.FUNDAMENTALS: CriticalityLevel.IMPORTANT,
    DataSource.TECHNICAL: CriticalityLevel.IMPORTANT,
    DataSource.YAHOO: CriticalityLevel.IMPORTANT,
    DataSource.EDGAR_KPI: CriticalityLevel.NICE_TO_HAVE,  # KPI miss never marks stale
}

# Staleness budgets in seconds (mirrors config/source_criticality.toml)
_STALENESS_BUDGETS: dict[str, tuple[CriticalityLevel, int]] = {
    "edgar": (CriticalityLevel.CRITICAL, 86400),
    "polygon": (CriticalityLevel.IMPORTANT, 300),
    "finnhub": (CriticalityLevel.IMPORTANT, 300),
    "alpaca_data": (CriticalityLevel.IMPORTANT, 300),
    "openfda": (CriticalityLevel.IMPORTANT, 86400),
    "finra": (CriticalityLevel.IMPORTANT, 86400),
    "form4": (CriticalityLevel.IMPORTANT, 86400),
    "ir_pages": (CriticalityLevel.IMPORTANT, 604800),
    "press": (CriticalityLevel.IMPORTANT, 86400),
    "fomc": (CriticalityLevel.NICE_TO_HAVE, 604800),
    "fred": (CriticalityLevel.NICE_TO_HAVE, 86400),
    "ecb": (CriticalityLevel.NICE_TO_HAVE, 86400),
    "fundamentals": (CriticalityLevel.IMPORTANT, 86400),
    "technical": (CriticalityLevel.IMPORTANT, 300),
    "yahoo": (CriticalityLevel.IMPORTANT, 3600),
    "edgar_kpi": (CriticalityLevel.NICE_TO_HAVE, 86400 * 7),  # KPIs change slowly; weekly ok
}

# ---------------------------------------------------------------------------
# Per-persona evidence mapping (Agents.md SS5-SS11)
# ---------------------------------------------------------------------------
PERSONA_EVIDENCE_MAP: dict[str, list[DataSource]] = {
    "MacroRegime": [
        DataSource.FRED,
        DataSource.FOMC,
        DataSource.POLYGON,  # sector ETFs, VIX via market data
        DataSource.ECB,
    ],
    "CatalystSummarizer": [
        DataSource.FINNHUB,  # earnings calendar + consensus + analyst trend
        DataSource.EDGAR,    # SEC 8-K
        DataSource.OPENFDA,
        DataSource.IR_PAGES,
        DataSource.PRESS,
        DataSource.YAHOO,    # price targets for catalyst impact
    ],
    "MoatAnalyst": [
        DataSource.EDGAR,       # 10-K/10-Q
        DataSource.FUNDAMENTALS,
        DataSource.IR_PAGES,
        DataSource.PRESS,
        DataSource.TECHNICAL,   # trend confirmation for moat durability
        DataSource.YAHOO,       # forward valuation for moat quality
    ],
    "GrowthHunter": [
        DataSource.FUNDAMENTALS,
        DataSource.EDGAR,
        DataSource.IR_PAGES,
        DataSource.FINNHUB,     # earnings history + calendar + quote
        DataSource.PRESS,       # guidance updates, M&A, partnerships
        DataSource.TECHNICAL,   # moving averages for growth trend confirmation
        DataSource.YAHOO,       # price targets + forward EPS growth
    ],
    "InsiderActivity": [
        DataSource.FORM4,
        DataSource.FUNDAMENTALS,
        DataSource.YAHOO,       # primary fundamentals profile (officer names, market cap)
        DataSource.TECHNICAL,   # price vs MAs for insider timing context
    ],
    "ShortInterest": [
        DataSource.FINRA,
        DataSource.FUNDAMENTALS,
        DataSource.YAHOO,        # primary fundamentals for float sanity + volume history
        DataSource.ALPACA_DATA,  # quotes for days-to-cover
        DataSource.TECHNICAL,    # trend for short thesis validation
    ],
    "Forensics": [
        DataSource.EDGAR,        # 10-K/10-Q
        DataSource.FUNDAMENTALS,
        DataSource.PRESS,
        DataSource.TECHNICAL,    # price action anomalies
        DataSource.YAHOO,        # forward valuation sanity check
    ],
}


def filter_evidence_for_persona(
    evidence: list[EvidencePacket],
    persona_name: str,
) -> list[EvidencePacket]:
    """Filter evidence packets to only sources relevant to a persona.

    If the persona is not in PERSONA_EVIDENCE_MAP, returns all evidence unchanged.

    Args:
        evidence: Full evidence packets for the ticker.
        persona_name: Persona class name (e.g. "MacroRegime").

    Returns:
        Filtered evidence packets containing only relevant sources.
    """
    allowed_sources = PERSONA_EVIDENCE_MAP.get(persona_name)
    if allowed_sources is None:
        return evidence

    allowed_set = set(allowed_sources)
    filtered_packets: list[EvidencePacket] = []

    for packet in evidence:
        filtered_items = [ev for ev in packet.evidence if ev.source in allowed_set]
        if filtered_items:
            filtered_packets.append(
                EvidencePacket(
                    ticker=packet.ticker,
                    cycle_id=packet.cycle_id,
                    evidence=filtered_items,
                    fetched_at=packet.fetched_at,
                    source_count=len({ev.source for ev in filtered_items}),
                    has_stale_data=packet.has_stale_data,
                )
            )

    return filtered_packets


def _read_key(source: DataSource, cycle_id: str) -> str:
    """Read an API key from Keychain. Returns empty string on failure."""
    from pmacs.storage.keychain import read_key

    dotted = _KEYCHAIN_MAP.get(source)
    if dotted is None:
        return ""
    key = read_key(dotted)
    if key is None:
        log_debug(
            "EVIDENCE_KEY_MISSING",
            payload={"source": source.value},
            level="WARN",
            error_code="DATA_UNAVAILABLE",
            cycle_id=cycle_id,
            msg=f"API key not found for source {source.value}, skipping",
        )
        return ""
    return key


def _get_cik_for_ticker(ticker: str) -> str:
    """Return a CIK for the given ticker.

    In production this would look up a ticker-to-CIK mapping table.
    For now returns a placeholder that the EDGAR source can handle.
    """
    # Common ticker -> CIK mapping (covers default PMACS universe + common additions).
    # CIKs verified against the SEC authoritative ticker map
    # (https://www.sec.gov/files/company_tickers_exchange.json) on 2026-06-18 — a prior
    # version of this map held wrong CIKs for ~14 tickers, which caused EDGAR to fetch the
    # wrong company's filings. Values below are the authoritative ones.
    _TICKER_CIK: dict[str, str] = {
        # Big Tech
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "GOOGL": "0001652044",
        "GOOG": "0001652044",
        "AMZN": "0001018724",
        "META": "0001326801",
        "NVDA": "0001045810",
        "TSLA": "0001318605",
        # PMACS default universe
        "PLTR": "0001321655",
        "NET": "0001477333",
        "MELI": "0001099590",
        "CELH": "0001341766",
        "INMD": "0001742692",
        "CRWD": "0001535527",
        "OUST": "0001816581",
        "NU": "0001691493",
        "HIMS": "0001773751",
        "NBIS": "0001513845",
        "ONDS": "0001646188",
        "TEM": "0001717115",
        "ZETA": "0001851003",
        "PANW": "0001327567",
        "KOD": "0001468748",
        "DLO": "0001846832",
        # Common additions
        "JNJ": "0000200406",
        "UNH": "0000731766",
        "JPM": "0000019617",
        "V": "0001403161",
        "XOM": "0000034088",
        "LLY": "0000059478",
        "PG": "0000080424",
        "MRK": "0000310158",
        "NFLX": "0001065280",
        "ADBE": "0000796343",
        "CRM": "0001108524",
        "NOW": "0001373715",
        "SNOW": "0001640147",
        "DDOG": "0001561550",
        "ZS": "0001713683",
        "OKTA": "0001660134",
        "TWLO": "0001447669",
    }
    return _TICKER_CIK.get(ticker, "0000000000")


def _get_ir_url_for_ticker(ticker: str) -> str:
    """Return a best-guess IR page URL for the ticker."""
    return f"https://investor.{ticker.lower()}.com"


# ---------------------------------------------------------------------------
# Source fetcher registry -- each entry is (source, fetch_function, kwargs_fn)
# kwargs_fn builds the keyword arguments for the fetch function from
# (ticker, gateway, api_key, cycle_id).
# ---------------------------------------------------------------------------

def _fetch_polygon(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.polygon import fetch_daily_bars
    return fetch_daily_bars(ticker, gw, key, cycle_id=cid)


def _fetch_finnhub(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.finnhub import fetch_quote, fetch_earnings_data, fetch_analyst_data, fetch_consensus_estimates
    from datetime import datetime, timezone
    quote_packet = fetch_quote(ticker, gw, key, cycle_id=cid)
    earnings_packet = fetch_earnings_data(ticker, gw, key, cycle_id=cid)
    analyst_packet = fetch_analyst_data(ticker, gw, key, cycle_id=cid)
    consensus_packet = fetch_consensus_estimates(ticker, gw, key, cycle_id=cid)
    # Merge all packets into one FINNHUB packet
    merged_evidence = (
        list(quote_packet.evidence)
        + list(earnings_packet.evidence)
        + list(analyst_packet.evidence)
        + list(consensus_packet.evidence)
    )
    return EvidencePacket(
        ticker=ticker,
        cycle_id=cid,
        evidence=merged_evidence,
        fetched_at=datetime.now(timezone.utc),
        source_count=1,
    )


def _fetch_alpaca(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.alpaca_data import fetch_bars
    return fetch_bars(ticker, gw, key, cycle_id=cid)


def _fetch_edgar(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.edgar import fetch as fetch_edgar
    cik = _get_cik_for_ticker(ticker)
    return fetch_edgar(cik, ticker, gw, cycle_id=cid)


def _fetch_edgar_kpi(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    """Best-effort SaaS-KPI extraction from EDGAR filing narrative (no API key).

    NICE_TO_HAVE: a KPI miss or fetch failure must never mark data stale, so this
    returns an empty packet on any error rather than raising.
    """
    from pmacs.data.sources.edgar_kpi import fetch as fetch_kpi
    cik = _get_cik_for_ticker(ticker)
    try:
        return fetch_kpi(cik, ticker, gw, cycle_id=cid)
    except Exception as exc:  # pragma: no cover - network dependent
        log_debug("DATA_UNAVAILABLE", payload={"source": "edgar_kpi", "ticker": ticker,
                  "error": str(exc)[:200]}, level="INFO", error_code="DATA_UNAVAILABLE",
                  cycle_id=cid, msg=f"edgar_kpi fetch failed for {ticker}: {exc}")
        return EvidencePacket(ticker=ticker, cycle_id=cid, evidence=[],
                              fetched_at=datetime.now(timezone.utc), source_count=0)


def _fetch_form4(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.form4 import fetch_insider_filings
    cik = _get_cik_for_ticker(ticker)
    return fetch_insider_filings(cik, ticker, gw, cycle_id=cid)


def _fetch_fundamentals(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    """Primary fundamentals via yfinance; Finnhub only fills fields yfinance lacks.

    Operator directive (2026-06-17): yfinance is the primary fundamentals source.
    Finnhub is used solely as a gap-filler for fields yfinance did not return — and
    never to override a value yfinance already supplied — because Finnhub's free
    tier is incomplete and its percentage quirks have corrupted data before.
    """
    from pmacs.data.sources.yfinance_fundamentals import fetch_fundamentals_yf

    primary = fetch_fundamentals_yf(ticker, gw, api_key="", cycle_id=cid)
    if not primary.evidence:
        # Total yfinance failure — fall back entirely to Finnhub.
        from pmacs.data.sources.fundamentals import fetch_fundamentals
        return fetch_fundamentals(ticker, gw, api_key=key, cycle_id=cid)

    # Gap-fill: pull Finnhub and merge only keys yfinance is missing per evidence id.
    try:
        from pmacs.data.sources.fundamentals import fetch_fundamentals
        finnhub = fetch_fundamentals(ticker, gw, api_key=key, cycle_id=cid)
    except Exception:
        return primary

    fin_by_id = {e.id: (e.data or {}) for e in finnhub.evidence}
    merged: list[Evidence] = []
    for ev in primary.evidence:
        extra = fin_by_id.get(ev.id, {})
        if not extra:
            merged.append(ev)
            continue
        filled = dict(ev.data)
        for k, v in extra.items():
            if k not in filled and v is not None and v != []:
                filled[k] = v
        merged.append(ev.model_copy(update={"data": filled}))

    return primary.model_copy(update={"evidence": merged})


def _fetch_finra(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.finra import fetch_short_interest
    return fetch_short_interest(ticker, gw, cycle_id=cid)


def _fetch_press(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.press import fetch_press_releases
    return fetch_press_releases(ticker, gw, cycle_id=cid)


def _fetch_ir_pages(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.ir_pages import fetch_ir_page
    url = _get_ir_url_for_ticker(ticker)
    return fetch_ir_page(ticker, url, gw, cycle_id=cid)


def _fetch_fred(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.fred import fetch_series
    # Fetch yield curve (T10Y2Y) and VIX-proxy (could be expanded)
    return fetch_series("T10Y2Y", gw, api_key=key, cycle_id=cid)


def _fetch_fomc(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.fomc import fetch_latest_statement
    return fetch_latest_statement(gw, cycle_id=cid)


def _fetch_ecb(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.ecb import fetch_fx_rate
    return fetch_fx_rate(gw, cycle_id=cid)


def _fetch_openfda(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.openfda import fetch_drug_events
    # Use ticker as drug name -- works for pharma tickers, returns empty for others
    return fetch_drug_events(ticker, gw, cycle_id=cid)


def _fetch_technical(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.technical import fetch_technical
    return fetch_technical(ticker, gw, key, cycle_id=cid)


def _fetch_yahoo(ticker: str, gw: DataGateway, key: str, cid: str) -> EvidencePacket:
    from pmacs.data.sources.yahoo import fetch_price_targets
    return fetch_price_targets(ticker, gw, cycle_id=cid)


# Ordered list of all source fetchers
_SOURCE_FETCHERS: list[tuple[DataSource, Any]] = [
    (DataSource.POLYGON, _fetch_polygon),
    (DataSource.FINNHUB, _fetch_finnhub),
    (DataSource.ALPACA_DATA, _fetch_alpaca),
    (DataSource.EDGAR, _fetch_edgar),
    (DataSource.FORM4, _fetch_form4),
    (DataSource.FUNDAMENTALS, _fetch_fundamentals),
    (DataSource.FINRA, _fetch_finra),
    (DataSource.PRESS, _fetch_press),
    (DataSource.IR_PAGES, _fetch_ir_pages),
    (DataSource.FRED, _fetch_fred),
    (DataSource.FOMC, _fetch_fomc),
    (DataSource.ECB, _fetch_ecb),
    (DataSource.OPENFDA, _fetch_openfda),
    (DataSource.TECHNICAL, _fetch_technical),
    (DataSource.YAHOO, _fetch_yahoo),
    (DataSource.EDGAR_KPI, _fetch_edgar_kpi),
]


def fetch_evidence_for_ticker(
    ticker: str,
    cycle_id: str,
) -> EvidencePacket:
    """Fetch evidence from all data sources for a single ticker.

    Architecture.md S6.2 -- evidence fetching pipeline.
    Each source is called independently; failures are logged and skipped.
    CRITICAL source failures set has_stale_data=True on the final packet.

    Evidence accumulation: loads previous cycle's evidence and merges with
    fresh fetch. Fresh data always wins; stale fills from cache.

    Args:
        ticker: Stock ticker symbol.
        cycle_id: Current cycle identifier.

    Returns:
        Merged EvidencePacket with all evidence collected.
    """
    import time as _time
    now = datetime.now(timezone.utc)
    all_evidence: list[Evidence] = []
    sources_ok: int = 0
    has_stale = False
    important_failures: int = 0

    # ── Load previous cycle evidence for accumulation ──────────────────────
    previous_evidence = _load_evidence_cache(ticker)

    _PER_SOURCE_TIMEOUT = 12  # seconds — prevents any single source from blocking
    _fetch_start = _time.monotonic()

    with DataGateway(timeout=10) as gw:
        for source, fetcher in _SOURCE_FETCHERS:
            api_key = _read_key(source, cycle_id)
            _src_start = _time.monotonic()
            try:
                packet = fetcher(ticker, gw, api_key, cycle_id)
                _elapsed = _time.monotonic() - _src_start
                log_debug(
                    "EVIDENCE_SOURCE_OK",
                    payload={"source": source.value, "ticker": ticker, "ms": int(_elapsed * 1000)},
                    cycle_id=cycle_id,
                    msg=f"[{ticker}] {source.value}: {int(_elapsed*1000)}ms",
                )
                if packet and packet.evidence:
                    all_evidence.extend(packet.evidence)
                    sources_ok += 1
            except Exception as exc:
                _elapsed = _time.monotonic() - _src_start
                crit = _SOURCE_CRITICALITY.get(source, CriticalityLevel.NICE_TO_HAVE)
                log_debug(
                    "EVIDENCE_SOURCE_FAILED",
                    payload={
                        "source": source.value,
                        "ticker": ticker,
                        "error": str(exc)[:200],
                        "criticality": crit.value,
                        "ms": int(_elapsed * 1000),
                    },
                    level="WARN" if crit == CriticalityLevel.CRITICAL else "INFO",
                    error_code="DATA_UNAVAILABLE" if crit == CriticalityLevel.CRITICAL else None,
                    cycle_id=cycle_id,
                    msg=f"Evidence fetch failed for {source.value}/{ticker} ({int(_elapsed*1000)}ms): {exc}",
                )
                if crit == CriticalityLevel.CRITICAL:
                    has_stale = True
                elif crit == CriticalityLevel.IMPORTANT:
                    important_failures += 1

    _total_elapsed = _time.monotonic() - _fetch_start
    log_debug(
        "EVIDENCE_FETCH_COMPLETE",
        payload={"ticker": ticker, "sources_ok": sources_ok, "total_ms": int(_total_elapsed * 1000)},
        cycle_id=cycle_id,
        msg=f"[{ticker}] Evidence fetch complete: {sources_ok}/{len(_SOURCE_FETCHERS)} sources in {int(_total_elapsed*1000)}ms",
    )

    # If 2+ IMPORTANT sources failed, data quality is degraded even if
    # no CRITICAL source was lost.  Mark stale so agents know.
    if important_failures >= 2 and not has_stale:
        has_stale = True
        log_debug(
            "IMPORTANT_SOURCES_DEGRADED",
            payload={
                "ticker": ticker,
                "important_failures": important_failures,
            },
            level="WARN",
            error_code="DATA_UNAVAILABLE",
            cycle_id=cycle_id,
            msg=f"{important_failures} IMPORTANT sources failed for {ticker} — marking data stale",
        )

    # Deduplicate by evidence id
    seen_ids: set[str] = set()
    unique_evidence: list[Evidence] = []
    for ev in all_evidence:
        if ev.id not in seen_ids:
            seen_ids.add(ev.id)
            unique_evidence.append(ev)

    # ── Merge with previous cycle evidence (accumulation) ──────────────────
    fresh_ids = {ev.id for ev in unique_evidence}
    carried_forward = 0
    for prev_ev in previous_evidence:
        if prev_ev.id not in fresh_ids:
            unique_evidence.append(prev_ev)
            fresh_ids.add(prev_ev.id)
            carried_forward += 1

    if carried_forward > 0:
        log_debug(
            "EVIDENCE_ACCUMULATED",
            payload={"ticker": ticker, "carried_forward": carried_forward, "fresh": len(unique_evidence) - carried_forward},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Evidence accumulation for {ticker}: carried {carried_forward} items from previous cycle",
        )

    # ── Cross-source validation ────────────────────────────────────────────
    # Compare EDGAR XBRL growth figures with Finnhub metrics to flag
    # discrepancies. This catches data corruption that validation alone misses.
    cross_source_notes: list[str] = []
    edgar_fin = next((ev for ev in unique_evidence if ev.id.endswith("_financials") and ev.source == DataSource.EDGAR), None)
    finnhub_metrics = next((ev for ev in unique_evidence if ev.id.endswith("_metrics") and ev.source == DataSource.FUNDAMENTALS), None)
    yahoo_fin = next((ev for ev in unique_evidence if ev.id.endswith("_financials") and ev.source == DataSource.YAHOO), None)

    if edgar_fin and finnhub_metrics:
        edgar_data = edgar_fin.data or {}
        finnhub_data = finnhub_metrics.data or {}

        # Compare revenue growth
        edgar_rev_growth = edgar_data.get("revenue_yoy_growth")
        finnhub_rev_growth_raw = finnhub_data.get("revenueGrowthTTMYoy")
        if edgar_rev_growth and finnhub_rev_growth_raw is not None:
            try:
                edgar_g = float(edgar_rev_growth.replace("%", "").replace("+", ""))
                finnhub_g = float(finnhub_rev_growth_raw)  # Already a percentage
                diff = abs(edgar_g - finnhub_g)
                if diff > 10:  # More than 10pp divergence
                    cross_source_notes.append(
                        f"Revenue growth divergence: EDGAR={edgar_rev_growth}, "
                        f"Finnhub={finnhub_g:+.1f}% — prefer EDGAR (SEC-reported)"
                    )
            except (ValueError, TypeError):
                pass

        # Compare gross margin
        edgar_gm = edgar_data.get("gross_margin_pct")
        finnhub_gm_raw = finnhub_data.get("grossMarginTTM")
        if edgar_gm and finnhub_gm_raw is not None:
            try:
                edgar_m = float(edgar_gm.replace("%", ""))
                finnhub_m = float(finnhub_gm_raw)  # Already a percentage
                diff = abs(edgar_m - finnhub_m)
                if diff > 5:  # More than 5pp divergence
                    cross_source_notes.append(
                        f"Gross margin divergence: EDGAR={edgar_gm}, "
                        f"Finnhub={finnhub_m:.1f}% — prefer EDGAR (SEC-reported)"
                    )
            except (ValueError, TypeError):
                pass

        # If all Finnhub metrics are flagged anomalous, note to prefer EDGAR entirely
        anomalous = finnhub_data.get("_anomalous_fields", [])
        if len(anomalous) >= 3:
            cross_source_notes.append(
                f"{len(anomalous)} Finnhub metrics flagged anomalous — use EDGAR XBRL data as primary source"
            )

    # ── Finnhub vs Yahoo Finance cross-validation ──────────────────────────
    # Yahoo Finance often has more current TTM data. Compare revenue and
    # margins to detect stale Finnhub data (>10% divergence = likely stale).
    if finnhub_metrics and yahoo_fin:
        fh_data = finnhub_metrics.data or {}
        yh_data = yahoo_fin.data or {}

        # Compare revenue: Finnhub revenueTTM vs Yahoo totalRevenue
        fh_rev = fh_data.get("revenueTTM")
        yh_rev = yh_data.get("ttm_revenue")
        if fh_rev and yh_rev and yh_rev > 0:
            try:
                rev_diff_pct = abs(float(fh_rev) - float(yh_rev)) / float(yh_rev) * 100
                if rev_diff_pct > 10:
                    cross_source_notes.append(
                        f"Revenue divergence: Finnhub TTM ${float(fh_rev)/1e9:.1f}B vs "
                        f"Yahoo ${float(yh_rev)/1e9:.1f}B ({rev_diff_pct:.0f}% off) — "
                        f"Finnhub data likely stale, prefer Yahoo/EDGAR"
                    )
            except (ValueError, TypeError):
                pass

        # Compare gross margin
        fh_gm = fh_data.get("grossMarginTTM")
        yh_gm = yh_data.get("gross_margin")
        if fh_gm is not None and yh_gm is not None:
            try:
                gm_diff = abs(float(fh_gm) - float(yh_gm))
                if gm_diff > 3:  # >3pp
                    cross_source_notes.append(
                        f"Gross margin divergence: Finnhub={float(fh_gm):.1f}% vs "
                        f"Yahoo={float(yh_gm):.1f}% ({gm_diff:.1f}pp off)"
                    )
            except (ValueError, TypeError):
                pass

        # Compare revenue growth
        fh_rg = fh_data.get("revenueGrowthTTMYoy")
        yh_rg = yh_data.get("revenue_growth_yoy")
        if fh_rg is not None and yh_rg is not None:
            try:
                rg_diff = abs(float(fh_rg) - float(yh_rg))
                if rg_diff > 15:  # >15pp
                    cross_source_notes.append(
                        f"Revenue growth divergence: Finnhub={float(fh_rg):+.1f}% vs "
                        f"Yahoo={float(yh_rg):+.1f}% ({rg_diff:.0f}pp off) — prefer Yahoo as more current"
                    )
            except (ValueError, TypeError):
                pass

        # Flag Finnhub freshness warning if present
        freshness = fh_data.get("_freshness_warning")
        if freshness:
            cross_source_notes.append(freshness)

    elif finnhub_metrics and not yahoo_fin:
        # No Yahoo data to cross-reference — check Finnhub freshness alone
        fh_data = finnhub_metrics.data or {}
        freshness = fh_data.get("_freshness_warning")
        if freshness:
            cross_source_notes.append(freshness)

    if cross_source_notes:
        from pmacs.schemas.data import Evidence as Ev, EvidenceType as ET
        unique_evidence.append(Ev(
            id=f"validation_{ticker}_cross_source",
            source=DataSource.EDGAR,  # authoritative source
            type=ET.FINANCIAL_STATEMENT,
            ticker=ticker,
            fetched_at=now,
            content_hash=str(hash(str(cross_source_notes))),
            title=f"{ticker} cross-source validation — {len(cross_source_notes)} notes",
            data={"validation_notes": cross_source_notes, "recommendation": "Prefer EDGAR XBRL over Finnhub when values diverge"},
        ))

    # Build merged packet
    merged = EvidencePacket(
        ticker=ticker,
        cycle_id=cycle_id,
        evidence=unique_evidence,
        fetched_at=now,
        source_count=sources_ok,
        has_stale_data=has_stale,
    )

    # Staleness check on the merged packet
    freshness_results = check_all_freshness(merged, _STALENESS_BUDGETS)
    for fr in freshness_results:
        if fr.status == "STALE" and fr.criticality == CriticalityLevel.CRITICAL:
            has_stale = True

    # Rebuild with updated stale flag if needed
    if has_stale and not merged.has_stale_data:
        merged = EvidencePacket(
            ticker=ticker,
            cycle_id=cycle_id,
            evidence=unique_evidence,
            fetched_at=now,
            source_count=sources_ok,
            has_stale_data=True,
        )

    # Log summary
    source_breakdown: dict[str, int] = {}
    for ev in unique_evidence:
        source_breakdown[ev.source.value] = source_breakdown.get(ev.source.value, 0) + 1

    # ── Save evidence cache for next cycle ─────────────────────────────────
    _save_evidence_cache(ticker, unique_evidence, cycle_id)

    log_debug(
        "EVIDENCE_FETCHED",
        payload={
            "ticker": ticker,
            "total_evidence": len(unique_evidence),
            "sources_ok": sources_ok,
            "has_stale_data": merged.has_stale_data,
            "source_breakdown": source_breakdown,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Evidence fetched for {ticker}: {len(unique_evidence)} items from {sources_ok} sources",
    )

    return merged


def fetch_price(ticker: str, cycle_id: str) -> float | None:
    """Fetch the latest closing price for a ticker.

    Tries Polygon daily bars first (CRITICAL), then falls back to
    Finnhub quote.  Returns None if both fail.

    Args:
        ticker: Stock ticker symbol.
        cycle_id: Current cycle identifier.

    Returns:
        Latest close price, or None on failure.
    """
    with DataGateway() as gw:
        # Strategy 1: Polygon daily bars
        try:
            api_key = _read_key(DataSource.POLYGON, cycle_id)
            if api_key:
                from pmacs.data.sources.polygon import fetch_daily_bars
                packet = fetch_daily_bars(ticker, gw, api_key, cycle_id=cycle_id)
                if packet.evidence:
                    # Last bar has the most recent close
                    for ev in reversed(packet.evidence):
                        close = ev.data.get("close")
                        if close and float(close) > 0:
                            return float(close)
        except Exception:
            pass

        # Strategy 2: Finnhub quote
        try:
            api_key = _read_key(DataSource.FINNHUB, cycle_id)
            if api_key:
                from pmacs.data.sources.finnhub import fetch_quote
                packet = fetch_quote(ticker, gw, api_key, cycle_id=cycle_id)
                if packet.evidence and packet.evidence[0].data:
                    price = packet.evidence[0].data.get("c")
                    if price and float(price) > 0:
                        return float(price)
        except Exception:
            pass

        # Strategy 3: Alpaca data
        try:
            api_key = _read_key(DataSource.ALPACA_DATA, cycle_id)
            if api_key:
                from pmacs.data.sources.alpaca_data import fetch_bars
                packet = fetch_bars(ticker, gw, api_key, cycle_id=cycle_id)
                if packet.evidence:
                    for ev in reversed(packet.evidence):
                        close = ev.data.get("c")
                        if close and float(close) > 0:
                            return float(close)
        except Exception:
            pass

    # Strategy 4: Yahoo Finance (free, no API key needed)
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price and float(price) > 0:
            return float(price)
    except Exception:
        pass

    log_debug(
        "EVIDENCE_PRICE_UNAVAILABLE",
        payload={"ticker": ticker},
        level="WARN",
        error_code="DATA_UNAVAILABLE",
        cycle_id=cycle_id,
        msg=f"Could not fetch price for {ticker} from any source",
    )
    return None


# ---------------------------------------------------------------------------
# Evidence cache (accumulation between cycles)
# ---------------------------------------------------------------------------

def _ensure_cache_table() -> None:
    """Create the evidence_cache table if it doesn't exist."""
    from pmacs.config import data_dir
    db_path = data_dir() / "pmacs.db"
    con = _sql_connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS evidence_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            UNIQUE(ticker, evidence_id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ev_cache_ticker ON evidence_cache(ticker)")
    con.commit()
    con.close()


def _load_evidence_cache(ticker: str) -> list[Evidence]:
    """Load the most recent evidence for a ticker from the cache.

    Returns one Evidence per evidence_id (the freshest version).
    Stubs and error evidence are excluded.
    """
    try:
        from pmacs.config import data_dir
        db_path = data_dir() / "pmacs.db"
        if not db_path.exists():
            return []
        con = _sql_connect(db_path)
        rows = con.execute(
            "SELECT evidence_id, evidence_json FROM evidence_cache WHERE ticker = ? ORDER BY id DESC",
            (ticker,),
        ).fetchall()
        con.close()

        seen: set[str] = set()
        results: list[Evidence] = []
        for eid, ej in rows:
            if eid in seen:
                continue
            seen.add(eid)
            try:
                import json
                d = json.loads(ej)
                # Skip stubs and error evidence
                data = d.get("data", {})
                if isinstance(data, dict) and data.get("status") == "INSUFFICIENT_DATA":
                    continue
                if isinstance(data, dict) and data.get("error"):
                    continue
                ev = Evidence(**d)
                results.append(ev)
            except Exception:
                continue
        return results
    except Exception:
        return []


def _save_evidence_cache(ticker: str, evidence: list[Evidence], cycle_id: str) -> None:
    """Save evidence items to the cache, replacing previous entries per evidence_id."""
    try:
        _ensure_cache_table()
        from pmacs.config import data_dir
        import json
        db_path = data_dir() / "pmacs.db"
        con = _sql_connect(db_path)
        try:
            rows = []
            for ev in evidence:
                data = ev.data or {}
                if isinstance(data, dict) and data.get("status") == "INSUFFICIENT_DATA":
                    continue
                if isinstance(data, dict) and data.get("error") and "unavailable" in str(data.get("error", "")).lower():
                    continue
                ej = json.dumps(ev.model_dump(mode="json"), default=str)
                rows.append((
                    ticker, ev.id, ej,
                    ev.fetched_at.isoformat() if ev.fetched_at else "",
                    cycle_id,
                ))
            con.executemany(
                "INSERT OR REPLACE INTO evidence_cache (ticker, evidence_id, evidence_json, fetched_at, cycle_id) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        log_debug(
            "EVIDENCE_CACHE_SAVE_FAILED",
            payload={"ticker": ticker, "error": str(exc)[:200]},
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Failed to save evidence cache for {ticker}: {exc}",
        )
