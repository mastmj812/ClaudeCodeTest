"""
Economic metrics: NPV, PV10, IRR, payout.
"""

import numpy as np
import numpy_financial as npf


def monthly_rate(annual_rate: float) -> float:
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def calc_npv(cashflows: np.ndarray, annual_discount_rate: float) -> float:
    """NPV at a given annual discount rate."""
    if len(cashflows) == 0:
        return 0.0
    r = monthly_rate(annual_discount_rate)
    return float(npf.npv(r, cashflows))


def calc_pv10(cashflows: np.ndarray) -> float:
    """SEC-standard PV10 (10% annual discount)."""
    return calc_npv(cashflows, 0.10)


def calc_irr(cashflows: np.ndarray) -> float | None:
    """
    Annualized IRR from monthly cash flows.
    Returns None if no positive IRR exists (never turns cash-flow positive,
    or multiple sign changes).
    """
    if len(cashflows) == 0:
        return None
    try:
        monthly_irr = npf.irr(cashflows)
        if monthly_irr is None or np.isnan(monthly_irr) or np.isinf(monthly_irr):
            return None
        return float((1.0 + monthly_irr) ** 12 - 1.0)
    except Exception:
        return None


def calc_payout_months(cashflows: np.ndarray) -> int | None:
    """
    Number of months until cumulative cash flow turns positive.
    Returns None if payout is never reached.
    """
    if len(cashflows) == 0:
        return None
    cumsum = np.cumsum(cashflows)
    positive = np.where(cumsum >= 0)[0]
    return int(positive[0]) if len(positive) else None


def well_economics(cashflows: np.ndarray, discount_rate: float = 0.10) -> dict:
    """Compute all four metrics for a single well's cash flow array."""
    payout = calc_payout_months(cashflows)
    return {
        "npv":     calc_npv(cashflows, discount_rate),
        "pv10":    calc_pv10(cashflows),
        "irr":     calc_irr(cashflows),
        "payout":  payout,
    }


def portfolio_irr(cashflow_matrix: list[np.ndarray]) -> float | None:
    """
    IRR on the sum of multiple well cash flows (pad shorter arrays with zeros).
    """
    if not cashflow_matrix:
        return None
    max_len = max(len(cf) for cf in cashflow_matrix)
    combined = np.zeros(max_len)
    for cf in cashflow_matrix:
        combined[:len(cf)] += cf
    return calc_irr(combined)
