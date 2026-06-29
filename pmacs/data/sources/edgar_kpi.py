"""EDGAR filing-narrative SaaS-KPI source — deterministic, no LLM.

Extracts NRR / GRR / ARR / RPO disclosures from a company's recent SEC filings
(10-K, 10-Q, 8-K, and foreign-issuer 20-F / 6-K). These KPIs are not in any
structured feed (yfinance/Finnhub/Polygon have no such fields); they live only in
filing narrative — MD&A and earnings-release exhibits. This source fetches the
filing documents, strips HTML, and regex-extracts the values with provenance.

Spec alignment (Five Non-Negotiables + "prefer N/A over inaccurate"):
  - No LLM, no manual analysis step — runs automatically in every evidence cycle.
  - Emits a value ONLY when a plausible number is found immediately adjacent to an
    unambiguous KPI phrase in a recent filing. Otherwise the field is None (N/A).
    We never fabricate or guess. Every emitted value carries provenance (form,
    filing date, accession, snippet) so the operator can spot-check.
  - Best-effort and isolated: a KPI miss/failure never marks a ticker stale
    (registered NICE_TO_HAVE in evidence_router) and never raises out of the cycle.

The extracted structured values are surfaced on the Ticker Data page via the
``explicit_kpis`` hook in ``pmacs.engines.ticker_metrics`` (authoritative — they
override the regex-over-prose fallback and the ARR-from-TTM-revenue approximation).

Reuses: ``_get_cik_for_ticker`` (evidence_router), ``DataGateway`` (rate bucket
``edgar_kpi``), the HTML stripper from ir_pages, and the numeric coercion idioms
from ticker_metrics. EDGAR needs no API key — only a User-Agent header.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

from pmacs.data.gateway import DataGateway
from pmacs.data.sources._html import strip_html as _shared_strip_html
from pmacs.logsys import log_debug
from pmacs.schemas.data import DataSource, Evidence, EvidencePacket, EvidenceType

# SEC requires a descriptive User-Agent on every request.
_USER_AGENT = "PMACS research@pmacs.local"
_HEADERS = {"Accept": "application/json, text/html, */*", "User-Agent": _USER_AGENT}

# Forms whose narrative may disclose KPIs. 20-F/6-K cover foreign private issuers
# (e.g. NBIS, DLO) which do not file 10-K/8-K.
_RELEVANT_FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
_MAX_FILINGS = 6
_MAX_EXHIBITS = 5   # max exhibit 99 docs fetched per 8-K/6-K filing
_DOC_CAP = 300_000  # cap each fetched document's stripped text

# KPI phrase + value extraction config.
# Each KPI: (key, phrase regexes, value kind, plausible range).
#   value kind "pct"  → capture a percentage, range is (lo, hi) in percent points
#   value kind "usd"  → capture a money amount, range is (lo, hi) in USD
_KPI_SPECS = [
    {
        "key": "nrr",
        "phrases": [
            r"net\s+revenue\s+retention",
            r"net\s+dollar\s+retention",
            r"dollar[-\s]based\s+net\s+retention",
            r"net\s+dollar\s+revenue\s+retention",
            r"\bNRR\b",
        ],
        "kind": "pct",
        "range": (50.0, 200.0),
        "related": [r"\bretention\b", r"recurring\s+revenue\s+retention"],
    },
    {
        "key": "grr",
        "phrases": [r"gross\s+(?:revenue\s+)?retention", r"\bGRR\b"],
        "kind": "pct",
        "range": (50.0, 100.0),
        "related": [r"\bretention\b"],
    },
    {
        "key": "arr",
        "phrases": [r"annual(?:ized)?\s+recurring\s+revenue", r"\bARR\b"],
        "kind": "usd",
        "range": (1_000_000.0, 1e13),
        "related": [r"recurring\s+revenue", r"subscription\s+revenue", r"\bcontract\s+value\b"],
    },
    {
        "key": "rpo",
        "phrases": [r"remaining\s+performance\s+obligations?", r"\bRPO\b"],
        "kind": "usd",
        "range": (1_000_000.0, 1e13),
        "related": [r"performance\s+obligation", r"\bbacklog\b", r"deferred\s+revenue"],
    },
]

# A percentage like 118, 118.5, 118.42 — captured with optional sign and decimals.
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# A money amount like $1.2B, 1.2 billion, $800M, 950 million.
# The single-letter suffixes (B/M/T) are guarded with a negative lookahead so the
# M in "MRR", the B in "Backlog", etc. are NOT parsed as a magnitude — that turned
# the ARR definition phrase "...multiplying it by 12. MRR..." into a bogus $12M ARR.
# Full words (billion/million/trillion) are matched first by the alternation and
# are unaffected.
_MONEY_RE = re.compile(
    r"\$?\s*(\d[\d,]*\.?\d*)\s*(billion|million|trillion|[BMT](?![A-Za-z]))?",
    re.IGNORECASE,
)
_MONEY_MULT = {
    "b": 1e9, "billion": 1e9,
    "m": 1e6, "million": 1e6,
    "t": 1e12, "trillion": 1e12,
}


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace (delegates to shared helper)."""
    return _shared_strip_html(html)


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money_to_usd(raw_num: str, suffix: str | None) -> float | None:
    """'$1.2B' / '1.2 billion' / '950 million' → USD float."""
    num = _to_float(raw_num.replace(",", ""))
    if num is None:
        return None
    if suffix:
        key = suffix.strip().lower()
        # Full words (billion/million/trillion) and single letters (B/M/T).
        mult = _MONEY_MULT.get(key) or _MONEY_MULT.get(key[:1])
        if mult is None:
            return None
        return round(num * mult, 2)
    return round(num, 2)


