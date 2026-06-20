"""Persona-specific output schemas.

Schemas for all 7 analysis personas + Crucible + MemoWriter:
  - MacroRegime, CatalystSummarizer, MoatAnalyst (personas 1-3)
  - GrowthHunter, InsiderActivity, ShortInterest, Forensics (personas 4-7)
  - Crucible (adversarial attacker)
  - MemoWriter (operator-facing memo synthesis)

Wave-2 debate + audit personas (Agents.md §11b-§11d):
  - BullAdvocate, BearAdvocate (emit DirectionalProbability; argue against consensus)
  - CrossPersonaAuditor (emits AuditorFlags; never probabilities)

Each persona produces a structured output parsed through Pydantic v2.
All models include probability-sum validators and persona-specific invariants.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pmacs.schemas.agents import PersonaName
from pmacs.schemas.failure import AUDITOR_ALLOWED_TAXONOMY, FailureTaxonomy


def _coerce_optional_float(v: object) -> float | None:
    """Coerce LLM string values like 'DATA NOT AVAILABLE' to None for optional floats."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _snap_to_grid(v: float, grid: float = 0.05) -> float:
    """Snap a probability to the nearest 0.05 grid point for determinism."""
    return round(round(v / grid) * grid, 2)


def _normalize_probs(p_up: float, p_flat: float, p_down: float) -> tuple[float, float, float]:
    """Normalize three probabilities to sum exactly to 1.0 after grid-snapping."""
    total = p_up + p_flat + p_down
    if total == 0:
        return (0.35, 0.35, 0.30)
    return (round(p_up / total, 2), round(p_flat / total, 2), round(p_down / total, 2))


class MacroRegimeOutput(BaseModel):
    """MacroRegime persona output — macro regime classification.

    spec_ref: Agents.md §4
    """

    model_config = ConfigDict(frozen=True)

    regime: Literal[
        "EXPANSION", "LATE_CYCLE", "CONTRACTION", "RECOVERY",
        "REGIME_SHIFT", "UNCERTAIN",
    ]
    regime_confidence: float = Field(ge=0.0, le=1.0)
    regime_reasoning: str = Field(max_length=800)
    yield_curve_signal: Literal["NORMAL", "FLAT", "INVERTED"]
    vix_regime: Literal["LOW", "MODERATE", "ELEVATED", "CRISIS"]
    sector_rotation_summary: str = Field(max_length=600)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> MacroRegimeOutput:
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:  # Reject wildly broken distributions
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


class CatalystEntry(BaseModel):
    """Single catalyst within a CatalystSummarizer output."""

    model_config = ConfigDict(frozen=True)

    catalyst_type: Literal[
        "earnings", "fda_decision", "product_launch", "regulatory_ruling",
        "ma_close", "partnership", "guidance_update",
    ]
    description: str = Field(max_length=400)
    expected_date: str | None = None
    status: Literal["PENDING", "RESOLVED_UP", "RESOLVED_DOWN", "RESOLVED_FLAT", "RESOLVED_MIXED"]
    thesis_impact: Literal[
        "STRONGLY_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "STRONGLY_NEGATIVE",
    ]
    evidence_ids: list[str] = Field(min_length=1)


