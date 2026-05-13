"""Statistical test for mutation A/B evaluation (Agents.md §17.3).

Welch's t-test with Cohen's d effect size.
Significance requires ALL THREE: p < 0.05, Cohen's d >= 0.20, n >= 20.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

try:
    from scipy.stats import t as scipy_t
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


@dataclass
class StatTestResult:
    p_value: float
    cohens_d: float
    sample_size: int
    is_significant: bool  # p < 0.05 AND cohens_d >= 0.20 AND n >= 20
    control_mean: float
    candidate_mean: float


def welch_t_test(
    control: list[float],
    candidate: list[float],
    alpha: float = 0.05,
    min_cohens_d: float = 0.20,
    min_sample: int = 20,
) -> StatTestResult:
    """Welch's t-test between control and candidate arms.

    Returns p-value, Cohen's d, and significance assessment.
    Significance requires all three conditions simultaneously.
    """
    n1, n2 = len(control), len(candidate)
    if n1 < 2 or n2 < 2:
        return StatTestResult(
            p_value=1.0,
            cohens_d=0.0,
            sample_size=min(n1, n2),
            is_significant=False,
            control_mean=_mean(control),
            candidate_mean=_mean(candidate),
        )

    m1, m2 = _mean(control), _mean(candidate)
    v1, v2 = _var(control), _var(candidate)

    # Degenerate case: zero variance in both groups
    # If means differ with zero variance, effect is deterministic
    if v1 == 0.0 and v2 == 0.0:
        if m1 == m2:
            return StatTestResult(
                p_value=1.0, cohens_d=0.0, sample_size=min(n1, n2),
                is_significant=False, control_mean=m1, candidate_mean=m2,
            )
        # Deterministic difference — treat as extremely significant
        total_n = min(n1, n2)
        return StatTestResult(
            p_value=0.0, cohens_d=float("inf"), sample_size=total_n,
            is_significant=total_n >= min_sample,
            control_mean=m1, candidate_mean=m2,
        )

    # Welch's t statistic
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        t = 0.0
    else:
        t = (m1 - m2) / se

    # Welch-Satterthwaite degrees of freedom
    num = (v1 / n1 + v2 / n2) ** 2
    denom = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / denom if denom > 0 else 1.0

    # p-value (two-tailed)
    p_value = 2.0 * _t_cdf(-abs(t), df)

    # Cohen's d (pooled standard deviation)
    pooled_std = (
        math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
        if (n1 + n2 > 2)
        else 1.0
    )
    cohens_d = abs(m2 - m1) / pooled_std if pooled_std > 0 else 0.0

    total_n = min(n1, n2)
    is_sig = p_value < alpha and cohens_d >= min_cohens_d and total_n >= min_sample

    return StatTestResult(
        p_value=p_value,
        cohens_d=cohens_d,
        sample_size=total_n,
        is_significant=is_sig,
        control_mean=m1,
        candidate_mean=m2,
    )


def _mean(data: list[float]) -> float:
    return sum(data) / len(data) if data else 0.0


def _var(data: list[float]) -> float:
    if len(data) < 2:
        return 0.0
    m = _mean(data)
    return sum((x - m) ** 2 for x in data) / (len(data) - 1)


def _t_cdf(t: float, df: float) -> float:
    """CDF of Student's t-distribution.

    Uses scipy.stats.t.cdf when available (exact), otherwise falls back
    to the Lentz continued-fraction approximation.
    """
    if _HAS_SCIPY:
        return float(scipy_t.cdf(t, df))
    return _t_cdf_lentz(t, df)


def _t_cdf_lentz(t: float, df: float) -> float:
    """CDF of Student's t-distribution via Lentz continued-fraction approximation.

    Uses the relation: CDF(t, df) = 1 - 0.5 * I_{df/(df+t^2)}(df/2, 0.5)
    For negative t, uses symmetry: CDF(-t) = 1 - CDF(t).
    """
    if t == 0.0:
        return 0.5
    x = df / (df + t * t)
    beta_val = _regularized_incomplete_beta(x, df / 2.0, 0.5)
    if t > 0:
        return 1.0 - 0.5 * beta_val
    else:
        return 0.5 * beta_val


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a, b) via Lentz continued fraction.

    Uses the continued fraction representation evaluated with modified Lentz's
    method (Numerical Recipes 6.4, DLMF 8.17). Accuracy within 1e-10 for
    typical t-test parameters (df 1-1000, |t| < 100).

    The symmetry relation I_x(a,b) = 1 - I_{1-x}(b,a) is used for x > (a+1)/(a+b+2)
    to ensure convergence.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    # Use symmetry for convergence
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _regularized_incomplete_beta(1.0 - x, b, a)

    # Prefix: x^a * (1-x)^b / (a * B(a,b))
    ln_prefix = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    prefix = math.exp(ln_prefix) / a

    # Lentz continued fraction (modified Lentz's method)
    TINY = 1e-30
    f = 1.0
    c = 1.0
    d = 1.0 - (a + 1.0) * x / (a + 1.0)
    if abs(d) < TINY:
        d = TINY
    d = 1.0 / d
    f = d

    for m in range(1, 200):
        # Even step: numerator = m*(b-m)*x / ((a+2m-1)*(a+2m))
        num = m * (b - m) * x / ((a + 2.0 * m - 1.0) * (a + 2.0 * m))
        d = 1.0 + num * d
        if abs(d) < TINY:
            d = TINY
        c = 1.0 + num / c
        if abs(c) < TINY:
            c = TINY
        d = 1.0 / d
        f *= c * d

        # Odd step: numerator = -(a+m)*(a+b+m)*x / ((a+2m)*(a+2m+1))
        num = -(a + m) * (a + b + m) * x / ((a + 2.0 * m) * (a + 2.0 * m + 1.0))
        d = 1.0 + num * d
        if abs(d) < TINY:
            d = TINY
        c = 1.0 + num / c
        if abs(c) < TINY:
            c = TINY
        d = 1.0 / d
        delta = c * d
        f *= delta

        if abs(delta - 1.0) < 1e-12:
            break

    return prefix * f