# ── Table-aware extraction ───────────────────────────────────────────────
# SaaS KPIs are frequently disclosed in an "Operating Metrics" / "Key
# Performance Indicators" table: a label cell ("Net revenue retention") with
# the figure in an adjacent cell. Flattening HTML to prose destroys that
# adjacency, so a prose-only scan misses them. We walk <table> structure with
# regex (no extra dependency, matching the rest of this module) and pair a
# label cell with a value cell in the same row. Still deterministic, still
# range-guarded — only emits when a plausible figure sits in a sibling cell.
_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)


def _table_rows(raw_html: str) -> list[list[str]]:
    """Return cleaned cell texts per row, for every <table> in ``raw_html``."""
    rows: list[list[str]] = []
    for tbl in _TABLE_RE.finditer(raw_html):
        for tr in _TR_RE.finditer(tbl.group(1)):
            cells = [_strip_html(c.group(1)) for c in _CELL_RE.finditer(tr.group(1))]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                rows.append(cells)
    return rows


def _extract_kpi_from_tables(raw_html: str, spec: dict) -> tuple[float | None, str | None]:
    """Find a KPI value paired with its label in a table row.

    Scans each row for a cell matching a KPI phrase, then scans the *other*
    cells in that row for a plausible value. Returns (value, row_snippet) or
    (None, None).
    """
    for row in _table_rows(raw_html):
        for i, cell in enumerate(row):
            if any(re.search(p, cell, re.IGNORECASE) for p in spec["phrases"]):
                for j, other in enumerate(row):
                    if j == i:
                        continue
                    val = _first_value(other, spec)
                    if val is not None:
                        return val, " ".join(row)[:160]
    return None, None


# ── Miss classification (diagnostic only — never affects emitted evidence) ──
# When a KPI comes back N/A, we want to know WHY, so the operator can decide
# whether to add a phrase variant, widen a window, or accept N/A as correct.
# Categories:
#   TERM_ABSENT          — neither the KPI phrase nor a related term appears.
#                          N/A is correct; the KPI is simply not disclosed.
#   TERM_VARIANT         — a related term appears but no exact KPI phrase.
#                          Phrasing gap: a new phrase synonym would catch it.
#   NUMBER_FAR           — exact phrase present, a plausible number exists
#                          within ±150 chars but outside the 80-char window.
#                          Window gap: widening the window would catch it.
#   NUMBER_OUT_OF_RANGE  — exact phrase present, nearby numbers exist but
#                          none fall in the plausible range. Eyeball this —
#                          could be a real disclosure our range rejects, or a
#                          different metric sharing the phrase.
#   NUMBER_NONE_NEAR     — exact phrase present but no number nearby in prose
#                          or tables. Likely disclosed as an image/chart, or
#                          the phrase refers to the concept qualitatively.
_MISS_WIDE = 150  # diagnostic window (vs 80 used for live extraction)


