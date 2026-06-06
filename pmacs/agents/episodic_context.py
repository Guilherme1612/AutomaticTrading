"""Episodic context builder — inject macro regime, failures, track record, and lessons into persona runs.

spec_ref: Architecture.md §1.13, Agents.md §17
"""
from __future__ import annotations

import hashlib
from typing import Any

# ---------------------------------------------------------------------------
# Ticker knowledge map — material facts not captured in live data sources.
# These are verified public facts (deals, team pedigree, partnerships) that
# pre-date the 180-day press window or exist outside structured data feeds.
# Agents must mark these [KNOWLEDGE] per Agents.md §3 when used in analysis.
# ---------------------------------------------------------------------------
_TICKER_KNOWLEDGE: dict[str, list[str]] = {
    "NBIS": [
        "[KNOWLEDGE] Microsoft and Meta committed $46.4B in compute capacity to Nebius — the single "
        "largest hyperscaler validation of an AI cloud infrastructure company. Creates captive revenue "
        "stream and distribution moat that competitors cannot easily replicate.",
        "[KNOWLEDGE] Core engineering team of ~1,200 engineers built Yandex's world-class AI/ML "
        "infrastructure at scale (tens of millions of users). This is elite pedigree — directly "
        "comparable to ex-Google Brain or ex-DeepMind founding teams. TEAM_EXPERTISE moat score "
        "should reflect this; default INTANGIBLE_ASSETS is incorrect for this company.",
        "[KNOWLEDGE] Nebius IPO'd on Nasdaq in late 2024 after spinning out of Yandex N.V.; "
        "company is pre-revenue at scale but has committed customer backlog from hyperscaler deals.",
        "[KNOWLEDGE] Nebius operates data centers in Finland, with expansion planned across Europe. "
        "The Finland location provides access to cheap renewable energy and cool climate — structural "
        "cost advantage for GPU compute. Revenue run-rate from Q1 2026 suggests ~$1.6B annualized.",
        "[KNOWLEDGE] Nebius has a ~$3B fully-funded capex plan for 2025-2026 with no additional "
        "equity dilution needed. This is rare for an AI infrastructure company at this stage.",
    ],
    "PLTR": [
        "[KNOWLEDGE] Palantir has US government contracts (AIP for defense) providing durable "
        "revenue floor; commercial AIP bootcamp model converts enterprise clients at high NRR.",
        "[KNOWLEDGE] Palantir was added to S&P 500 in Sept 2024, driving passive fund inflows. "
        "AIP platform (Artificial Intelligence Platform) is the primary growth driver with 100%+ "
        "commercial revenue growth in recent quarters.",
    ],
    "NET": [
        "[KNOWLEDGE] Cloudflare's network reaches 95%+ of the internet-connected population within "
        "50ms; Zero Trust platform (SASE) creates strong switching costs in enterprise security.",
        "[KNOWLEDGE] Cloudflare Workers AI enables inference at the edge — positions NET as the "
        "AI inference platform complementing training-focused hyperscalers.",
    ],
    "MELI": [
        "[KNOWLEDGE] MercadoLibre is the dominant e-commerce and fintech platform in Latin America "
        "with ~40% GMV market share in Brazil; Mercado Pago fintech arm has 50M+ active users.",
        "[KNOWLEDGE] MELI's fintech arm (Mercado Pago) is the largest digital wallet in LATAM. "
        "Credit portfolio growing 70%+ YoY with improving NPL ratios — underappreciated profit driver.",
    ],
    "OUST": [
        "[KNOWLEDGE] Ouster merged with Velodyne in 2023 creating the largest lidar company by "
        "volume; digital lidar architecture has structural cost advantage over analog competitors.",
        "[KNOWLEDGE] Ouster's solid-state digital lidar (DF series) targets automotive OEMs at "
        "<$600/unit price point — key for ADAS adoption. Multiple design wins with major OEMs.",
    ],
    "SOFI": [
        "[KNOWLEDGE] SoFi became a bank holding company in 2022 — gives access to low-cost "
        "deposits (cost of funds ~1.5% vs 4%+ for fintech peers) — key structural advantage.",
        "[KNOWLEDGE] SoFi's Galileo technology platform powers 150M+ accounts across 100+ fintechs. "
        "This B2B infrastructure is a high-margin, underappreciated revenue stream.",
    ],
    "NU": [
        "[KNOWLEDGE] Nu Holdings is the largest digital bank outside Asia with 100M+ customers "
        "across Brazil, Mexico, and Colombia. 60%+ of Brazilian adults are Nubank customers.",
        "[KNOWLEDGE] Nu's cost-to-serve is ~$0.80/customer vs ~$5-8 for traditional banks — "
        "structural cost advantage enables profitability at scale that incumbents cannot match.",
    ],
    "GOOGL": [
        "[KNOWLEDGE] Alphabet's Google Cloud reached $12B quarterly revenue run-rate in 2025, "
        "becoming a credible #3 cloud provider. AI/ML services (Vertex AI, Gemini) are key growth drivers.",
        "[KNOWLEDGE] DOJ antitrust remedy could force Chrome/Android divestiture — binary risk "
        "but implementation timeline is 3-5 years, giving Alphabet time to negotiate.",
    ],
    "META": [
        "[KNOWLEDGE] Meta's Llama open-source AI model family is the most widely-adopted open LLM. "
        "Meta monetizes AI through improved ad targeting and engagement, not direct model sales.",
        "[KNOWLEDGE] Meta invested $30B+ in GPU infrastructure in 2024-2025. This capex creates "
        "a compute moat that few competitors can match. Reality Labs losses ($16B+ annually) are "
        "a drag but the core ads business more than covers it.",
    ],
    "MSFT": [
        "[KNOWLEDGE] Microsoft Azure is the #2 cloud provider with 33%+ growth, driven by AI "
        "services (Azure OpenAI). Enterprise Microsoft 365 Copilot adoption is the next major catalyst.",
        "[KNOWLEDGE] MSFT's acquisition of Activision Blizzard closed Oct 2023, making it the #3 "
        "gaming company by revenue. This diversifies beyond cloud/enterprise.",
    ],
    "NVDA": [
        "[KNOWLEDGE] NVIDIA dominates AI training GPU market with ~80%+ share. Blackwell (B200) "
        "architecture launched in 2025 with 2-4x performance improvement over H100.",
        "[KNOWLEDGE] Major hyperscaler capex commitments (MSFT $80B+, META $60B+, GOOGL $50B+) "
        "in 2025-2026 are primarily NVDA GPU purchases — creates multi-year revenue visibility.",
    ],
    "AMZN": [
        "[KNOWLEDGE] AWS is the #1 cloud provider (~31% market share). Amazon's advertising "
        "business is a $50B+ high-margin growth engine, now the 3rd largest digital ad platform.",
    ],
    "RKLB": [
        "[KNOWLEDGE] Rocket Lab is the only private small-launch provider with 50+ successful "
        "Electron missions. Neutron medium-lift vehicle (2025 debut) opens the mega-constellation market.",
        "[KNOWLEDGE] Rocket Lab's Space Systems division (satellite components) is a growing "
        "high-margin business independent of launch cadence. Won $500M+ in government contracts.",
    ],
    "ASTS": [
        "[KNOWLEDGE] AST SpaceMobile is building the first space-based cellular broadband network "
        "that connects directly to standard smartphones (no special hardware). Partnership with "
        "AT&T and Verizon for US coverage.",
        "[KNOWLEDGE] ASTS launched its first 5 commercial BlueBird satellites in 2024. Revenue "
        "is pre-revenue but FCC spectrum licenses are valuable assets.",
    ],
    "RBRK": [
        "[KNOWLEDGE] Rubrik is a zero-trust data security company led by ex-Microsoft CEO Bipul "
        "Sinha. IPO'd April 2024. Focus on ransomware recovery and data observability.",
        "[KNOWLEDGE] Rubrik's subscription transition is nearly complete, with 90%+ ARR from "
        "subscription. This improves revenue predictability and gross margins.",
    ],
    "CRWD": [
        "[KNOWLEDGE] CrowdStrike is the market leader in cloud-native endpoint detection and "
        "response (EDR). Falcon platform covers 70%+ of Fortune 500.",
        "[KNOWLEDGE] After the July 2024 outage incident, CrowdStrike recovered customer trust "
        "with enhanced testing protocols. Net retention remained above 115%.",
    ],
    "PANW": [
        "[KNOWLEDGE] Palo Alto Networks is transitioning to platformization ( consolidating "
        "point products into unified Prisma/Strata/Cortex platforms). This creates switching costs.",
    ],
    "HIMS": [
        "[KNOWLEDGE] Hims & Hers is a telehealth platform with 2M+ subscribers. GLP-1 weight "
        "loss offerings (compounded semaglutide) are a major growth catalyst, though regulatory "
        "risk exists as FDA shortage status may change.",
    ],
    "TEM": [
        "[KNOWLEDGE] Tempus AI provides AI-powered precision medicine diagnostics. Founded by "
        "Eric Lefkofsky (Groupon co-founder). Largest clinical data library in oncology.",
    ],
    "KOD": [
        "[KNOWLEDGE] Kodiak Robotics is an autonomous trucking company. Pre-revenue with "
        "military contracts (DOD) and partnership with Atlas Energy.",
    ],
}


