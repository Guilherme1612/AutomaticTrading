"""Persona-specific output schemas.

Schemas for all 7 analysis personas + Crucible + MemoWriter:
  - MacroRegime, CatalystSummarizer, MoatAnalyst (personas 1-3)
  - GrowthHunter, InsiderActivity, ShortInterest, Forensics (personas 4-7)
  - Crucible (adversarial attacker)
  - MemoWriter (operator-facing memo synthesis)

Each persona produces a structured output parsed through Pydantic v2.
All models include probability-sum validators and persona-specific invariants.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    regime_reasoning: str = Field(max_length=500)
    yield_curve_signal: Literal["NORMAL", "FLAT", "INVERTED"]
    vix_regime: Literal["LOW", "MODERATE", "ELEVATED", "CRISIS"]
    sector_rotation_summary: str = Field(max_length=300)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> MacroRegimeOutput:
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
        return self


class CatalystEntry(BaseModel):
    """Single catalyst within a CatalystSummarizer output."""

    model_config = ConfigDict(frozen=True)

    catalyst_type: Literal[
        "earnings", "fda_decision", "product_launch", "regulatory_ruling",
        "ma_close", "partnership", "guidance_update",
    ]
    description: str = Field(max_length=200)
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
    net_catalyst_outlook: str = Field(max_length=300)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> CatalystSummarizerOutput:
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
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
    reasoning: str = Field(max_length=300)
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
    competitive_entry_reasoning: str = Field(max_length=200)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_invariants(self) -> MoatAnalystOutput:
        # Probability sum
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")

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
    growth_durability_reasoning: str = Field(max_length=300)
    key_risk_to_growth: str = Field(max_length=200)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "GrowthHunterOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
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
    signal_reasoning: str = Field(max_length=300)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "InsiderActivityOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
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
    anomaly_reasoning: str = Field(max_length=300)
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_prob_sum(self) -> "ShortInterestOutput":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
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
    description: str = Field(max_length=300)
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

    @model_validator(mode="after")
    def _check_consistency(self) -> "ForensicsOutput":
        # Probability sum check
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities sum to {total}")
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
    description: str = Field(max_length=400)
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
    summary: str = Field(max_length=500)
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
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    verdict_line: str = Field(max_length=150)
    thesis_summary: str = Field(max_length=400)
    key_evidence: list[str] = Field(min_length=1, max_length=5)
    key_risks: list[str] = Field(max_length=3)
    conviction: float
    p_up: float
    p_flat: float
    p_down: float
    ev_multiple: float | None = None
    sizing_usd: float | None = None
    dissenting_personas: list[str]
    dissent_summary: str | None = Field(default=None, max_length=200)

    @field_validator("verdict_line")
    @classmethod
    def _check_verdict_prefix(cls, v: str) -> str:
        valid = ("STRONG_BUY", "BUY", "HOLD", "SKIP")
        if not any(v.startswith(prefix) for prefix in valid):
            raise ValueError(
                f"verdict_line must start with one of {valid}, got: '{v[:50]}'"
            )
        return v