def _any_number(window: str, spec: dict) -> bool:
    """True if any pct/money token exists in window, ignoring plausibility range."""
    matches = _PCT_RE.findall(window) if spec["kind"] == "pct" else _MONEY_RE.findall(window)
    return bool(matches)


def _classify_miss(spec: dict, docs: list[tuple[str, str, str]]) -> tuple[str, str]:
    """Classify why a KPI was not found across ``docs`` = [(name, stripped, raw)].

    Returns (category, sample_snippet). Diagnostic only.
    """
    related_hit: str | None = None
    prose_hits: list[tuple[str, str, str, re.Match]] = []  # (name, stripped, phrase, m)
    for name, stripped, _raw in docs:
        if not stripped:
            continue
        for phrase in spec["phrases"]:
            for m in re.finditer(phrase, stripped, re.IGNORECASE):
                prose_hits.append((name, stripped, phrase, m))
        if related_hit is None:
            for rel in spec.get("related", []):
                m = re.search(rel, stripped, re.IGNORECASE)
                if m:
                    related_hit = stripped[max(0, m.start() - 40):m.start() + 80].strip()[:160]

    if prose_hits:
        # The phrase IS there. Why didn't we accept a value?
        for name, stripped, phrase, m in prose_hits:
            wide = stripped[max(0, m.start() - _MISS_WIDE): m.end() + _MISS_WIDE]
            if _first_value(wide, spec) is not None:
                return "NUMBER_FAR", wide[:160]  # would be caught by a wider window
            if _any_number(wide, spec):
                return "NUMBER_OUT_OF_RANGE", wide[:160]
        return "NUMBER_NONE_NEAR", prose_hits[0][1][
            max(0, prose_hits[0][3].start() - 40):prose_hits[0][3].end() + 80][:160]

    if related_hit is not None:
        return "TERM_VARIANT", related_hit
    return "TERM_ABSENT", ""


def _extract_kpi(text: str, spec: dict) -> tuple[float | None, str | None]:
    """Find the first plausible KPI value in ``text`` for the given spec.

    Returns (value, snippet) or (None, None). Searches a window around each phrase
    match; prefers a number immediately following the phrase, then one preceding it.
    Only accepts values within the spec's plausible range.
    """
    for phrase in spec["phrases"]:
        for m in re.finditer(phrase, text, re.IGNORECASE):
            # Window after the phrase (preferred: "NRR of 118%", "ARR: $1.2B").
            after = text[m.end(): m.end() + 80]
            val = _first_value(after, spec)
            if val is not None:
                return val, _snippet(text, m.start(), m.end())
            # Window before the phrase ("118% NRR").
            before = text[max(0, m.start() - 60): m.start()]
            val = _first_value(before, spec, reverse=True)
            if val is not None:
                return val, _snippet(text, m.start(), m.end())
    return None, None


def _first_value(window: str, spec: dict, reverse: bool = False) -> float | None:
    """First plausible value in ``window``; None if none in range. reverse scans R→L."""
    lo, hi = spec["range"]
    if spec["kind"] == "pct":
        matches = list(_PCT_RE.finditer(window))
    else:
        matches = list(_MONEY_RE.finditer(window))
    if reverse:
        matches.reverse()
    for mt in matches:
        if spec["kind"] == "pct":
            v = _to_float(mt.group(1))
        else:
            v = _money_to_usd(mt.group(1), mt.group(2))
        if v is not None and lo <= v <= hi:
            return round(v, 2)
    return None


def _snippet(text: str, start: int, end: int) -> str:
    s = max(0, start - 40)
    e = min(len(text), end + 80)
    return text[s:e].strip()[:160]


