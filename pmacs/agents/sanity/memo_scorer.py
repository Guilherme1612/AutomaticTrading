"""Memo accuracy scorer — cross-validates memo claims against evidence data.

Produces a 0-100 reliability score across 6 dimensions:
  1. Numerical grounding  (0-25): Do numbers in the memo trace to evidence?
  2. Verdict consistency  (0-15): Does verdict match conviction thresholds?
  3. Evidence coverage     (0-15): Are key evidence items backed by data?
  4. Risk coverage         (0-15): Do key_risks address Crucible attacks?
  5. Completeness          (0-15): Are required fields populated with real data?
  6. Cross-source coherence(0-15): Do financial_snapshot figures match evidence?

Higher score = more reliable memo. Memos scoring < 50 should be retried.

spec_ref: Agents.md §13.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreDimension:
    """One dimension of the memo score."""
    name: str
    score: float  # 0.0 to max_score
    max_score: float
    issues: list[str] = field(default_factory=list)


@dataclass
class MemoScore:
    """Aggregate memo reliability score with per-dimension breakdown."""
    total: float  # 0-100
    grade: str  # A/B/C/D/F
    dimensions: list[ScoreDimension] = field(default_factory=list)
    critical_issues: list[str] = field(default_factory=list)  # Issues that MUST be fixed

    @property
    def passed(self) -> bool:
        return self.total >= 50.0 and len(self.critical_issues) == 0

    def summary(self) -> str:
        lines = [f"Memo Score: {self.total:.0f}/100 (Grade: {self.grade})"]
        for d in self.dimensions:
            status = "OK" if d.score >= d.max_score * 0.6 else "LOW"
            lines.append(f"  {d.name}: {d.score:.0f}/{d.max_score:.0f} [{status}]")
            for issue in d.issues[:3]:
                lines.append(f"    - {issue}")
        if self.critical_issues:
            lines.append("CRITICAL ISSUES (must fix):")
            for ci in self.critical_issues:
                lines.append(f"  !! {ci}")
        return "\n".join(lines)


# --- Number extraction helpers -----------------------------------------------

_NUM_PATTERN = re.compile(
    r"""
    (?:                                # Optional currency/sign prefix
        [\$€£]?\s*[+-]?               #   $, +, -, or $+
        |[+-]?\s*[\$€£]?              #   or sign before currency
    )
    (\d[\d,]*\.?\d*)                   # The number itself (with commas)
    \s*                                # Optional whitespace
    ([BMKTx%]?)                        # Optional suffix: B(illion), M(illion), K, T, x, %
    (?![a-zA-Z])                       # NOT followed by more letters (prevents "Meta" M → Million)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_MULTIPLIER = {"b": 1e9, "m": 1e6, "k": 1e3, "t": 1e12}


def _extract_numbers(text: str) -> list[float]:
    """Extract all numeric values from text, normalizing $2.1B → 2_100_000_000."""
    if not text:
        return []
    nums = []
    for match in _NUM_PATTERN.finditer(text):
        try:
            raw = match.group(1).replace(",", "")
            val = float(raw)
            suffix = match.group(2).lower()
            if suffix in _MULTIPLIER:
                val *= _MULTIPLIER[suffix]
            # Skip percentages as raw numbers (they're already useful as-is)
            if suffix == "%":
                pass  # Keep as percentage value
            nums.append(val)
        except (ValueError, IndexError):
            continue
    return nums


def _extract_percentages(text: str) -> list[float]:
    """Extract percentage values from text like '+34%', '14.2%', '-5%'."""
    if not text:
        return []
    pattern = re.compile(r'[+-]?\d+\.?\d*\s*%')
    results = []
    for m in pattern.finditer(text):
        try:
            results.append(float(m.group(0).replace("%", "").strip()))
        except ValueError:
            continue
    return results


def _numbers_match(memo_num: float, evidence_num: float, tolerance: float = 0.15) -> bool:
    """Check if two numbers are within tolerance of each other.

    Uses relative tolerance for large numbers, absolute for small ones.
    """
    if evidence_num == 0:
        return abs(memo_num) < 1.0
    ratio = abs(memo_num - evidence_num) / max(abs(evidence_num), 1e-9)
    return ratio <= tolerance


# --- Evidence data extraction ------------------------------------------------

