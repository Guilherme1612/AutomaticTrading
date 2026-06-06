"""PersonaRunner base class — three-layer LLM validation pipeline (Agents.md §3).

Layer 1: llama-server HTTP call with GBNF grammar constraint
Layer 2: Pydantic model_validate() parse
Layer 3: Sanity validator

Retry: up to 2 retries with +0.05 temperature bump per retry (3 total attempts).
On all failures: log ABORTED_LLM debug event, return None.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from pmacs.agents.grammars import load_grammar
from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.data.gateway import sanitize_evidence
from pmacs.logsys import log_debug
from pmacs.schemas.agents import PersonaOutput
from pmacs.schemas.data import Evidence, EvidencePacket
from pmacs.storage.audit import AuditWriter


_DEFAULT_LOCAL_URL = "http://127.0.0.1:8080/completion"  # Fallback only; actual URL from model_registry.json


def _fmt_pct(v: object) -> str | None:
    """Format a percentage value, e.g. 22.0 → '+22.0%'.

    Finnhub returns values as percentages (not fractions), so no multiplication needed.
    """
    if v is None:
        return None
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_usd(v: object) -> str | None:
    """Format a raw dollar value with M/B suffix, e.g. 12_300_000_000 → '$12.3B'."""
    if v is None:
        return None
    try:
        f = float(v)
        if abs(f) >= 1e9:
            return f"${f / 1e9:.2f}B"
        if abs(f) >= 1e6:
            return f"${f / 1e6:.1f}M"
        return f"${f:,.0f}"
    except (TypeError, ValueError):
        return str(v)
MAX_RETRIES = 2  # 2 retries = 3 total attempts
TEMP_BUMP = 0.05


class PersonaRunner(ABC):
    """Abstract base for all persona runners.

    Subclasses must implement:
        - get_pydantic_model() -> type[BaseModel]
        - get_sanity_validator() -> BaseSanityValidator
        - build_prompt(evidence, episodic_context) -> str
    """

    def __init__(
        self,
        persona_name: str,
        model_config: dict[str, Any] | None = None,
        grammar_name: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 3072,
        cycle_id: str = "",
        audit_writer: AuditWriter | None = None,
        simulation_mode: bool = False,
        billing_ctx: dict[str, Any] | None = None,
    ) -> None:
        self.persona_name = persona_name
        self.model_config = model_config or {}
        self.grammar_name = grammar_name or persona_name
        self.base_temperature = temperature
        self.max_tokens = max_tokens
        self.cycle_id = cycle_id
        self._audit = audit_writer
        self.simulation_mode = simulation_mode
        self._last_call_usage: dict | None = None  # Side-channel for billing (Phase 16)
        self._billing_ctx = billing_ctx  # {"sqlite_conn": ..., "duckdb_adapter": ..., "model_id": ...}
        self._cycle_cumulative_actual: float = 0.0
        self._cycle_cumulative_estimated: float = 0.0

    @abstractmethod
    def get_pydantic_model(self) -> type[BaseModel]:
        """Return the Pydantic model class for this persona's output."""

    @abstractmethod
    def get_sanity_validator(self) -> BaseSanityValidator:
        """Return the sanity validator instance for this persona."""

    @abstractmethod
    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        """Build the LLM prompt from evidence packets and optional episodic context."""

    def run(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> PersonaOutput | None:
        """Execute the three-layer validation pipeline.

        1. Call llama-server with grammar
        2. Parse response through Pydantic model_validate
        3. Run sanity validator

        On any layer failure: retry with +0.05 temp.
        After 3 total failures: log ABORTED_LLM, return None.
        """
        grammar_text = self._load_grammar()
        evidence = self._sanitize_evidence_packets(evidence)
        if episodic_context:
            episodic_context = sanitize_evidence(
                episodic_context, source="episodic", cycle_id=self.cycle_id,
            )
        prompt = self.build_prompt(evidence, episodic_context)
        model_cls = self.get_pydantic_model()
        validator = self.get_sanity_validator()
        model_hash = self._get_model_hash()

        last_error: str = ""

        for attempt in range(MAX_RETRIES + 1):
            current_temp = self.base_temperature + (attempt * TEMP_BUMP)
            latency_ms: float = 0.0
            raw_output: str = ""

            # Layer 1: HTTP call to llama-server
            try:
                # Pre-flight budget check (Phase 16 — PRD §8)
                self._check_preflight_budget(prompt)

                t0 = time.monotonic()
                raw_output = self._call_llm(prompt, grammar_text, current_temp)
                latency_ms = (time.monotonic() - t0) * 1000

                # Post-call billing (Phase 16 — PRD §7, §9)
                self._record_call_billing(prompt, latency_ms)
            except httpx.ConnectError as exc:
                last_error = f"LLM connection refused: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_CONNECTION_REFUSED",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: connection refused",
                )
                continue
            except httpx.TimeoutException as exc:
                last_error = f"LLM timeout: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_TIMEOUT",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: timeout",
                )
                continue
            except Exception as exc:
                last_error = f"LLM call error: {exc}"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt, "error": str(exc)},
                    level="WARN",
                    error_code="LLM_OUTPUT_EMPTY",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: unexpected error",
                )
                continue

            if not raw_output or not raw_output.strip():
                last_error = "LLM returned empty output"
                log_debug(
                    "LLM_CALL_FAILED",
                    payload={"persona": self.persona_name, "attempt": attempt},
                    level="WARN",
                    error_code="LLM_OUTPUT_EMPTY",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: empty output",
                )
                continue

            # Layer 2: Pydantic validation
            parsed: dict[str, Any] | None = None
            try:
                # Try to extract JSON from the raw output (may have surrounding text)
                json_str = self._extract_json(raw_output)
                parsed = json.loads(json_str)
                model_instance = model_cls.model_validate(parsed)
                parsed = model_instance.model_dump()
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = f"Pydantic parse failed: {exc}"
                log_debug(
                    "LLM_PARSE_FAILED",
                    payload={
                        "persona": self.persona_name,
                        "attempt": attempt,
                        "raw_snippet": raw_output[:200],
                        "error": str(exc),
                    },
                    level="WARN",
                    error_code="GBNF_PARSE_FAIL",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: parse/validation failed",
                )
                # Audit the failed attempt
                self._audit_llm_call(
                    prompt=prompt,
                    output=raw_output,
                    model_hash=model_hash,
                    grammar_version=self.grammar_name,
                    retry_count=attempt,
                    latency_ms=latency_ms,
                    success=False,
                )
                continue

            # Layer 3: Sanity validation
            sanity_result: SanityResult = validator.validate(parsed, evidence)
            if not sanity_result.passed:
                last_error = f"Sanity check failed: {sanity_result.reason}"
                log_debug(
                    "LLM_SANITY_FAILED",
                    payload={
                        "persona": self.persona_name,
                        "attempt": attempt,
                        "reason": sanity_result.reason,
                    },
                    level="WARN",
                    error_code="SANITY_VALIDATION_FAIL",
                    cycle_id=self.cycle_id,
                    msg=f"Attempt {attempt + 1}: sanity check failed",
                )
                self._audit_llm_call(
                    prompt=prompt,
                    output=raw_output,
                    model_hash=model_hash,
                    grammar_version=self.grammar_name,
                    retry_count=attempt,
                    latency_ms=latency_ms,
                    success=False,
                )
                continue

            # All three layers passed — build PersonaOutput
            persona_output = PersonaOutput(
                persona=self._get_persona_enum(),
                ticker=self._extract_ticker(evidence),
                cycle_id=self.cycle_id,
                raw_output=raw_output,
                grammar_version=self.grammar_name,
                model_hash=model_hash,
                temperature=current_temp,
                retry_count=attempt,
            )

            # Audit successful call
            self._audit_llm_call(
                prompt=prompt,
                output=raw_output,
                model_hash=model_hash,
                grammar_version=self.grammar_name,
                retry_count=attempt,
                latency_ms=latency_ms,
                success=True,
            )

            return persona_output

        # All attempts exhausted
        log_debug(
            "LLM_ABORTED",
            payload={
                "persona": self.persona_name,
                "attempts": MAX_RETRIES + 1,
                "last_error": last_error,
            },
            level="WARN",
            error_code="ABORTED_LLM",
            cycle_id=self.cycle_id,
            msg=f"All {MAX_RETRIES + 1} attempts failed for {self.persona_name}",
        )

        # Simulation fallback: generate deterministic conservative output
        if self.simulation_mode:
            return self._generate_simulation_output(evidence)

        return None

    def _generate_simulation_output(
        self, evidence: list[EvidencePacket]
    ) -> PersonaOutput | None:
        """Generate a deterministic simulation output when LLM is unavailable.

        Uses the simulation module to produce conservative persona outputs
        that pass Pydantic validation and sanity checks.
        """
        from pmacs.agents.simulation import make_simulation_output

        model_cls = self.get_pydantic_model()
        sim_data = make_simulation_output(
            persona_name=self.persona_name,
            model_cls=model_cls,
            evidence=evidence,
            cycle_id=self.cycle_id,
        )

        if sim_data is None:
            log_debug(
                "LLM_SIMULATION_UNSUPPORTED",
                payload={"persona": self.persona_name},
                level="WARN",
                error_code="ABORTED_LLM",
                cycle_id=self.cycle_id,
                msg=f"Simulation not supported for {self.persona_name}",
            )
            return None

        # The simulation output is already validated by make_simulation_output
        raw_output = json.dumps(sim_data, default=str)

        log_debug(
            "LLM_SIMULATION_USED",
            payload={
                "persona": self.persona_name,
                "ticker": self._extract_ticker(evidence),
            },
            level="INFO",
            cycle_id=self.cycle_id,
            msg=f"Simulation output generated for {self.persona_name}",
        )

        # Audit the simulation usage
        self._audit_llm_call(
            prompt="[SIMULATION]",
            output=raw_output,
            model_hash="simulation",
            grammar_version=self.grammar_name,
            retry_count=MAX_RETRIES + 1,
            latency_ms=0.0,
            success=True,
        )

        return PersonaOutput(
            persona=self._get_persona_enum(),
            ticker=self._extract_ticker(evidence),
            cycle_id=self.cycle_id,
            raw_output=raw_output,
            grammar_version=self.grammar_name,
            model_hash="simulation",
            temperature=0.0,
            retry_count=MAX_RETRIES + 1,
        )

    def _load_grammar(self) -> str:
        """Load GBNF grammar text for this persona."""
        try:
            return load_grammar(self.grammar_name)
        except FileNotFoundError:
            return ""

    def _sanitize_evidence_packets(
        self, packets: list[EvidencePacket],
    ) -> list[EvidencePacket]:
        """Sanitize all text fields in evidence packets (Agents.md §19.2).

        Returns new packets — never mutates (anti-pattern §16.4).
        """
        sanitized_packets: list[EvidencePacket] = []
        for packet in packets:
            new_evidence: list[Evidence] = []
            for ev in packet.evidence:
                new_data = {
                    k: sanitize_evidence(v, source=ev.source.value, cycle_id=self.cycle_id)
                    if isinstance(v, str)
                    else v
                    for k, v in ev.data.items()
                }
                new_title = (
                    sanitize_evidence(ev.title, source=ev.source.value, cycle_id=self.cycle_id)
                    if ev.title
                    else ev.title
                )
                new_evidence.append(ev.model_copy(update={"data": new_data, "title": new_title}))
            sanitized_packets.append(packet.model_copy(update={"evidence": new_evidence}))
        return sanitized_packets

    @staticmethod
    def format_evidence_for_prompt(evidence: list[EvidencePacket]) -> str:
        """Render evidence packets as structured prompt text.

        Produces clean, agent-readable evidence blocks. Financial metrics and
        SEC XBRL facts are expanded as key-value lines so the LLM sees real
        numbers rather than truncated JSON or just a title.

        Rules:
        - fundamentals_*_metrics  → full KPI table (revenue growth, margins, etc.)
        - edgar_*_financials       → reported revenue/EPS growth from XBRL
        - edgar_*_cashflow         → operating cash flow figures
        - edgar_*_filings          → filing dates (brief)
        - Market data (quotes)     → price + change summary
        - Everything else          → title + up to 500 chars of data summary
        """
        lines: list[str] = []
        for packet in evidence:
            for ev in getattr(packet, "evidence", []):
                ev_id = getattr(ev, "id", "unknown")
                title = getattr(ev, "title", None) or ""
                data = getattr(ev, "data", {}) or {}
                source_val = getattr(getattr(ev, "source", None), "value", "")

                # ── Financial metrics (Finnhub stock/metric) ──────────────────
                if ev_id.endswith("_metrics") and source_val == "fundamentals":
                    lines.append(f"\n[{ev_id}] {title}")
                    # Data quality warning if anomalies detected
                    quality_warning = data.get("_data_quality_warning")
                    if quality_warning:
                        lines.append(f"  ** {quality_warning} **")
                    # Freshness warning if data is stale
                    freshness_warning = data.get("_freshness_warning")
                    if freshness_warning:
                        lines.append(f"  ** {freshness_warning} **")
                    # Show data period for transparency
                    most_recent_period = data.get("_most_recent_period")
                    if most_recent_period:
                        age_days = data.get("_data_age_days")
                        age_str = f" ({age_days} days ago)" if age_days else ""
                        lines.append(f"  Data period: {most_recent_period}{age_str}")
                    _kv = [
                        ("Revenue growth TTM YoY", data.get("revenueGrowthTTMYoy_pct")),
                        ("Revenue growth 3Y CAGR", _fmt_pct(data.get("revenueGrowth3Y"))),
                        ("Revenue growth 5Y CAGR", _fmt_pct(data.get("revenueGrowth5Y"))),
                        ("Revenue TTM", _fmt_usd(data.get("revenueTTM"))),
                        ("EPS TTM", data.get("epsTTM")),
                        ("EPS growth TTM YoY", data.get("epsGrowthTTMYoy_pct")),
                        ("Gross margin TTM", data.get("grossMarginTTM_pct")),
                        ("Net margin TTM", data.get("netProfitMarginTTM_pct")),
                        ("FCF margin TTM", data.get("fcfMarginTTM_pct")),
                        ("Operating margin TTM", _fmt_pct(data.get("operatingMarginTTM"))),
                        ("ROIC TTM", _fmt_pct(data.get("roicTTM"))),
                        ("P/E (normalized)", data.get("peNormalizedAnnual")),
                        ("EV/EBITDA TTM", data.get("evToEbitdaTTM")),
                        ("Debt/Equity", data.get("totalDebtToEquityAnnual")),
                        ("52-week return", _fmt_pct(data.get("52WeekPriceReturnDaily"))),
                    ]
                    for label, val in _kv:
                        if val is not None:
                            lines.append(f"  {label}: {val}")
                    # Show which fields are flagged anomalous (agents should prefer EDGAR)
                    anomalous = data.get("_anomalous_fields", [])
                    if anomalous:
                        lines.append(f"  FLAGGED UNRELIABLE: {', '.join(anomalous)} — prefer EDGAR XBRL data")
                    # Annual series
                    for field in ("annual_revenue", "annual_eps", "annual_netIncome", "annual_freeCashFlow"):
                        series = data.get(field)
                        if series:
                            series_str = " | ".join(
                                f"{e['period']}: {_fmt_usd(e['v']) if 'revenue' in field or 'Income' in field or 'Cash' in field else e['v']}"
                                for e in series if e.get("v") is not None
                            )
                            lines.append(f"  {field.replace('annual_', '').capitalize()} history: {series_str}")
                    continue

                # ── Company profile ───────────────────────────────────────────
                if ev_id.endswith("_profile") and source_val == "fundamentals":
                    lines.append(f"\n[{ev_id}] {title}")
                    for k in ("name", "exchange", "finnhubIndustry", "marketCapitalization", "shareOutstanding"):
                        if k in data:
                            lines.append(f"  {k}: {data[k]}")
                    continue

                # ── EDGAR XBRL financials ─────────────────────────────────────
                if ev_id.endswith("_financials") and source_val == "edgar":
                    lines.append(f"\n[{ev_id}] {title}")
                    for k in ("revenue_yoy_growth", "eps_yoy_growth", "net_income_yoy_growth", "opcf_yoy_growth", "gross_margin_pct"):
                        if k in data:
                            lines.append(f"  {k.replace('_', ' ').title()}: {data[k]}")
                    rev_r = data.get("revenue_most_recent", {})
                    if rev_r:
                        lines.append(f"  Most recent revenue: {_fmt_usd(rev_r.get('value_usd'))} (period {rev_r.get('period_end')}, {rev_r.get('form')})")
                    eps_r = data.get("eps_most_recent", {})
                    if eps_r:
                        lines.append(f"  Most recent EPS: {eps_r.get('value')} (period {eps_r.get('period_end')}, {eps_r.get('form')})")
                    ni_r = data.get("net_income_most_recent", {})
                    if ni_r:
                        lines.append(f"  Most recent net income: {_fmt_usd(ni_r.get('value_usd'))} (period {ni_r.get('period_end')})")
                    continue

                # ── EDGAR cash flow ───────────────────────────────────────────
                if ev_id.endswith("_cashflow") and source_val == "edgar":
                    lines.append(f"\n[{ev_id}] {title}")
                    opcf_r = data.get("opcf_most_recent", {})
                    if opcf_r:
                        lines.append(f"  Operating cash flow: {_fmt_usd(opcf_r.get('value_usd'))} (period {opcf_r.get('period_end')}, {opcf_r.get('form')})")
                    if "opcf_yoy_growth" in data:
                        lines.append(f"  Operating CF YoY growth: {data['opcf_yoy_growth']}")
                    # Derived ratios
                    eq = data.get("earnings_quality")
                    fcf_ni = data.get("fcf_to_net_income")
                    if eq:
                        lines.append(f"  Earnings quality: {eq}")
                    if fcf_ni:
                        for r in fcf_ni[:4]:
                            lines.append(f"    {r['period']}: OCF/NI = {r['fcf_to_ni']:.2f}x")
                    op_lev = data.get("operating_leverage")
                    op_lev_interp = data.get("operating_leverage_interpretation")
                    if op_lev is not None:
                        lines.append(f"  Operating leverage: {op_lev:.2f}x")
                    if op_lev_interp:
                        lines.append(f"    {op_lev_interp}")
                    continue

                # ── EDGAR filing index ────────────────────────────────────────
                if ev_id.endswith("_filings") and source_val == "edgar":
                    lines.append(f"\n[{ev_id}] {title}")
                    lines.append(f"  Most recent 10-K: {data.get('most_recent_10k', 'N/A')}")
                    lines.append(f"  Most recent 10-Q: {data.get('most_recent_10q', 'N/A')}")
                    recent = data.get("filings", [])[:5]
                    if recent:
                        for f in recent:
                            lines.append(f"  {f.get('form', '')} filed {f.get('date', '')}")
                    continue

                # ── Cross-source validation notes ────────────────────────────
                if ev_id.startswith("validation_") and "cross_source" in ev_id:
                    notes = data.get("validation_notes", [])
                    rec = data.get("recommendation", "")
                    if notes:
                        lines.append(f"\n[{ev_id}] {title}")
                        for note in notes:
                            lines.append(f"  ** {note} **")
                        if rec:
                            lines.append(f"  Recommendation: {rec}")
                    continue

                # ── Press / news items ────────────────────────────────────────
                if source_val == "press" or ev_id.startswith("press_"):
                    # Catalyst timeline — structured event calendar
                    if ev_id.endswith("_catalyst_timeline"):
                        total = data.get("total_catalysts", 0)
                        cat_summary = data.get("category_summary", "")
                        lines.append(f"\n[{ev_id}] {title}")
                        lines.append(f"  Total catalyst events: {total}")
                        if cat_summary:
                            lines.append(f"  Categories: {cat_summary}")
                        for evt in data.get("events", [])[:10]:
                            date = evt.get("date", "")
                            cat = evt.get("category", "")
                            headline = evt.get("headline", "")
                            lines.append(f"  {date} [{cat}] {headline}")
                        continue

                    headline = data.get("headline", title or "")
                    summary = data.get("summary", "")
                    published = data.get("published_utc", "")
                    category = data.get("category", "")
                    source_name = data.get("source_name", "")
                    header = f"\n[{ev_id}] {headline}"
                    if published:
                        header += f" ({published})"
                    if category:
                        header += f" [{category}]"
                    if source_name:
                        header += f" — {source_name}"
                    lines.append(header)
                    if summary:
                        lines.append(f"  {summary[:400]}")
                    continue

                # ── Analyst recommendations ───────────────────────────────────
                if ev_id.endswith("_analyst_recommendations"):
                    lines.append(f"\n[{ev_id}] {title}")
                    consensus = data.get("consensus", "N/A")
                    total = data.get("total_analysts", 0)
                    bullish_pct = data.get("bullish_pct")
                    bearish_pct = data.get("bearish_pct")
                    period = data.get("period", "")
                    lines.append(f"  Consensus: {consensus} ({total} analysts, period {period})")
                    if bullish_pct is not None:
                        lines.append(
                            f"  Buy/StrongBuy: {data.get('strong_buy',0)+data.get('buy',0)} "
                            f"({bullish_pct:.1f}%) | Hold: {data.get('hold',0)} | "
                            f"Sell/StrongSell: {data.get('sell',0)+data.get('strong_sell',0)} "
                            f"({bearish_pct:.1f}%)"
                        )
                    continue

                # ── Analyst price target ──────────────────────────────────────
                if ev_id.endswith("_price_target"):
                    lines.append(f"\n[{ev_id}] {title}")
                    mean = data.get("target_mean")
                    low = data.get("target_low")
                    high = data.get("target_high")
                    median = data.get("target_median")
                    if mean is not None:
                        lines.append(f"  Mean target: ${float(mean):.2f} | Median: ${float(median):.2f}" if median else f"  Mean target: ${float(mean):.2f}")
                    if low is not None and high is not None:
                        lines.append(f"  Range: ${float(low):.2f} – ${float(high):.2f}")
                    upside = data.get("upside_to_mean_pct")
                    upside_med = data.get("upside_to_median_pct")
                    if upside is not None:
                        lines.append(f"  Upside to mean: {upside:+.1f}%")
                    if upside_med is not None:
                        lines.append(f"  Upside to median: {upside_med:+.1f}%")
                    n_analysts = data.get("num_analysts")
                    if n_analysts:
                        lines.append(f"  Covering analysts: {n_analysts}")
                    continue

                # ── Analyst recommendation trend ──────────────────────────────
                if ev_id.endswith("_analyst_trend"):
                    lines.append(f"\n[{ev_id}] {title}")
                    trend = data.get("trend", "")
                    delta = data.get("delta_pct")
                    by_period = data.get("bullish_pct_by_period", [])
                    periods = data.get("periods", [])
                    if trend:
                        lines.append(f"  Trend: {trend} ({delta:+.1f}% bullish shift over {len(by_period)} periods)")
                    if by_period and periods:
                        for i, pct in enumerate(by_period):
                            if pct is not None and i < len(periods):
                                lines.append(f"    {periods[i]}: {pct:.1f}% bullish")
                    continue

                # ── Consensus estimates (NTM + next quarter) ──────────────────
                if ev_id.endswith("_consensus_estimates"):
                    lines.append(f"\n[{ev_id}] {title}")
                    for label, key in [
                        ("NTM EPS consensus", "ntm_eps_consensus"),
                        ("NTM EPS range", None),  # composite
                        ("NTM revenue consensus", "ntm_revenue_consensus"),
                        ("Next Q EPS consensus", "next_q_eps_consensus"),
                        ("Next Q revenue consensus", "next_q_revenue_consensus"),
                        ("Next Q period", "next_q_period"),
                    ]:
                        if key:
                            val = data.get(key)
                            if val is not None:
                                if "revenue" in key:
                                    lines.append(f"  {label}: {_fmt_usd(val)}")
                                else:
                                    lines.append(f"  {label}: {val}")
                    # EPS range
                    eps_lo = data.get("ntm_eps_low")
                    eps_hi = data.get("ntm_eps_high")
                    if eps_lo is not None and eps_hi is not None:
                        lines.append(f"  NTM EPS range: ${float(eps_lo):.2f} – ${float(eps_hi):.2f}")
                    continue

                # ── Estimate revisions ────────────────────────────────────────
                if ev_id.endswith("_estimate_revisions"):
                    lines.append(f"\n[{ev_id}] {title}")
                    trend = data.get("revision_trend", "")
                    avg_surprise = data.get("avg_eps_surprise_pct")
                    beats = data.get("positive_surprise_quarters", "")
                    interp = data.get("interpretation", "")
                    if trend:
                        lines.append(f"  Revision trend: {trend}")
                    if avg_surprise is not None:
                        lines.append(f"  Avg EPS surprise: {avg_surprise:+.1f}%")
                    if beats:
                        lines.append(f"  Positive surprise quarters: {beats}")
                    if interp:
                        lines.append(f"  {interp}")
                    continue

                # ── IR page content ───────────────────────────────────────────
                if ev_id.startswith("ir_"):
                    content_text = data.get("content", "")
                    if content_text:
                        lines.append(f"\n[{ev_id}] {title}")
                        lines.append(f"  {content_text[:1500]}")
                    continue

                # ── Earnings history (actual vs estimate) ─────────────────────
                if ev_id.endswith("_earnings_history"):
                    lines.append(f"\n[{ev_id}] {title}")
                    beat_rate = data.get("beat_rate", "")
                    rev_beat_rate = data.get("revenue_beat_rate", "")
                    if beat_rate:
                        lines.append(f"  EPS beat rate: {beat_rate}")
                    if rev_beat_rate:
                        lines.append(f"  Revenue beat rate: {rev_beat_rate}")
                    for q in data.get("history", [])[:4]:
                        period = q.get("period", "?")
                        actual = q.get("actual_eps")
                        est = q.get("estimate_eps")
                        surp = q.get("surprise_pct")
                        rev_actual = q.get("revenue_actual")
                        rev_est = q.get("revenue_estimate")
                        parts = [f"  {period}:"]
                        if actual is not None and est is not None:
                            parts.append(f"EPS actual={actual} vs est={est}")
                            if surp is not None:
                                parts.append(f"({surp:+.1f}% surprise)")
                        if rev_actual is not None:
                            parts.append(f"revenue={_fmt_usd(rev_actual)}")
                            if rev_est:
                                parts.append(f"vs est={_fmt_usd(rev_est)}")
                        lines.append(" ".join(parts))
                    continue

                # ── Earnings calendar (upcoming date + estimates) ──────────────
                if ev_id.endswith("_earnings_calendar"):
                    lines.append(f"\n[{ev_id}] {title}")
                    next_date = data.get("next_earnings_date", "")
                    if next_date:
                        lines.append(f"  Next earnings: {next_date}")
                    eps_est = data.get("eps_estimate")
                    rev_est = data.get("revenue_estimate")
                    if eps_est is not None:
                        lines.append(f"  Consensus EPS estimate: {eps_est}")
                    if rev_est is not None:
                        lines.append(f"  Consensus revenue estimate: {_fmt_usd(rev_est)}")
                    continue

                # ── Market data (quote) ───────────────────────────────────────
                if "quote" in ev_id or source_val in ("finnhub", "polygon", "alpaca_data"):
                    price = data.get("c") or data.get("close")
                    pct = data.get("dp")  # Finnhub daily % change
                    summary = title or f"{source_val} market data"
                    if price:
                        summary += f" — price ${float(price):.2f}"
                    if pct is not None:
                        summary += f", {float(pct):+.2f}% today"
                    lines.append(f"\n[{ev_id}] {summary}")
                    continue

                # ── Technical: Moving averages ────────────────────────────────
                if ev_id.endswith("_moving_averages") and source_val == "technical":
                    lines.append(f"\n[{ev_id}] {title}")
                    price = data.get("current_price")
                    sma50 = data.get("sma_50")
                    sma200 = data.get("sma_200")
                    trend = data.get("trend", "")
                    if price:
                        lines.append(f"  Current price: ${float(price):.2f}")
                    if sma50:
                        lines.append(f"  SMA(50): ${float(sma50):.2f}")
                    if sma200:
                        lines.append(f"  SMA(200): ${float(sma200):.2f}")
                    if trend:
                        lines.append(f"  Trend: {trend.replace('_', ' ')}")
                    d50 = data.get("dist_from_sma50_pct")
                    d200 = data.get("dist_from_sma200_pct")
                    if d50 is not None:
                        lines.append(f"  Distance from SMA50: {d50:+.1f}%")
                    if d200 is not None:
                        lines.append(f"  Distance from SMA200: {d200:+.1f}%")
                    h52 = data.get("high_52w")
                    l52 = data.get("low_52w")
                    dh52 = data.get("dist_from_high_52w_pct")
                    dl52 = data.get("dist_from_low_52w_pct")
                    if h52:
                        lines.append(f"  52-week range: ${float(l52 or 0):.2f} – ${float(h52):.2f}")
                    if dh52 is not None:
                        lines.append(f"  Distance from 52w high: {dh52:+.1f}%")
                    if dl52 is not None:
                        lines.append(f"  Distance from 52w low: {dl52:+.1f}%")
                    continue

                # ── Technical: Momentum (RSI) ─────────────────────────────────
                if ev_id.endswith("_momentum") and source_val == "technical":
                    lines.append(f"\n[{ev_id}] {title}")
                    rsi = data.get("rsi_14")
                    roc20 = data.get("roc_20d_pct")
                    roc50 = data.get("roc_50d_pct")
                    if rsi is not None:
                        lines.append(f"  RSI(14): {rsi:.1f} {'(OVERBOUGHT)' if data.get('overbought') else '(OVERSOLD)' if data.get('oversold') else ''}")
                    if roc20 is not None:
                        lines.append(f"  20-day rate of change: {roc20:+.1f}%")
                    if roc50 is not None:
                        lines.append(f"  50-day rate of change: {roc50:+.1f}%")
                    continue

                # ── Yahoo forward valuation ────────────────────────────────────
                if ev_id.endswith("_forward_valuation") and source_val == "yahoo":
                    lines.append(f"\n[{ev_id}] {title}")
                    fwd_pe = data.get("forward_pe")
                    peg = data.get("peg_ratio")
                    fwd_eps = data.get("forward_eps")
                    trail_eps = data.get("trailing_eps")
                    fwd_eps_growth = data.get("forward_eps_growth_pct")
                    ny_eps_growth = data.get("next_year_eps_growth_pct")
                    earn_growth = data.get("earnings_growth_yoy")
                    rev_growth = data.get("revenue_growth_yoy")
                    ntm_rev = data.get("ntm_revenue_consensus")
                    eps_trend = data.get("eps_trend") or {}
                    if fwd_pe is not None:
                        lines.append(f"  Forward P/E: {fwd_pe:.2f}")
                    if peg is not None:
                        lines.append(f"  PEG ratio: {peg:.2f}")
                    if fwd_eps is not None:
                        lines.append(f"  Forward EPS: ${fwd_eps:.2f}")
                    if trail_eps is not None:
                        lines.append(f"  Trailing EPS: ${trail_eps:.2f}")
                    if fwd_eps_growth is not None:
                        lines.append(f"  Forward EPS growth: {fwd_eps_growth:+.1f}%")
                    if ny_eps_growth is not None:
                        lines.append(f"  Next year EPS growth: {ny_eps_growth:+.1f}%")
                    if earn_growth is not None:
                        lines.append(f"  Earnings growth YoY: {earn_growth:+.1f}%")
                    if rev_growth is not None:
                        lines.append(f"  Revenue growth YoY: {rev_growth:+.1f}%")
                    if ntm_rev is not None:
                        lines.append(f"  NTM revenue consensus: {_fmt_usd(ntm_rev)}")
                    if eps_trend:
                        for label, key in [("Current Q EPS est", "current_q"), ("Next Q EPS est", "next_q"), ("Current year EPS est", "current_year"), ("Next year EPS est", "next_year")]:
                            val = eps_trend.get(key)
                            if val is not None:
                                lines.append(f"  {label}: {val}")
                    continue

                # ── Yahoo financial data (cross-reference) ──────────────────────
                if ev_id.endswith("_financials") and source_val == "yahoo":
                    lines.append(f"\n[{ev_id}] {title}")
                    for label, key in [
                        ("TTM Revenue", "ttm_revenue"),
                        ("TTM FCF", "ttm_fcf"),
                        ("TTM Operating CF", "ttm_operating_cf"),
                        ("Revenue growth YoY", "revenue_growth_yoy"),
                        ("Earnings growth YoY", "earnings_growth_yoy"),
                        ("Gross margin", "gross_margin"),
                        ("Operating margin", "operating_margin"),
                        ("Net margin", "net_margin"),
                        ("ROE", "roe"),
                    ]:
                        val = data.get(key)
                        if val is not None:
                            if key in ("ttm_revenue", "ttm_fcf", "ttm_operating_cf"):
                                lines.append(f"  {label}: {_fmt_usd(val)}")
                            elif key in ("gross_margin", "operating_margin", "net_margin", "roe"):
                                lines.append(f"  {label}: {float(val):.1f}%")
                            else:
                                lines.append(f"  {label}: {float(val):+.1f}%")
                    lines.append("  Source: Yahoo Finance — typically more current than Finnhub free tier")
                    continue

                # ── Default: title + brief data summary (500 chars max) ───────
                data_summary = str(data)[:500] if data else ""
                header = title or data_summary[:120] or ev_id
                lines.append(f"\n[{ev_id}] {header}")
                if data and not title:
                    pass  # title already contains data summary
                elif data and data_summary and len(data_summary) > len(title or ""):
                    lines.append(f"  {data_summary[:400]}")

        return "\n".join(lines) if lines else "No evidence provided."

    def _call_llm(
        self, prompt: str, grammar: str, temperature: float, timeout: float = 120.0
    ) -> str:
        """Dispatch to the active LLM backend (local or API).

        Reads the active backend from model_registry.json.
        Dispatches by structured_output field:
          - "gbnf" -> local llama-server (HTTP, with grammar)
          - "json_schema" -> OpenAI-compatible API (OpenAI, OpenRouter, etc.)
          - "tool_use" -> Anthropic Messages API
        This is extensible: add a new backend to model_registry.json with the
        matching structured_output type — no code changes needed.
        """
        backend_name, backend = self._get_active_backend()
        structured_output = backend.get("structured_output", "")

        if structured_output == "gbnf":
            return self._call_llm_local(prompt, grammar, temperature, timeout, backend)
        elif structured_output == "json_schema":
            return self._call_llm_openai(prompt, temperature, timeout, backend)
        elif structured_output == "tool_use":
            return self._call_llm_anthropic(prompt, temperature, timeout, backend)
        else:
            raise ValueError(
                f"Unknown structured_output type '{structured_output}' "
                f"for backend '{backend_name}'"
            )

    def _get_active_backend(self) -> tuple[str, dict]:
        """Read active backend from model_registry.json.

        Returns:
            Tuple of (backend_name, backend_config_dict).
        """
        try:
            from pmacs.config import load_config
            config = load_config()
            registry = config.model_registry
            active = registry.active or "llama_server"
            backend = registry.backends.get(active)
            if backend is None:
                return ("llama_server", {"url": "http://127.0.0.1:8080"})
            return (active, {
                "url": backend.url or "http://127.0.0.1:8080",
                "default_model": backend.default_model,
                "structured_output": backend.structured_output,
                "api_key_ref": getattr(backend, "api_key_ref", ""),
                "base_url": getattr(backend, "base_url", ""),
            })
        except Exception:
            return ("llama_server", {"url": "http://127.0.0.1:8080"})

    def _get_api_key(self, api_key_ref: str) -> str:
        """Retrieve API key from system keyring.

        Tries the full ref first (e.g., pmacs.credentials.openrouter_api_key),
        then falls back to the short name (e.g., openrouter_key) for keys
        saved by the wizard.
        """
        if not api_key_ref:
            return ""
        try:
            import keyring
            key = keyring.get_password("pmacs.credentials", api_key_ref)
            if key:
                return key
            # Fallback: try short name (wizard saves as "{provider}_key")
            # e.g., "pmacs.credentials.openrouter_api_key" -> "openrouter_key"
            short_name = api_key_ref.split(".")[-1].replace("_api_key", "_key")
            key = keyring.get_password("pmacs.credentials", short_name)
            return key or ""
        except Exception:
            return ""

    def _call_llm_local(
        self, prompt: str, grammar: str, temperature: float, timeout: float, backend: dict
    ) -> str:
        """Call llama-server or Ollama HTTP API.

        Args:
            prompt: The full prompt text.
            grammar: GBNF grammar string (empty string = no grammar).
            temperature: Sampling temperature.
            timeout: HTTP timeout in seconds.
            backend: Backend config dict with 'url'.

        Returns:
            The 'content' field from the server response.
        """
        url = backend.get("url", _DEFAULT_LOCAL_URL)
        if not url.endswith("/completion"):
            url = url.rstrip("/") + "/completion"

        body: dict[str, Any] = {
            "prompt": prompt,
            "temperature": temperature,
            "n_predict": self.max_tokens,
        }
        if grammar:
            body["grammar"] = grammar
        else:
            from pmacs.logsys import log_debug
            log_debug(
                "LLM_GRAMMAR_MISSING",
                payload={"persona": self.persona_name},
                level="WARN",
                error_code="GRAMMAR_NOT_FOUND",
                msg=f"No grammar for {self.persona_name} — output will be unconstrained, Pydantic parsing likely to fail",
            )

        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
            data = response.json()

        # Capture usage for billing (Phase 16 side-channel)
        timings = data.get("timings", {})
        self._last_call_usage = {
            "prompt_tokens": timings.get("prompt_n", 0),
            "completion_tokens": timings.get("predicted_n", 0),
            "generation_id": None,  # Local calls have no generation_id
        }

        return data.get("content", "")

    def _call_llm_anthropic(
        self, prompt: str, temperature: float, timeout: float, backend: dict,
    ) -> str:
        """Call Anthropic Messages API.

        Args:
            prompt: The full prompt text.
            temperature: Sampling temperature.
            timeout: HTTP timeout in seconds.
            backend: Backend config dict with 'default_model', 'api_key_ref', 'base_url'.

        Returns:
            The text content from the API response.
        """
        api_key = self._get_api_key(backend.get("api_key_ref", ""))
        if not api_key:
            raise ConnectionError("Anthropic API key not found in keyring")

        model = backend.get("default_model", "claude-sonnet-4-20250514")
        base_url = backend.get("base_url", "").rstrip("/") or "https://api.anthropic.com"

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": temperature,
        }

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{base_url}/v1/messages",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        # Capture usage for billing (Phase 16 side-channel)
        usage = data.get("usage", {})
        self._last_call_usage = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "generation_id": data.get("id", ""),
        }

        # Extract text from content blocks
        content_blocks = data.get("content", [])
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        return "\n".join(texts)

    def _call_llm_openai(
        self, prompt: str, temperature: float, timeout: float, backend: dict,
    ) -> str:
        """Call OpenAI Chat Completions API.

        Args:
            prompt: The full prompt text.
            temperature: Sampling temperature.
            timeout: HTTP timeout in seconds.
            backend: Backend config dict with 'default_model', 'api_key_ref', 'base_url'.

        Returns:
            The content field from the API response.
        """
        api_key_ref = backend.get("api_key_ref", "")
        api_key = self._get_api_key(api_key_ref) if api_key_ref else ""
        if api_key_ref and not api_key:
            raise ConnectionError(f"API key not found in keyring for ref: {api_key_ref}")

        model = backend.get("default_model", "gpt-4o")
        base_url = backend.get("base_url", "").rstrip("/") or "https://api.openai.com/v1"

        extra_params = dict(backend.get("extra_params") or {})
        # max_tokens_multiplier: accounts for thinking-mode overhead (e.g. Qwen3.6 on Ollama)
        token_multiplier = extra_params.pop("max_tokens_multiplier", 1)
        effective_max_tokens = self.max_tokens * int(token_multiplier)
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": effective_max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        # Merge remaining backend-specific extra params
        if extra_params:
            body.update(extra_params)

        headers: dict[str, str] = {"content-type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        # Capture usage for billing (Phase 16 side-channel)
        usage = data.get("usage", {})
        self._last_call_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "generation_id": data.get("id", ""),
        }

        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "") or ""
            # Qwen3.6 thinking mode: JSON lands in reasoning when content is empty
            if not content.strip():
                content = msg.get("reasoning", "") or ""
            return content
        return ""

    def _audit_llm_call(
        self,
        prompt: str,
        output: str,
        model_hash: str,
        grammar_version: str,
        retry_count: int,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Write an audit event for the LLM call."""
        if self._audit is None:
            return

        self._audit.append(
            event_type="LLM_CALL",
            payload={
                "persona": self.persona_name,
                "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "output_hash": hashlib.sha256(output.encode()).hexdigest()[:16],
                "model_hash": model_hash,
                "grammar_version": grammar_version,
                "retry_count": retry_count,
                "latency_ms": round(latency_ms, 1),
                "success": success,
                **(
                    {
                        "prompt_tokens": self._last_call_usage.get("prompt_tokens", 0),
                        "completion_tokens": self._last_call_usage.get("completion_tokens", 0),
                        "generation_id": self._last_call_usage.get("generation_id"),
                    }
                    if self._last_call_usage else {}
                ),
            },
            cycle_id=self.cycle_id,
        )

    def _check_preflight_budget(self, prompt: str) -> None:
        """Pre-flight budget estimate. Raises RuntimeError if budget exceeded."""
        if not self._billing_ctx:
            return
        try:
            from pmacs.billing.token_estimator import estimate_call_cost
            from pmacs.billing.budget_enforcer import enforce_budgets
            from pmacs.billing.pricing import get_pricing

            sqlite_conn = self._billing_ctx["sqlite_conn"]
            model_id = self._billing_ctx.get("model_id", "")
            pricing = get_pricing(sqlite_conn, model_id)
            estimated = estimate_call_cost(prompt, self.persona_name, pricing)
            self._cycle_cumulative_estimated += estimated.estimated_cost_usd

            result = enforce_budgets(sqlite_conn, estimated.estimated_cost_usd)
            if not result.allowed:
                raise RuntimeError(
                    f"Budget blocked: {result.reason}"
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # Billing failure must not block the cycle

    def _record_call_billing(self, prompt: str, latency_ms: float) -> None:
        """Post-call billing: compute body cost, log usage, check runaway."""
        if not self._billing_ctx or not self._last_call_usage:
            return
        try:
            from pmacs.billing.cost_calculator import compute_body_cost
            from pmacs.billing.token_estimator import estimate_call_cost
            from pmacs.billing.usage_logger import log_usage
            from pmacs.billing.budget_enforcer import check_runaway
            from pmacs.billing.pricing import get_pricing
            from pmacs.billing.reconciler import spawn_reconcile_call
            from pmacs.schemas.billing import BodyCost, EstimatedCost

            sqlite_conn = self._billing_ctx["sqlite_conn"]
            duckdb_adapter = self._billing_ctx["duckdb_adapter"]
            model_id = self._billing_ctx.get("model_id", "")
            pricing = get_pricing(sqlite_conn, model_id)

            usage = self._last_call_usage
            estimated = estimate_call_cost(prompt, self.persona_name, pricing)
            body_cost_usd = compute_body_cost(
                {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]},
                pricing,
            )

            call_record = BodyCost(
                call_id=hashlib.sha256(f"{self.cycle_id}:{self.persona_name}:{time.monotonic()}".encode()).hexdigest()[:16],
                cycle_id=self.cycle_id,
                persona=self.persona_name,
                model_id=model_id,
                generation_id=usage.get("generation_id"),
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                body_cost_usd=body_cost_usd,
                latency_ms=int(latency_ms),
            )

            log_usage(sqlite_conn, duckdb_adapter, call_record, estimated)

            # Mid-cycle runaway check
            self._cycle_cumulative_actual += body_cost_usd
            runaway = check_runaway(self._cycle_cumulative_actual, self._cycle_cumulative_estimated)
            if not runaway.allowed:
                from pmacs.nervous.sse_publisher import publish_system_event
                publish_system_event("cost.runaway_detected", {
                    "persona": self.persona_name,
                    "cycle_id": self.cycle_id,
                    "actual": round(self._cycle_cumulative_actual, 6),
                    "estimated": round(self._cycle_cumulative_estimated, 6),
                })

            # Spawn reconciliation for cloud calls (has generation_id)
            gen_id = usage.get("generation_id")
            if gen_id:
                spawn_reconcile_call(
                    call_record.call_id,
                    gen_id,
                    self._billing_ctx.get("sqlite_path", ""),
                    self._billing_ctx.get("duckdb_path", ""),
                )
        except Exception:
            pass  # Billing failure must not block the cycle

    def _get_model_hash(self) -> str:
        """Get the configured model hash from config, or empty string.

        Derives the hash key from gguf_path (filename without .gguf extension)
        to look up the correct hash in model_hashes.
        """
        try:
            from pmacs.config import load_config
            config = load_config()
            # Derive model name from gguf_path: strip path and .gguf suffix
            gguf_path = config.resources.gguf_path
            if gguf_path:
                model_name = Path(gguf_path).stem
                return config.model_hashes.get(model_name, "")
            # Fallback: single-model config
            if len(config.model_hashes) == 1:
                return next(iter(config.model_hashes.values()))
        except (ImportError, AttributeError):
            pass
        return ""

    def _get_persona_enum(self):
        """Convert persona_name string to PersonaName enum."""
        from pmacs.schemas.agents import PersonaName
        try:
            return PersonaName(self.persona_name)
        except ValueError:
            return PersonaName.GATEKEEPER

    def _extract_ticker(self, evidence: list[EvidencePacket]) -> str:
        """Extract ticker from first evidence packet, or empty string."""
        if evidence:
            return evidence[0].ticker
        return ""

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON object from raw LLM output.

        Handles cases where the model wraps JSON in markdown code blocks
        or surrounding text.
        """
        text = raw.strip()
        # Try to find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text