def _filing_docs(gateway: DataGateway, cik_int: int, accession: str, primary_doc: str,
                 form: str) -> list[tuple[str, str, str]]:
    """Return [(filename, stripped_text, raw_html)] for the primary doc + exhibits.

    The stripped text feeds the prose scan; the raw HTML feeds the table-aware
    scan (table cell adjacency is destroyed by stripping). For 8-K/6-K we also
    fetch exhibit 99.x (the earnings press release), where KPIs usually live.
    Each doc is capped at ``_DOC_CAP`` chars (both forms).
    """
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
    docs: list[tuple[str, str, str]] = []

    def _grab(name: str) -> tuple[str, str] | None:
        try:
            r = gateway.fetch("edgar_kpi", base + name, headers=_HEADERS)
            if r and r.status_code == 200 and r.text:
                raw = r.text[:_DOC_CAP]
                return _strip_html(raw), raw
        except Exception:
            return None
        return None

    if primary_doc:
        pair = _grab(primary_doc)
        if pair:
            docs.append((primary_doc, pair[0], pair[1]))

    if form in ("8-K", "6-K"):
        # Earnings releases split content across exhibit 99.x documents, and the
        # KPI can live in any of them (dLocal puts NRR in ex99-3, not ex99-1). Fetch
        # all ex99*.htm exhibits — capped — so we don't miss the disclosure.
        try:
            idx = gateway.fetch("edgar_kpi", base + "index.json", headers=_HEADERS)
            if idx and idx.status_code == 200:
                items = idx.json().get("directory", {}).get("item", [])
                ex_count = 0
                for it in items:
                    if ex_count >= _MAX_EXHIBITS:
                        break
                    name = it.get("name", "")
                    if (re.search(r"ex[-_]?99", name, re.IGNORECASE)
                            and name.lower().endswith((".htm", ".html"))
                            and name != primary_doc):
                        pair = _grab(name)
                        if pair:
                            docs.append((name, pair[0], pair[1]))
                            ex_count += 1
        except Exception:
            pass
    return docs


def _select_filings(gateway: DataGateway, cik_int: int, ticker: str,
                    cycle_id: str, now: datetime) -> list[dict]:
    """Submissions index → most-recent relevant filings. Empty on failure."""
    padded = str(cik_int).zfill(10)
    try:
        resp = gateway.fetch(
            "edgar_kpi",
            f"https://data.sec.gov/submissions/CIK{padded}.json",
            headers=_HEADERS,
        )
        sub = resp.json() if resp and resp.status_code == 200 else {}
    except Exception as exc:  # pragma: no cover - network dependent
        log_debug("DATA_UNAVAILABLE", payload={"source": "edgar_kpi", "ticker": ticker, "error": str(exc)[:200]},
                  level="INFO", error_code="DATA_UNAVAILABLE", cycle_id=cycle_id,
                  msg=f"edgar_kpi submissions fetch failed for {ticker}")
        return []

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    pdocs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", recent.get("fileDate", []))

    selected: list[dict] = []
    for i, form in enumerate(forms):
        # Normalize amendments ("20-F/A" → "20-F") so annual/quarterly amendments
        # are still scanned; the earnings exhibit is where KPIs usually live.
        norm = form.split("/")[0] if form else ""
        if norm in _RELEVANT_FORMS and i < len(accns) and i < len(pdocs):
            selected.append({
                "form": form,
                "accession": accns[i],
                "primary": pdocs[i],
                "date": dates[i] if i < len(dates) else "",
            })
        if len(selected) >= _MAX_FILINGS:
            break
    # Submissions are most-recent-first already.
    return selected


def _scan_doc(doc_name: str, stripped: str, raw: str, specs: list[dict],
              found: dict, filing: dict) -> None:
    """Scan one document's prose, then its tables, for each still-missing KPI."""
    if not stripped:
        return
    for spec in specs:
        if spec["key"] in found:
            continue
        val, snippet = _extract_kpi(stripped, spec)
        via = "prose"
        if val is None and raw:
            val, snippet = _extract_kpi_from_tables(raw, spec)
            via = "table"
        if val is not None:
            found[spec["key"]] = {
                "value": val,
                "form": filing["form"],
                "date": filing["date"],
                "accession": filing["accession"],
                "doc": doc_name,
                "snippet": snippet,
                "via": via,
            }