class CatalystSummarizerOutput(BaseModel):
    """CatalystSummarizer persona output — catalyst inventory and assessment.

    spec_ref: Agents.md §6
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    catalysts: list[CatalystEntry] = Field(max_length=10)
    net_catalyst_outlook: str = Field(max_length=600)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> CatalystSummarizerOutput:
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


class MoatComponent(BaseModel):
    """Single moat component within a MoatAnalyst output."""

    model_config = ConfigDict(frozen=True)

    moat_type: Literal[
        "NETWORK_EFFECTS", "SWITCHING_COSTS", "INTANGIBLE_ASSETS",
        "COST_ADVANTAGE", "EFFICIENT_SCALE", "DATA_ADVANTAGE",
    ]
    strength: float = Field(ge=0.0, le=1.0)
    trajectory: Literal["WIDENING", "STABLE", "NARROWING"]
    reasoning: str = Field(max_length=600)
    evidence_ids: list[str] = Field(min_length=1)


class MoatAnalystOutput(BaseModel):
    """MoatAnalyst persona output — competitive moat assessment.

    spec_ref: Agents.md §7
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    moat_components: list[MoatComponent] = Field(min_length=1, max_length=6)
    moat_strength: float = Field(ge=0.0, le=1.0)
    competitive_entry_risk: Literal["LOW", "MODERATE", "HIGH"]
    competitive_entry_reasoning: str = Field(max_length=500)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_invariants(self) -> MoatAnalystOutput:
        # Probability sum
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)

        # moat_strength should be consistent with component average
        if self.moat_components:
            avg = sum(c.strength for c in self.moat_components) / len(self.moat_components)
            if abs(self.moat_strength - avg) > 0.15:
                raise ValueError(
                    f"moat_strength ({self.moat_strength}) is more than 0.15 from "
                    f"component average ({avg:.3f})"
                )

        # High competitive entry risk implies lower moat strength
        if self.competitive_entry_risk == "HIGH" and self.moat_strength >= 0.7:
            raise ValueError(
                f"competitive_entry_risk HIGH but moat_strength {self.moat_strength} >= 0.7"
            )

        # No duplicate moat types
        types = [c.moat_type for c in self.moat_components]
        if len(types) != len(set(types)):
            raise ValueError("duplicate moat_type found in moat_components")

        return self


# ---------------------------------------------------------------------------
# Persona 4: GrowthHunter
# ---------------------------------------------------------------------------

class GrowthHunterOutput(BaseModel):
    """Growth equity analysis output.

    Assess revenue trajectory, TAM penetration, unit economics, and
    growth durability for a given ticker.

    spec_ref: Agents.md §8
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    revenue_yoy_pct: float | None = None
    revenue_acceleration: Literal["ACCELERATING", "STABLE", "DECELERATING", "UNKNOWN"]
    gross_margin_pct: float | None = None
    gross_margin_trend: Literal["EXPANDING", "STABLE", "CONTRACTING", "UNKNOWN"]
    tam_penetration_pct: float | None = None
    growth_durability: Literal["HIGH", "MODERATE", "LOW", "UNKNOWN"]
    growth_durability_reasoning: str = Field(max_length=600)
    key_risk_to_growth: str = Field(max_length=500)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("revenue_yoy_pct", "gross_margin_pct", "tam_penetration_pct", mode="before")
    @classmethod
    def _coerce_floats(cls, v: object) -> float | None:
        return _coerce_optional_float(v)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "GrowthHunterOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


# ---------------------------------------------------------------------------
# Persona 5: InsiderActivity
# ---------------------------------------------------------------------------

class InsiderTransaction(BaseModel):
    """Single insider transaction from Form 4 filing."""

    model_config = ConfigDict(frozen=True)

    insider_name: str
    insider_role: str
    transaction_type: Literal[
        "OPEN_MARKET_BUY", "OPEN_MARKET_SELL", "OPTION_EXERCISE",
        "10B5_1_SELL", "GIFT", "OTHER",
    ]
    amount_usd: float
    shares: int
    date: str  # ISO date
    evidence_id: str


class InsiderActivityOutput(BaseModel):
    """Insider activity analysis output.

    Detect meaningful insider trading patterns from Form 4 filings.

    spec_ref: Agents.md §9
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    transactions: list[InsiderTransaction]
    signal: Literal[
        "CLUSTER_BUY", "CLUSTER_SELL", "LARGE_BUY", "LARGE_SELL",
        "CEO_BUY", "ROUTINE", "NO_SIGNAL", "INSUFFICIENT_DATA",
    ]
    signal_reasoning: str = Field(max_length=600)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "InsiderActivityOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


# ---------------------------------------------------------------------------
# Persona 6: ShortInterest
# ---------------------------------------------------------------------------