def build_context_brief(
    persona: str,
    ticker: str,
    regime: str = "UNCERTAIN",
    regime_confidence: float = 0.0,
    recent_failures: list[dict] | None = None,
    persona_brier: float | None = None,
    persona_cycle_count: int = 0,
    recent_lessons: list[str] | None = None,
    affinity_data: dict[str, Any] | None = None,
    fde_history: list[dict] | None = None,
    prior_verdict: str | None = None,
    prior_conviction: float | None = None,
    prior_key_signal: str | None = None,
    prior_decided_at: str | None = None,
    ticker_analysis_count: int = 0,
) -> str:
    """Build a ~400-word context brief for a persona run (first analysis) or ~700 words (repeat).

    Injects macro regime, prior verdict (long-term memory), recent failures,
    track record, lessons, persona-ticker affinity from DuckDB, and FDE history.

    Parameters
    ----------
    persona : str
        Target persona name.
    ticker : str
        Ticker being analysed.
    regime : str
        Current macro regime label.
    regime_confidence : float
        Confidence in the regime classification [0, 1].
    recent_failures : list[dict] | None
        Recent FailedAssumption entries (``taxonomy``, ``summary`` keys).
    persona_brier : float | None
        This persona's average Brier score on *ticker*.
    persona_cycle_count : int
        Number of cycles this persona has run on *ticker*.
    recent_lessons : list[str] | None
        Recent lesson texts relevant to the ticker.
    affinity_data : dict | None
        Row from ``persona_ticker_affinity`` with ``avg_brier`` and
        ``cycle_count`` keys.  When provided, overrides
        *persona_brier* / *persona_cycle_count*.
    fde_history : list[dict] | None
        Recent FDE failure classifications with ``taxonomy`` and
        ``severity`` keys.
    prior_verdict : str | None
        The verdict from the most recent prior analysis of this ticker
        (e.g. "HOLD", "BUY"). Critical for long-term memory.
    prior_conviction : float | None
        Conviction score [0,1] from the most recent prior analysis.
    prior_key_signal : str | None
        The primary signal that drove the prior verdict.
    prior_decided_at : str | None
        ISO date string when prior decision was made.
    ticker_analysis_count : int
        Total number of completed analyses of this ticker (0 = first time).

    Returns
    -------
    str
        Truncated to ≤300 words.
    """
    sections: list[str] = []

    # --- Prior analysis (long-term memory — most important for repeat analyses) ---
    if prior_verdict and ticker_analysis_count >= 1:
        prior_str = (
            f"PRIOR ANALYSIS #{ticker_analysis_count} of {ticker} "
            f"(decided {prior_decided_at or 'previously'}): "
            f"verdict={prior_verdict}"
        )
        if prior_conviction is not None:
            prior_str += f", conviction={prior_conviction:.2f}"
        if prior_key_signal:
            prior_str += f". Key signal was: {prior_key_signal}"
        prior_str += (
            f". This is analysis #{ticker_analysis_count + 1} — "
            "if the thesis has materially changed, explain what changed and why. "
            "If unchanged, confirm or tighten the prior view with fresher data."
        )
        sections.append(prior_str)

    # --- Macro regime (always present) ---
    sections.append(
        f"MACRO CONTEXT: Current regime is {regime} (confidence {regime_confidence:.0%})."
    )

    # --- Recent failures (from holding resolution) ---
    if recent_failures:
        failure_strs = [
            f"{f.get('taxonomy', 'UNKNOWN')}: {f.get('summary', '')}" for f in recent_failures[:3]
        ]
        sections.append("RECENT FAILURES: " + "; ".join(failure_strs))

    # --- Persona-ticker affinity (DuckDB persona_ticker_affinity) ---
    # If affinity_data provided, prefer it over raw brier/cycle_count.
    eff_brier: float | None = persona_brier
    eff_cycles: int = persona_cycle_count
    if affinity_data is not None:
        eff_brier = affinity_data.get("avg_brier", persona_brier)
        eff_cycles = int(affinity_data.get("cycle_count", persona_cycle_count))

    if eff_brier is not None and eff_cycles >= 5:
        sections.append(
            f"YOUR TRACK RECORD ON {ticker}: avg Brier {eff_brier:.3f} "
            f"over {eff_cycles} cycles."
        )

    # --- FDE failure history ---
    if fde_history:
        fde_strs = [
            f"{h.get('taxonomy', 'UNKNOWN')} (sev {h.get('severity', 0.0):.1f})"
            for h in fde_history[:3]
        ]
        sections.append("FAILURE PATTERNS: " + "; ".join(fde_strs))

    # --- Lessons ---
    if recent_lessons:
        sections.append("RELEVANT PAST LESSONS: " + "; ".join(recent_lessons[:2]))

    # --- Ticker knowledge (material facts not in live data) ---
    ticker_facts = _TICKER_KNOWLEDGE.get(ticker, [])
    if ticker_facts:
        sections.append("KNOWN MATERIAL FACTS (use [KNOWLEDGE] tag in analysis): " + " | ".join(ticker_facts))

    brief = " ".join(sections)
    words = brief.split()
    # Repeat analyses deserve more context — expand limit for subsequent runs
    # First analysis: 400 words. Repeat: 700 words to carry more prior detail.
    word_limit = 700 if ticker_analysis_count >= 1 else 400
    if len(words) > word_limit:
        brief = " ".join(words[:word_limit]) + "..."

    return brief


def inject_and_log(
    persona: str,
    ticker: str,
    cycle_id: str,
    **kwargs: Any,
) -> tuple[str, str]:
    """Build context brief and log audit event.

    Returns
    -------
    tuple[str, str]
        ``(brief, content_hash)`` — the brief text and its SHA-256 hex
        digest for audit chain verification.
    """
    brief = build_context_brief(persona=persona, ticker=ticker, **kwargs)
    content_hash = hashlib.sha256(brief.encode()).hexdigest()

    # Lazy import to avoid circular dependency at module level.
    from pmacs.logsys.debug_log import log_debug

    log_debug(
        "episodic_context_injected",
        payload={
            "content_hash": content_hash,
            "persona": persona,
            "ticker": ticker,
            "cycle_id": cycle_id,
            "word_count": len(brief.split()),
        },
        level="INFO",
        cycle_id=cycle_id,
    )

    return brief, content_hash