def fetch(cik: str, ticker: str, gateway: DataGateway, cycle_id: str = "") -> EvidencePacket:
    """Fetch recent filings and extract SaaS KPI disclosures.

    Returns an EvidencePacket with one ``edgar_kpi_{ticker}`` evidence item, or an
    empty packet on failure (never a stub — failure must not mark data stale).
    """
    now = datetime.now(timezone.utc)
    try:
        cik_int = int(cik)
    except (TypeError, ValueError):
        return _empty(ticker, cycle_id, now)

    selected = _select_filings(gateway, cik_int, ticker, cycle_id, now)
    if not selected:
        return _empty(ticker, cycle_id, now)

    # ── 2. Scan filings, most recent first, until all KPIs found ────────────
    found: dict[str, dict] = {}  # key → {value, form, date, accession, doc, snippet, via}
    for f in selected:
        if len(found) == len(_KPI_SPECS):
            break
        try:
            docs = _filing_docs(gateway, cik_int, f["accession"], f["primary"], f["form"])
        except Exception:  # pragma: no cover - network dependent
            docs = []
        for doc_name, stripped, raw in docs:
            _scan_doc(doc_name, stripped, raw, _KPI_SPECS, found, f)

    if not found:
        # No KPI disclosed in recent filings — emit nothing so the page shows N/A.
        log_debug("EVIDENCE_FETCHED", payload={"source": "edgar_kpi", "ticker": ticker,
                  "filings_scanned": len(selected), "kpis_found": 0}, level="INFO", cycle_id=cycle_id,
                  msg=f"edgar_kpi: no SaaS KPI disclosures found in {len(selected)} filings for {ticker}")
        return _empty(ticker, cycle_id, now)

    # ── 3. Build structured evidence ────────────────────────────────────────
    data: dict = {
        "source": "EDGAR narrative",
        "filings_scanned": len(selected),
        "nrr_pct": found.get("nrr", {}).get("value"),
        "grr_pct": found.get("grr", {}).get("value"),
        "arr_usd": found.get("arr", {}).get("value"),
        "rpo_usd": found.get("rpo", {}).get("value"),
    }
    disclosure_lines = []
    provenance: dict[str, dict] = {}
    label_map = {"nrr": "Net revenue retention", "grr": "Gross retention",
                 "arr": "Annual recurring revenue", "rpo": "Remaining performance obligations"}
    for key, label in label_map.items():
        if key in found:
            fnd = found[key]
            unit = "%" if key in ("nrr", "grr") else "$"
            val = fnd["value"]
            if unit == "%":
                disclosure_lines.append(f"{label}: {val:.1f}%")
            else:
                disclosure_lines.append(f"{label}: ${_fmt_money(val)}")
            provenance[key] = {
                "form": fnd["form"], "filed": fnd["date"],
                "accession": fnd["accession"], "snippet": fnd["snippet"],
                "via": fnd.get("via", "prose"),
            }
    data["disclosure_text"] = " ".join(disclosure_lines)
    data["provenance"] = provenance

    evidence = [Evidence(
        id=f"edgar_kpi_{ticker}",
        source=DataSource.EDGAR_KPI,
        type=EvidenceType.SEC_FILING,
        ticker=ticker,
        fetched_at=now,
        content_hash=str(hash(str(sorted(data.items(), key=lambda x: x[0])))),
        title=f"{ticker} SaaS KPIs (EDGAR narrative) — {len(found)} found in {len(selected)} filings",
        data=data,
    )]
    log_debug("EVIDENCE_FETCHED", payload={"source": "edgar_kpi", "ticker": ticker,
              "filings_scanned": len(selected), "kpis_found": len(found),
              "keys": list(found.keys())}, level="INFO", cycle_id=cycle_id,
              msg=f"edgar_kpi: {len(found)} KPI(s) for {ticker} from {len(selected)} filings")
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=evidence,
                          fetched_at=now, source_count=1)


def _fmt_money(v: float) -> str:
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:.0f}"


def _empty(ticker: str, cycle_id: str, now: datetime) -> EvidencePacket:
    return EvidencePacket(ticker=ticker, cycle_id=cycle_id, evidence=[],
                          fetched_at=now, source_count=0)


# ── One-shot refresh (writes only the edgar_kpi_{ticker} row into the cache) ──

def refresh(ticker: str) -> bool:
    """Re-fetch KPI disclosures for ``ticker`` and save to the evidence cache.

    Returns True if a (possibly empty-of-KPIs) evidence row was written. Raises on
    hard failure so the CLI can report it.
    """
    from pmacs.data.evidence_router import _get_cik_for_ticker, _save_evidence_cache
    cik = _get_cik_for_ticker(ticker)
    with DataGateway(timeout=15) as gw:
        packet = fetch(cik, ticker, gw, cycle_id="refresh")
    if packet.evidence:
        _save_evidence_cache(ticker, packet.evidence, cycle_id="refresh")
        return True
    return False