def _extract_evidence_numbers(evidence_data: list[dict]) -> dict[str, list[float]]:
    """Extract key financial numbers from evidence packets.

    Returns dict mapping metric categories to lists of values found in evidence.
    """
    metrics: dict[str, list[float]] = {
        "revenue": [],
        "revenue_growth": [],
        "eps": [],
        "margins": [],
        "pe_ratio": [],
        "fcf": [],
        "price_target": [],
        "debt_equity": [],
    }

    for packet in evidence_data:
        evidence_list = packet.get("evidence", [])
        if hasattr(packet, "evidence"):
            evidence_list = packet.evidence

        for ev in evidence_list:
            data = getattr(ev, "data", ev.get("data", {})) if isinstance(ev, dict) else getattr(ev, "data", {})
            ev_id = getattr(ev, "id", ev.get("id", "")) if isinstance(ev, dict) else getattr(ev, "id", "")

            if not data:
                continue

            # Revenue figures
            for key in ("revenueTTM", "revenue_ttm"):
                val = data.get(key)
                if val is not None:
                    try:
                        metrics["revenue"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            # Revenue from EDGAR
            rev_recent = data.get("revenue_most_recent", {})
            if isinstance(rev_recent, dict) and rev_recent.get("value_usd"):
                try:
                    metrics["revenue"].append(float(rev_recent["value_usd"]))
                except (TypeError, ValueError):
                    pass

            # Revenue growth
            for key in ("revenueGrowthTTMYoy_pct", "revenue_yoy_growth"):
                val = data.get(key)
                if val is not None:
                    try:
                        # Handle string values like "+34.2%"
                        if isinstance(val, str):
                            val = val.replace("%", "").replace("+", "").strip()
                        metrics["revenue_growth"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            # EPS
            for key in ("epsTTM", "eps_ttm"):
                val = data.get(key)
                if val is not None:
                    try:
                        metrics["eps"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            eps_recent = data.get("eps_most_recent", {})
            if isinstance(eps_recent, dict) and eps_recent.get("value"):
                try:
                    metrics["eps"].append(float(eps_recent["value"]))
                except (TypeError, ValueError):
                    pass

            # Margins
            for key in ("grossMarginTTM_pct", "netProfitMarginTTM_pct", "fcfMarginTTM_pct",
                         "operatingMarginTTM", "gross_margin_pct"):
                val = data.get(key)
                if val is not None:
                    try:
                        if isinstance(val, str):
                            val = val.replace("%", "").replace("+", "").strip()
                        metrics["margins"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            # P/E ratio
            for key in ("peNormalizedAnnual", "pe_ratio", "forwardPE"):
                val = data.get(key)
                if val is not None:
                    try:
                        metrics["pe_ratio"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            # Price targets
            for key in ("targetMeanPrice", "mean_target", "targetMedianPrice"):
                val = data.get(key)
                if val is not None:
                    try:
                        metrics["price_target"].append(float(val))
                    except (TypeError, ValueError):
                        pass

            # Debt/equity
            for key in ("totalDebtToEquityAnnual", "debt_to_equity"):
                val = data.get(key)
                if val is not None:
                    try:
                        metrics["debt_equity"].append(float(val))
                    except (TypeError, ValueError):
                        pass

    return metrics


# --- Scoring functions -------------------------------------------------------

def _score_numerical_grounding(
    memo: dict[str, Any],
    evidence_metrics: dict[str, list[float]],
) -> ScoreDimension:
    """Score 0-25: Do numbers in the memo trace back to evidence?"""
    max_score = 25.0
    issues: list[str] = []
    points = max_score  # Start full, deduct for problems

    # Check financial_snapshot numbers against evidence
    snapshot = memo.get("financial_snapshot", {})
    if snapshot:
        for field_name, metric_key in [
            ("revenue", "revenue"),
            ("revenue_growth", "revenue_growth"),
            ("pe_ratio", "pe_ratio"),
            ("gross_margin", "margins"),
            ("net_margin", "margins"),
            ("operating_margin", "margins"),
        ]:
            snap_val = snapshot.get(field_name, "")
            if not snap_val or snap_val in ("N/A", "n/a", "DATA NOT AVAILABLE"):
                continue
            snap_nums = _extract_numbers(str(snap_val))
            if not snap_nums:
                continue
            ev_nums = evidence_metrics.get(metric_key, [])
            if not ev_nums:
                # Number in memo but not in evidence — potential hallucination
                issues.append(f"financial_snapshot.{field_name}={snap_val} not found in evidence")
                points -= 3.0
                continue
            # Check if any evidence number is close to the memo number
            best_match = False
            for sn in snap_nums:
                for en in ev_nums:
                    if _numbers_match(sn, en, tolerance=0.20):
                        best_match = True
                        break
                if best_match:
                    break
            if not best_match:
                issues.append(
                    f"financial_snapshot.{field_name}={snap_val} diverges from evidence "
                    f"({[f'{n:.2f}' for n in ev_nums[:3]]})"
                )
                points -= 4.0

    # Check fair_value against price targets if available
    fair_value = memo.get("fair_value")
    price_targets = evidence_metrics.get("price_target", [])
    if fair_value and price_targets:
        closest = min(price_targets, key=lambda pt: abs(pt - fair_value))
        if not _numbers_match(fair_value, closest, tolerance=0.50):
            issues.append(
                f"fair_value=${fair_value:.2f} far from analyst targets "
                f"({['$' + f'{pt:.2f}' for pt in price_targets[:3]]})"
            )
            points -= 3.0

    # Check thesis and key_evidence for numbers
    thesis = memo.get("thesis", "")
    thesis_nums = _extract_numbers(thesis)
    if not thesis_nums:
        issues.append("thesis contains no specific numbers — likely vague")
        points -= 3.0

    key_evidence = memo.get("key_evidence", [])
    items_without_numbers = 0
    for item in key_evidence:
        if not _extract_numbers(str(item)):
            items_without_numbers += 1
    if items_without_numbers > 1:
        issues.append(f"{items_without_numbers}/{len(key_evidence)} key_evidence items lack numbers")
        points -= 2.0

    return ScoreDimension(
        name="Numerical Grounding",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


def _score_verdict_consistency(
    memo: dict[str, Any],
    conviction: float | None = None,
    verdict: str | None = None,
) -> ScoreDimension:
    """Score 0-15: Does verdict match conviction thresholds?"""
    max_score = 15.0
    issues: list[str] = []
    points = max_score

    verdict_line = memo.get("verdict_line", "")
    memo_conviction = memo.get("conviction", conviction)

    if memo_conviction is not None and verdict_line:
        # Determine expected verdict from conviction
        if memo_conviction >= 0.75:
            expected = "STRONG_BUY"
        elif memo_conviction >= 0.60:
            expected = "BUY"
        elif memo_conviction >= 0.40:
            expected = "HOLD"
        else:
            expected = "SKIP"

        # Check if verdict_line matches expected
        if not verdict_line.startswith(expected):
            # Check if it uses the engine verdict (which is authoritative)
            if verdict and verdict_line.startswith(verdict):
                pass  # Engine verdict override is fine
            else:
                issues.append(
                    f"verdict_line starts with '{verdict_line[:15]}...' but conviction "
                    f"{memo_conviction:.2f} implies {expected}"
                )
                points -= 8.0

    # Check p_up/p_flat/p_down sum
    p_up = memo.get("p_up", 0.0)
    p_flat = memo.get("p_flat", 0.0)
    p_down = memo.get("p_down", 0.0)
    if p_up + p_flat + p_down > 0:
        total = p_up + p_flat + p_down
        if abs(total - 1.0) > 0.05:
            issues.append(f"p_up+p_flat+p_down={total:.3f}, should be ~1.0")
            points -= 5.0

    # Check conviction is in valid range
    if memo_conviction is not None:
        if memo_conviction < 0.0 or memo_conviction > 1.0:
            issues.append(f"conviction={memo_conviction} out of [0,1] range")
            points -= 5.0

    return ScoreDimension(
        name="Verdict Consistency",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


def _score_evidence_coverage(
    memo: dict[str, Any],
    agent_results: list[dict] | None = None,
) -> ScoreDimension:
    """Score 0-15: Are key evidence items backed by agent data?"""
    max_score = 15.0
    issues: list[str] = []
    points = max_score

    key_evidence = memo.get("key_evidence", [])
    if not key_evidence:
        issues.append("key_evidence is empty")
        return ScoreDimension(name="Evidence Coverage", score=0.0, max_score=max_score, issues=issues)

    if len(key_evidence) < 3:
        issues.append(f"Only {len(key_evidence)} evidence items (need 3-5)")
        points -= 5.0

    # Check each evidence item has substance (not just generic statements)
    generic_patterns = [
        r"strong\s+growth",
        r"solid\s+fundamentals",
        r"attractive\s+valuation",
        r"market\s+leader",
        r"well[-\s]positioned",
        r"significant\s+opportunity",
    ]
    for i, item in enumerate(key_evidence):
        item_lower = str(item).lower()
        for pattern in generic_patterns:
            if re.search(pattern, item_lower) and not _extract_numbers(str(item)):
                issues.append(f"key_evidence[{i}] is generic with no numbers: '{str(item)[:60]}...'")
                points -= 2.0
                break

    # Cross-check against agent signals if available
    if agent_results:
        agent_text = " ".join(
            str(r.get("key_signal", "")) + " " + str(r.get("analysis", ""))
            for r in agent_results if not r.get("error")
        ).lower()
        unsupported = 0
        for item in key_evidence:
            # Extract key phrases (3+ word sequences) from evidence item
            words = str(item).lower().split()
            key_phrases = [" ".join(words[j:j+3]) for j in range(len(words) - 2)]
            found_match = False
            for phrase in key_phrases[:5]:
                # Remove common words for matching
                if any(w in phrase for w in ["the", "and", "this", "that", "with"]):
                    continue
                if phrase in agent_text:
                    found_match = True
                    break
            if not found_match and _extract_numbers(str(item)):
                # Has numbers but no phrase match — check if numbers appear in agent text
                item_nums = _extract_numbers(str(item))
                for num in item_nums[:2]:
                    num_str = f"{num:.0f}" if num == int(num) else f"{num:.1f}"
                    if num_str in agent_text:
                        found_match = True
                        break
            if not found_match:
                unsupported += 1
        if unsupported > len(key_evidence) // 2:
            issues.append(f"{unsupported}/{len(key_evidence)} evidence items not traceable to agent outputs")
            points -= 5.0

    return ScoreDimension(
        name="Evidence Coverage",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


def _score_risk_coverage(
    memo: dict[str, Any],
    crucible_attacks: list | None = None,
) -> ScoreDimension:
    """Score 0-15: Do key_risks address Crucible attacks?"""
    max_score = 15.0
    issues: list[str] = []
    points = max_score

    key_risks = memo.get("key_risks", [])
    if not key_risks:
        issues.append("key_risks is empty")
        return ScoreDimension(name="Risk Coverage", score=0.0, max_score=max_score, issues=issues)

    if len(key_risks) < 2:
        issues.append(f"Only {len(key_risks)} risk items (need 2-3)")
        points -= 5.0

    # Check risks contain specifics (not just "market risk" or "competition")
    for i, risk in enumerate(key_risks):
        if not _extract_numbers(str(risk)):
            issues.append(f"key_risks[{i}] has no quantitative anchor: '{str(risk)[:60]}...'")
            points -= 2.0

    # Check Crucible attack coverage
    if crucible_attacks:
        attack_types = set()
        for attack in crucible_attacks:
            if isinstance(attack, dict):
                atype = attack.get("attack_type", attack.get("type", "")).upper()
            elif isinstance(attack, str):
                atype = attack.split(":")[0].strip().upper() if ":" in attack else attack[:20].upper()
            else:
                continue
            if atype:
                attack_types.add(atype)

        # Check if risks mention the attack types
        risks_text = " ".join(str(r).lower() for r in key_risks)
        unaddressed = []
        for atype in attack_types:
            if atype.lower() not in risks_text and not any(
                w in risks_text for w in atype.lower().split("_")
            ):
                unaddressed.append(atype)

        if unaddressed:
            issues.append(f"Crucible attacks not addressed in key_risks: {unaddressed}")
            points -= min(8.0, len(unaddressed) * 3.0)

    return ScoreDimension(
        name="Risk Coverage",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


def _score_completeness(memo: dict[str, Any]) -> ScoreDimension:
    """Score 0-15: Are required and recommended fields populated?"""
    max_score = 15.0
    issues: list[str] = []
    points = max_score

    # Required fields (deduct heavily if missing)
    required = ["verdict_line", "thesis", "key_evidence", "key_risks"]
    for f in required:
        val = memo.get(f)
        if not val or (isinstance(val, list) and len(val) == 0):
            issues.append(f"Required field '{f}' is missing/empty")
            points -= 4.0

    # Recommended fields (deduct lightly if missing)
    recommended = [
        "fair_value", "valuation_methodology", "financial_snapshot",
        "business_model", "conviction", "p_up",
    ]
    missing_recommended = 0
    for f in recommended:
        val = memo.get(f)
        if val is None or val == "" or val == {} or val == 0.0:
            missing_recommended += 1
    if missing_recommended > 3:
        issues.append(f"{missing_recommended}/{len(recommended)} recommended fields missing")
        points -= min(5.0, missing_recommended * 1.0)

    # Check thesis length (should be substantive)
    thesis = memo.get("thesis", "")
    if thesis and len(thesis) < 100:
        issues.append(f"thesis is only {len(thesis)} chars — too brief for investment decision")
        points -= 3.0

    # Check financial_snapshot isn't all N/A
    snapshot = memo.get("financial_snapshot", {})
    if snapshot:
        na_count = sum(1 for v in snapshot.values() if str(v).upper() in ("N/A", "UNKNOWN", ""))
        if na_count > len(snapshot) * 0.6 and len(snapshot) > 2:
            issues.append(f"{na_count}/{len(snapshot)} financial_snapshot fields are N/A")
            points -= 3.0

    return ScoreDimension(
        name="Completeness",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


def _score_cross_source_coherence(
    memo: dict[str, Any],
    evidence_metrics: dict[str, list[float]],
) -> ScoreDimension:
    """Score 0-15: Do financial figures in the memo internally agree?"""
    max_score = 15.0
    issues: list[str] = []
    points = max_score

    # Check fair_value vs valuation_range
    fair_value = memo.get("fair_value")
    val_range = memo.get("valuation_range", {})
    if fair_value and val_range:
        low = val_range.get("low")
        high = val_range.get("high")
        base = val_range.get("base")
        if low is not None and high is not None:
            try:
                low_f, high_f, fv_f = float(low), float(high), float(fair_value)
                if fv_f < low_f or fv_f > high_f:
                    issues.append(
                        f"fair_value=${fv_f:.2f} outside valuation_range "
                        f"[${low_f:.2f}-${high_f:.2f}]"
                    )
                    points -= 5.0
                if low_f >= high_f:
                    issues.append(f"valuation_range inverted: low=${low_f:.2f} >= high=${high_f:.2f}")
                    points -= 5.0
                if base is not None:
                    base_f = float(base)
                    if base_f < low_f or base_f > high_f:
                        issues.append(f"base=${base_f:.2f} outside low/high range")
                        points -= 3.0
            except (TypeError, ValueError):
                pass

    # Check verdict vs thesis sentiment alignment
    verdict_line = memo.get("verdict_line", "")
    thesis = memo.get("thesis", "")
    if verdict_line and thesis:
        is_bullish_verdict = any(verdict_line.startswith(v) for v in ("STRONG_BUY", "BUY"))
        is_bearish_verdict = verdict_line.startswith("SKIP")

        # Simple sentiment check — count positive/negative financial keywords
        bull_words = ["growth", "expanding", "improving", "accelerat", "undervalued",
                      "catalyst", "upside", "beat", "exceed", "outperform"]
        bear_words = ["decline", "deteriorat", "overvalued", "downside", "miss",
                      "weakness", "risk", "concern", "negative", "falling"]
        thesis_lower = thesis.lower()
        bull_count = sum(1 for w in bull_words if w in thesis_lower)
        bear_count = sum(1 for w in bear_words if w in thesis_lower)

        if is_bullish_verdict and bear_count > bull_count + 3:
            issues.append("BUY verdict but thesis sentiment is predominantly bearish")
            points -= 5.0
        elif is_bearish_verdict and bull_count > bear_count + 3:
            issues.append("SKIP verdict but thesis sentiment is predominantly bullish")
            points -= 5.0

    # Check p_up direction matches verdict
    p_up = memo.get("p_up", 0.0)
    p_down = memo.get("p_down", 0.0)
    if p_up > 0 and p_down > 0 and verdict_line:
        if verdict_line.startswith(("STRONG_BUY", "BUY")) and p_down > p_up:
            issues.append(f"BUY verdict but p_down={p_down:.2f} > p_up={p_up:.2f}")
            points -= 5.0
        elif verdict_line.startswith("SKIP") and p_up > p_down + 0.2:
            issues.append(f"SKIP verdict but p_up={p_up:.2f} >> p_down={p_down:.2f}")
            points -= 3.0

    return ScoreDimension(
        name="Cross-Source Coherence",
        score=max(0.0, min(max_score, points)),
        max_score=max_score,
        issues=issues,
    )


# --- Main scorer entry point -------------------------------------------------

def score_memo(
    memo: dict[str, Any],
    evidence: list | None = None,
    agent_results: list[dict] | None = None,
    crucible_attacks: list | None = None,
    conviction: float | None = None,
    verdict: str | None = None,
) -> MemoScore:
    """Score a memo for accuracy and reliability.

    Args:
        memo: The memo dict (from MemoWriterOutput.model_dump() or JSON parse).
        evidence: List of EvidencePacket objects or dicts with evidence data.
        agent_results: List of agent result dicts with persona/key_signal/analysis.
        crucible_attacks: List of Crucible attack dicts.
        conviction: Engine conviction score (authoritative).
        verdict: Engine verdict string (authoritative).

    Returns:
        MemoScore with total 0-100, grade A-F, per-dimension breakdown.
    """
    # Extract numbers from evidence for cross-validation
    evidence_metrics: dict[str, list[float]] = {}
    if evidence:
        # Handle both EvidencePacket objects and raw dicts
        evidence_dicts = []
        for ep in evidence:
            if isinstance(ep, dict):
                evidence_dicts.append(ep)
            else:
                evidence_dicts.append({"evidence": getattr(ep, "evidence", [])})
        evidence_metrics = _extract_evidence_numbers(evidence_dicts)

    # Score each dimension
    dimensions = [
        _score_numerical_grounding(memo, evidence_metrics),
        _score_verdict_consistency(memo, conviction, verdict),
        _score_evidence_coverage(memo, agent_results),
        _score_risk_coverage(memo, crucible_attacks),
        _score_completeness(memo),
        _score_cross_source_coherence(memo, evidence_metrics),
    ]

    total = sum(d.score for d in dimensions)

    # Identify critical issues (that MUST be fixed on retry)
    critical: list[str] = []
    for d in dimensions:
        if d.score < d.max_score * 0.3:  # Below 30% on any dimension = critical
            critical.append(f"{d.name} scored {d.score:.0f}/{d.max_score:.0f}: {'; '.join(d.issues[:2])}")

    # Also flag specific critical problems
    if not memo.get("verdict_line"):
        critical.append("verdict_line is missing")
    if not memo.get("thesis"):
        critical.append("thesis is missing")
    if not memo.get("key_evidence"):
        critical.append("key_evidence is missing")

    # Grade assignment
    if total >= 85:
        grade = "A"
    elif total >= 70:
        grade = "B"
    elif total >= 55:
        grade = "C"
    elif total >= 40:
        grade = "D"
    else:
        grade = "F"

    return MemoScore(
        total=total,
        grade=grade,
        dimensions=dimensions,
        critical_issues=critical,
    )


def format_retry_feedback(score: MemoScore) -> str:
    """Format scoring feedback to inject into retry prompt.

    This tells the LLM what was wrong so it can fix it on the next attempt.
    """
    lines = [
        "## MEMO QUALITY FEEDBACK (fix these issues)",
        f"Previous memo scored {score.total:.0f}/100 (Grade: {score.grade}). "
        "The following issues MUST be fixed:",
        "",
    ]

    # Prioritize critical issues
    if score.critical_issues:
        lines.append("### CRITICAL (will cause rejection):")
        for ci in score.critical_issues:
            lines.append(f"- {ci}")
        lines.append("")

    # Then per-dimension issues, sorted by severity
    sorted_dims = sorted(score.dimensions, key=lambda d: d.score / max(d.max_score, 1))
    for d in sorted_dims:
        if d.issues:
            pct = d.score / d.max_score * 100
            lines.append(f"### {d.name} ({pct:.0f}%):")
            for issue in d.issues[:3]:
                lines.append(f"- FIX: {issue}")
            lines.append("")

    lines.append(
        "IMPORTANT: Use ONLY numbers from the evidence provided. "
        "Do not fabricate financial figures. If data is unavailable, "
        "write 'DATA NOT AVAILABLE' — do not guess."
    )

    return "\n".join(lines)


# --- Fundamentals text parser for pipeline integration ------------------------

def parse_fundamentals_text(text: str) -> list[dict]:
    """Parse a fundamentals text block into evidence-like dicts for the scorer.

    The pipeline prefetches fundamentals as formatted text with lines like:
      Revenue TTM: $1.44B
      Revenue growth TTM YoY: +49.9%
      Gross margin TTM: 60.1%
      ...

    Extracts key-value pairs and builds a synthetic evidence structure that
    _extract_evidence_numbers() can consume for cross-validation.
    """
    if not text:
        return []

    data: dict = {}

    # Pattern: "Label: value" lines
    kv_pattern = re.compile(r"^\s*(.+?):\s+(.+)$", re.MULTILINE)
    for m in kv_pattern.finditer(text):
        label = m.group(1).strip().lower()
        value = m.group(2).strip()

        # Revenue
        if "revenue ttm" in label and "growth" not in label:
            nums = _extract_numbers(value)
            if nums:
                data["revenueTTM"] = nums[0]
        elif "revenue growth" in label or "revenue yoy" in label:
            pcts = _extract_percentages(value)
            if pcts:
                data["revenueGrowthTTMYoy_pct"] = f"{pcts[0]:+.1f}%"
        elif "most recent revenue" in label:
            nums = _extract_numbers(value)
            if nums:
                data["revenue_most_recent"] = {"value_usd": nums[0]}

        # EPS
        elif "eps ttm" in label or "most recent eps" in label:
            nums = _extract_numbers(value)
            if nums:
                data["epsTTM"] = nums[0]

        # Margins
        elif "gross margin" in label:
            pcts = _extract_percentages(value)
            if pcts:
                data["grossMarginTTM_pct"] = f"{pcts[0]:.1f}%"
        elif "net margin" in label or "net profit margin" in label:
            pcts = _extract_percentages(value)
            if pcts:
                data["netProfitMarginTTM_pct"] = f"{pcts[0]:.1f}%"
        elif "operating margin" in label:
            pcts = _extract_percentages(value)
            if pcts:
                data["operatingMarginTTM"] = pcts[0] / 100.0
        elif "fcf margin" in label:
            pcts = _extract_percentages(value)
            if pcts:
                data["fcfMarginTTM_pct"] = f"{pcts[0]:.1f}%"

        # P/E ratio
        elif "p/e" in label or "pe " in label:
            nums = _extract_numbers(value)
            if nums:
                data["peNormalizedAnnual"] = nums[0]

        # Price targets
        elif "price target" in label or "target mean" in label or "consensus" in label:
            nums = _extract_numbers(value)
            if nums:
                data["targetMeanPrice"] = nums[0]

        # Debt/equity
        elif "debt" in label and "equity" in label:
            nums = _extract_numbers(value)
            if nums:
                data["totalDebtToEquityAnnual"] = nums[0]

    if not data:
        return []

    # Build a synthetic evidence structure matching what _extract_evidence_numbers expects
    class _SyntheticSource:
        value = "fundamentals"

    class _SyntheticEvidence:
        def __init__(self, d: dict):
            self.data = d
            self.id = "fundamentals_synthetic"
            self.source = _SyntheticSource()

    class _SyntheticPacket:
        def __init__(self, d: dict):
            self.evidence = [_SyntheticEvidence(d)]


# --- Prior-memo extraction (Commit 2 — Tier 2A) -----------------------------
# The orchestrator's _step_13c_episodic_context previously pulled only 7 fields
# from the prior memo and truncated prior_key_signal to 200 chars. The full
# thesis, fair_value, methodology, evidence, risks, what_would_change_my_mind,
# and forward_valuation.expected_price_usd were never reinjected — so on
# cycle 2+ the LLM re-derives facts already in the persisted memo. This
# helper extracts the rich subset from the JSON-serialised memos.memo_json
# so it can be passed to build_context_brief as new kwargs and rendered into
# the persona prompt.
#
# spec_ref: Architecture.md §16.9; Agents.md §13.5

def extract_prior_memo_summary(memo_json_str: str | None) -> dict:
    """Extract the rich prior-memo fields from a memos.memo_json string.

    Returns a dict with the following keys (all optional; missing keys default
    to None / empty list):
      - thesis: str
      - verdict_line: str
      - fair_value: str | None
      - valuation_methodology: str | None
      - key_evidence: list[str]
      - key_risks: list[str]
      - what_would_change_my_mind: list[str]
      - forward_expected_price_usd: float | None
      - crucible_severity: float | None
      - conviction: float | None
      - decided_at: str | None

    On any parse failure (empty string, malformed JSON, non-dict), returns
    an empty dict. This helper is best-effort — never raises. The orchestrator
    treats an empty result as "no prior memo" and continues.
    """
    if not memo_json_str:
        return {}
    try:
        import json as _epj
        parsed = _epj.loads(memo_json_str)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    def _get_str(*keys: str) -> str | None:
        for k in keys:
            v = parsed.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return None

    def _get_list(*keys: str) -> list[str]:
        for k in keys:
            v = parsed.get(k)
            if isinstance(v, list):
                return [str(x) for x in v if x is not None][:8]
            if isinstance(v, str) and v.strip():
                # Some memos store a single string in what_would_change_my_mind.
                return [v.strip()][:8]
        return []

    # forward_valuation is a nested dict on the persisted memo_json.
    fv = parsed.get("forward_valuation")
    fv_price: float | None = None
    if isinstance(fv, dict):
        _p = fv.get("expected_price_usd")
        if isinstance(_p, (int, float)):
            fv_price = float(_p)

    _sev = parsed.get("crucible_severity")
    _conv = parsed.get("conviction")
    return {
        "thesis": _get_str("thesis"),
        "verdict_line": _get_str("verdict_line"),
        "fair_value": _get_str("fair_value_estimate", "fair_value"),
        "valuation_methodology": _get_str("valuation_methodology"),
        "key_evidence": _get_list("key_evidence"),
        "key_risks": _get_list("key_risks"),
        "what_would_change_my_mind": _get_list("what_would_change_my_mind"),
        "forward_expected_price_usd": fv_price,
        "crucible_severity": float(_sev) if isinstance(_sev, (int, float)) else None,
        "conviction": float(_conv) if isinstance(_conv, (int, float)) else None,
        "decided_at": _get_str("decided_at"),
    }


# --- Persona weight table formatter (Commit 4 — Tier 4) ----------------------
# Surfaces who drove the arbitration verdict and how reliable each persona is
# on this ticker (per-persona Brier calibration from DuckDB). The operator
# can see, in the memo, which personas were weighted highest.

def format_persona_weight_table(
    persona_weights: list | None,
    per_persona_calibration: dict | None = None,
) -> str:
    """Render a sorted ASCII table of persona arbitration weights.

    Each row: ``{persona:20s}  weight={pct:5.1f}%  brier={brier:.3f}  n={n}  mult={mult:.2f}``

    Rows sorted by weight desc. If ``persona_weights`` is empty/None, returns
    an empty string so callers can short-circuit rendering.

    Args:
        persona_weights: List of PersonaWeight objects (or dicts) with fields
            persona, weight, brier_score, calibration_count, weight_multiplier.
        per_persona_calibration: Optional dict mapping persona_name → avg_brier
            (from DuckDB persona_ticker_affinity). When supplied, an extra
            "ticker_brier" column appears and a "LOW CONFIDENCE on this ticker"
            marker is shown for brier > 0.25.

    Returns:
        Multi-line string. Empty string when no weights are provided.
    """
    if not persona_weights:
        return ""
    # Normalize to (name, weight_pct, brier, n, mult, ticker_brier|None)
    rows: list[tuple] = []
    for w in persona_weights:
        if isinstance(w, dict):
            name = str(w.get("persona", "?"))
            weight = float(w.get("weight", 0.0))
            brier = float(w.get("brier_score", 0.0) or 0.0)
            n = int(w.get("calibration_count", 0) or 0)
            mult = float(w.get("weight_multiplier", 1.0) or 1.0)
        else:
            name = str(getattr(w, "persona", "?"))
            weight = float(getattr(w, "weight", 0.0) or 0.0)
            brier = float(getattr(w, "brier_score", 0.0) or 0.0)
            n = int(getattr(w, "calibration_count", 0) or 0)
            mult = float(getattr(w, "weight_multiplier", 1.0) or 1.0)
        ticker_brier = None
        if per_persona_calibration:
            tb = per_persona_calibration.get(name)
            if isinstance(tb, (int, float)):
                ticker_brier = float(tb)
        rows.append((name, weight * 100.0, brier, n, mult, ticker_brier))
    rows.sort(key=lambda r: r[1], reverse=True)

    header = (
        f"  {'persona':20s}  {'weight':>7s}  {'brier':>6s}  {'n':>4s}  "
        f"{'mult':>5s}  {'ticker_brier':>12s}  note"
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    for name, pct, brier, n, mult, tb in rows:
        tb_str = f"{tb:.3f}" if tb is not None else "—"
        note = ""
        if tb is not None and tb > 0.25:
            note = "  LOW CONFIDENCE on this ticker"
        lines.append(
            f"  {name:20s}  {pct:6.1f}%  {brier:6.3f}  {n:4d}  "
            f"{mult:5.2f}  {tb_str:>12s}{note}"
        )
    return "\n".join(lines)

    return [_SyntheticPacket(data)]