class ShortInterestOutput(BaseModel):
    """Short interest analysis output.

    Detect short interest anomalies: spikes, sustained high levels,
    and changes in short positioning.

    spec_ref: Agents.md §10
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    short_pct_float: float | None = None
    days_to_cover: float | None = None
    short_change_pct: float | None = None
    anomaly: Literal["SPIKE_UP", "SPIKE_DOWN", "HIGH_SUSTAINED", "NORMAL", "INSUFFICIENT_DATA"]
    anomaly_reasoning: str = Field(max_length=600)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("short_pct_float", "days_to_cover", "short_change_pct", mode="before")
    @classmethod
    def _coerce_floats(cls, v: object) -> float | None:
        return _coerce_optional_float(v)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "ShortInterestOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


# ---------------------------------------------------------------------------
# Persona 7: Forensics
# ---------------------------------------------------------------------------

class RedFlag(BaseModel):
    """Single accounting/forensic red flag."""

    model_config = ConfigDict(frozen=True)

    category: Literal[
        "REVENUE_QUALITY", "EARNINGS_QUALITY", "CASH_FLOW_DIVERGENCE",
        "RELATED_PARTY", "AUDITOR_FLAGS", "DSO_DPO_ANOMALY",
        "MARGIN_ANOMALY", "GOODWILL_RISK",
    ]
    severity: float = Field(ge=0.0, le=1.0)
    description: str = Field(max_length=600)
    evidence_ids: list[str] = Field(min_length=1)


class ForensicsOutput(BaseModel):
    """Forensic accounting analysis output.

    Detect red flags in financial statements: revenue quality,
    earnings manipulation, cash flow divergence, and more.

    spec_ref: Agents.md §11
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    red_flags: list[RedFlag] = Field(max_length=8)
    red_flag_count: int
    overall_accounting_quality: Literal[
        "CLEAN", "MINOR_CONCERNS", "MATERIAL_CONCERNS",
        "SEVERE_RISK", "INSUFFICIENT_DATA",
    ]
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ForensicsOutput":
        # Probability sum check
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        # red_flag_count must match len(red_flags)
        if self.red_flag_count != len(self.red_flags):
            raise ValueError(
                f"red_flag_count ({self.red_flag_count}) != "
                f"len(red_flags) ({len(self.red_flags)})"
            )
        return self


# ---------------------------------------------------------------------------
# Crucible (Adversarial Attacker)
# ---------------------------------------------------------------------------

class CrucibleAttack(BaseModel):
    """Single adversarial attack against an investment thesis.

    spec_ref: Agents.md §14
    """

    model_config = ConfigDict(frozen=True)

    attack_type: Literal[
        "LOGICAL_HOLE", "CITATION_GAP", "COUNTERARGUMENT",
        "OVERLOOKED_RISK", "BASE_RATE_NEGLECT",
    ]
    severity: float = Field(ge=0.0, le=1.0)
    description: str = Field(max_length=800)
    evidence_ids: list[str]
    missing_evidence: str | None = None