# ── Miss profiler (diagnostic; writes nothing) ────────────────────────────

# Human-readable guidance per miss category — printed so the operator knows
# the right next action without reading the classifier code.
_MISS_GUIDANCE = {
    "TERM_ABSENT": "KPI not disclosed in recent filings — N/A is correct.",
    "TERM_VARIANT": "Phrasing gap — add a phrase synonym to _KPI_SPECS to catch it.",
    "NUMBER_FAR": "Window gap — a wider prose window (currently 80) would catch it.",
    "NUMBER_OUT_OF_RANGE": "Range guard rejected a nearby number — eyeball it; may be a real disclosure or a different metric.",
    "NUMBER_NONE_NEAR": "Phrase present but no figure nearby — likely an image/chart or qualitative mention; N/A is probably correct.",
}


def profile(ticker: str) -> dict:
    """Classify, per missing KPI, WHY it was not extracted. Diagnostic only.

    Returns ``{ticker, found: {kpi: value}, misses: [{kpi, category, sample, guidance}]}``.
    Makes no extra network calls beyond the normal fetch path; never writes to
    the evidence cache. Used by the ``--debug-misses`` CLI mode.
    """
    from pmacs.data.evidence_router import _get_cik_for_ticker
    cik = _get_cik_for_ticker(ticker)
    now = datetime.now(timezone.utc)
    with DataGateway(timeout=15) as gw:
        cik_int = int(cik)
        selected = _select_filings(gw, cik_int, ticker, "profile", now)
        # Gather ALL docs (don't stop early) so every missing KPI can be classified.
        all_docs: list[tuple[str, str, str]] = []
        for f in selected:
            try:
                docs = _filing_docs(gw, cik_int, f["accession"], f["primary"], f["form"])
            except Exception:  # pragma: no cover - network dependent
                docs = []
            all_docs.extend(docs)

    found: dict[str, dict] = {}
    for doc_name, stripped, raw in all_docs:
        _scan_doc(doc_name, stripped, raw, _KPI_SPECS, found, {"form": "", "date": "",
                "accession": "", "primary": ""})

    misses = []
    for spec in _KPI_SPECS:
        if spec["key"] in found:
            continue
        category, sample = _classify_miss(spec, all_docs)
        misses.append({
            "kpi": spec["key"],
            "category": category,
            "sample": sample,
            "guidance": _MISS_GUIDANCE[category],
        })
    return {
        "ticker": ticker,
        "filings_scanned": len(selected),
        "found": {k: v["value"] for k, v in found.items()},
        "misses": misses,
    }


def _print_profile(ticker: str) -> None:
    try:
        result = profile(ticker)
    except Exception as exc:  # pragma: no cover - network dependent
        print(f"{ticker}: FAILED ({exc})")
        return
    found = result["found"]
    print(f"\n{ticker} — {result['filings_scanned']} filings scanned")
    if found:
        print(f"  found: {found}")
    if not result["misses"]:
        print("  no misses — all KPIs extracted")
        return
    for miss in result["misses"]:
        print(f"  [{miss['kpi'].upper()}] {miss['category']}")
        print(f"      {miss['guidance']}")
        if miss["sample"]:
            print(f"      sample: \"{miss['sample']}\"")


def main(argv: list[str]) -> int:
    debug_misses = False
    args = list(argv[1:])
    if args and args[0] in ("--debug-misses", "--profile-misses"):
        debug_misses = True
        args = args[1:]
    tickers = [t.upper() for t in args]
    if not tickers:
        print("usage: python -m pmacs.data.sources.edgar_kpi [--debug-misses] TICKER [TICKER ...]")
        return 1
    if debug_misses:
        for t in tickers:
            _print_profile(t)
        return 0
    for t in tickers:
        try:
            ok = refresh(t)
            from pmacs.data.evidence_router import _load_evidence_cache
            if ok:
                ev = {e.id: (e.data or {}) for e in _load_evidence_cache(t)}
                d = ev.get(f"edgar_kpi_{t}", {})
                kpis = {k: d.get(k) for k in ("nrr_pct", "grr_pct", "arr_usd", "rpo_usd") if d.get(k) is not None}
                print(f"{t}: refreshed — KPIs found: {kpis or 'none'}")
            else:
                print(f"{t}: no KPI disclosures found (N/A)")
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"{t}: FAILED ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