class CrucibleOutput(BaseModel):
    """Crucible persona output — adversarial thesis attack.

    spec_ref: Agents.md §14
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    attacks: list[CrucibleAttack] = Field(max_length=10)
    attack_count: int
    severity: float = Field(ge=0.0, le=1.0)
    thesis_survives: bool
    summary: str = Field(max_length=800)
    rewrite_cycle: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def _check_severity(self) -> CrucibleOutput:
        if self.attacks:
            max_sev = max(a.severity for a in self.attacks)
            if abs(max_sev - self.severity) > 0.05:
                raise ValueError(
                    f"severity {self.severity} != max attack severity {max_sev}"
                )
        return self

    @model_validator(mode="after")
    def _check_count(self) -> CrucibleOutput:
        if self.attack_count != len(self.attacks):
            raise ValueError("attack_count must match attacks length")
        return self

    @model_validator(mode="after")
    def _check_survives(self) -> CrucibleOutput:
        if self.thesis_survives and self.severity > 0.6:
            raise ValueError("thesis_survives=True but severity > 0.6")
        if not self.thesis_survives and self.severity < 0.6:
            raise ValueError("thesis_survives=False but severity < 0.6")
        return self


# ---------------------------------------------------------------------------
# MemoWriter (Operator-Facing Memo Synthesis)
# ---------------------------------------------------------------------------

class MemoWriterOutput(BaseModel):
    """MemoWriter persona output — operator-facing investment memo.

    spec_ref: Agents.md §15

    Field names match the JSON produced by _generate_full_memo() in pipeline.py
    and stored in the memos.memo_json column.  The template (memo.html) reads
    these field names directly after json.loads().
    """

    model_config = ConfigDict(frozen=True)

    ticker: str = ""
    verdict_line: str = Field(default="", max_length=400)

    # Core thesis — field is "thesis" (NOT "thesis_summary") to match DB/template
    thesis: str = Field(default="", max_length=3000)

    # Valuation
    fair_value: float | None = None
    valuation_range: dict = Field(default_factory=dict)          # {low, base, high}
    valuation_methodology: str | None = None

    # Business deep-dive (all optional — LLM may omit on error)
    business_model: str | None = None
    financial_snapshot: dict = Field(default_factory=dict)
    industry_kpis: dict = Field(default_factory=dict)  # sector-specific KPIs extracted programmatically
    growth_drivers: list[dict] = Field(default_factory=list)
    competitive_position: dict = Field(default_factory=dict)
    risk_factors: list[dict] = Field(default_factory=list)
    catalyst_calendar: list[dict] = Field(default_factory=list)

    # Evidence / risks
    key_evidence: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)

    # Crucible
    bear_case_response: str | None = None
    crucible_attacks: list[str | dict] = Field(default_factory=list)  # str (legacy) or dict {attack/severity/...}
    crucible_severity: float | None = None
    crucible_thesis_survives: bool | None = None
    crucible_summary: str | None = None

    # Agent signals & sizing
    agent_signals: list[dict] = Field(default_factory=list)
    position_sizing_note: str | None = None

    # Probabilities (populated by _generate_full_memo via arbitration)
    p_up: float = 0.0
    p_flat: float = 0.0
    p_down: float = 0.0
    conviction: float = 0.0

    # Sizing & EV (populated by the pipeline after arbitration)
    ev_multiple: float | None = None
    sizing_usd: float | None = None

    # Dissent tracking
    dissenting_personas: list[str] = Field(default_factory=list)
    dissent_summary: str | None = None

    # ── Wave-2 enrichment (Agents.md §16.9) ─────────────────────────────────
    # All optional with defaults so the pipeline's existing construction paths
    # (memo_dict merge in pipeline.py, simulation) keep working unchanged.
    # bull_bear_debate: {bull_case, bear_case, advocate_lean, reverse_dcf_gap}
    # what_would_change_my_mind: pre-registered falsification triggers.
    # reverse_dcf / scenario_price: serialized engine results for display.
    bull_bear_debate: dict = Field(default_factory=dict)
    what_would_change_my_mind: list[str] = Field(default_factory=list)
    reverse_dcf: dict | None = None
    scenario_price: dict | None = None

    # Human-readable fallback written alongside JSON
    raw_text: str | None = None

    @field_validator("verdict_line")
    @classmethod
    def _check_verdict_prefix(cls, v: str) -> str:
        if not v:
            return v  # empty is allowed during construction; sanity validator checks
        valid = ("STRONG_BUY", "BUY", "HOLD", "SKIP")
        if not any(v.startswith(prefix) for prefix in valid):
            raise ValueError(
                f"verdict_line must start with one of {valid}, got: '{v[:50]}'"
            )
        return v


# ---------------------------------------------------------------------------
# Wave-2 debate + audit personas (Agents.md §11b-§11d)
# ---------------------------------------------------------------------------

# Wave-1 personas that advocates/auditor may target/reference. The auditor's
# flag.target_persona and the advocates' target_persona must be one of these.
WAVE1_PERSONAS: frozenset[PersonaName] = frozenset({
    PersonaName.MACRO_REGIME,
    PersonaName.CATALYST_SUMMARIZER,
    PersonaName.MOAT_ANALYST,
    PersonaName.GROWTH_HUNTER,
    PersonaName.INSIDER_ACTIVITY,
    PersonaName.SHORT_INTEREST,
    PersonaName.FORENSICS,
})


class BullAdvocateOutput(BaseModel):
    """BullAdvocate persona output — argues the bull case against consensus.

    spec_ref: Agents.md §11b
    """

    model_config = ConfigDict(frozen=True)

    ticker: str = ""
    cycle_id: str = ""
    target_persona: PersonaName  # the wave-1 persona this argument pushes against
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=600)
    strongest_bear_counterpoint: str = Field(max_length=300)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_invariants(self) -> BullAdvocateOutput:
        if self.target_persona not in WAVE1_PERSONAS:
            raise ValueError(
                f"target_persona must be a wave-1 analysis persona, got {self.target_persona}"
            )
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


class BearAdvocateOutput(BaseModel):
    """BearAdvocate persona output — argues the bear case against consensus.

    spec_ref: Agents.md §11c
    """

    model_config = ConfigDict(frozen=True)

    ticker: str = ""
    cycle_id: str = ""
    target_persona: PersonaName
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=600)
    strongest_bull_counterpoint: str = Field(max_length=300)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("p_up", "p_flat", "p_down", mode="before")
    @classmethod
    def _snap_probs(cls, v: float) -> float:
        return _snap_to_grid(v)

    @model_validator(mode="after")
    def _check_invariants(self) -> BearAdvocateOutput:
        if self.target_persona not in WAVE1_PERSONAS:
            raise ValueError(
                f"target_persona must be a wave-1 analysis persona, got {self.target_persona}"
            )
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 0.10:
            raise ValueError(f"probabilities sum to {total}")
        if abs(total - 1.0) > 1e-9:
            p_up, p_flat, p_down = _normalize_probs(self.p_up, self.p_flat, self.p_down)
            object.__setattr__(self, "p_up", p_up)
            object.__setattr__(self, "p_flat", p_flat)
            object.__setattr__(self, "p_down", p_down)
        return self


class AuditorFlag(BaseModel):
    """A single reasoning-flaw flag emitted by the CrossPersonaAuditor.

    spec_ref: Agents.md §11d.4 / §15.4
    """

    model_config = ConfigDict(frozen=True)

    flag_type: Literal[
        "CITATION_GAP",
        "CONCLUSION_UNSUPPORTED",
        "CONFLICTING_CONCLUSIONS",
        "NUMBER_MISUSE",
        "HALLUCINATED_EVIDENCE",
    ]
    target_persona: PersonaName  # the wave-1 persona the flag applies to
    severity: float = Field(ge=0.0, le=1.0)
    description: str = Field(max_length=400)
    evidence_ids: list[str] = Field(default_factory=list)
    taxonomy_mapping: FailureTaxonomy  # projection into FDE (must be auditor-allowed)

    @model_validator(mode="after")
    def _check_invariants(self) -> AuditorFlag:
        if self.target_persona not in WAVE1_PERSONAS:
            raise ValueError(
                f"flag target_persona must be a wave-1 analysis persona, got {self.target_persona}"
            )
        if self.taxonomy_mapping not in AUDITOR_ALLOWED_TAXONOMY:
            raise ValueError(
                f"taxonomy_mapping must be an auditor-allowed type (Agents.md §15.4), "
                f"got {self.taxonomy_mapping}"
            )
        # Flag type and taxonomy should correspond (defense against a mismatched
        # projection that would mislead the Mutation Engine).
        expected = {
            "CITATION_GAP": FailureTaxonomy.CITATION_GAP,
            "CONCLUSION_UNSUPPORTED": FailureTaxonomy.CONCLUSION_UNSUPPORTED,
            "CONFLICTING_CONCLUSIONS": FailureTaxonomy.CONFLICTING_CONCLUSIONS,
            "NUMBER_MISUSE": FailureTaxonomy.NUMBER_MISUSE,
            "HALLUCINATED_EVIDENCE": FailureTaxonomy.HALLUCINATED_EVIDENCE,
        }
        if expected[self.flag_type] != self.taxonomy_mapping:
            raise ValueError(
                f"taxonomy_mapping {self.taxonomy_mapping} does not match flag_type {self.flag_type}"
            )
        return self


class AuditorOutput(BaseModel):
    """CrossPersonaAuditor output — structured flags, NO probabilities.

    The auditor never touches the math (Five Non-Negotiable #2). The orchestrator
    consumes flags deterministically: weight caps, Crucible-brief injection, FDE write.

    spec_ref: Agents.md §11d
    """

    model_config = ConfigDict(frozen=True)

    ticker: str = ""
    cycle_id: str = ""
    flags: list[AuditorFlag] = Field(max_length=20, default_factory=list)
    summary: str = Field(max_length=600, default="")
