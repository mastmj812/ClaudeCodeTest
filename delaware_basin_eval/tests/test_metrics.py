"""
Economic metrics: NPV, PV10, IRR, payout — sanity checks on cash flow patterns
with known closed-form answers.
"""

import numpy as np
import pytest

from economics.metrics import (
    calc_npv, calc_pv10, calc_irr, calc_payout_months,
    monthly_rate, well_economics, portfolio_irr,
)


def test_monthly_rate_compounds_to_annual():
    r_m = monthly_rate(0.10)
    assert (1.0 + r_m) ** 12 == pytest.approx(1.10, rel=1e-9)


def test_npv_of_pure_capex_is_negative_capex():
    cf = np.array([-100.0])
    assert calc_npv(cf, 0.10) == pytest.approx(-100.0)


def test_npv_zero_discount_equals_sum():
    cf = np.array([-100.0, 30.0, 40.0, 50.0])
    assert calc_npv(cf, 0.0) == pytest.approx(20.0)


def test_pv10_uses_10_percent():
    # NPV at 10% should match calc_pv10
    cf = np.array([-100.0, 20.0, 30.0, 60.0, 50.0])
    assert calc_pv10(cf) == pytest.approx(calc_npv(cf, 0.10))


def test_irr_for_break_even_after_one_month_is_well_defined():
    # -100 at t=0, +110 at t=1 (monthly) → monthly IRR = 10%, annual ≈ 213.84%
    cf = np.array([-100.0, 110.0])
    irr = calc_irr(cf)
    assert irr is not None
    assert irr == pytest.approx((1.10) ** 12 - 1.0, rel=1e-6)


def test_irr_none_when_never_positive():
    cf = np.array([-100.0, -10.0, -5.0])
    assert calc_irr(cf) is None


def test_payout_returns_month_of_first_breakeven():
    # cumulative: -100, -70, -30, +10 → payout at month 3
    cf = np.array([-100.0, 30.0, 40.0, 40.0])
    assert calc_payout_months(cf) == 3


def test_payout_none_when_never_recovers():
    cf = np.array([-100.0, 5.0, 5.0, 5.0])
    assert calc_payout_months(cf) is None


def test_well_economics_returns_all_four_metrics():
    cf = np.array([-100.0, 30.0, 40.0, 50.0, 50.0])
    e = well_economics(cf, discount_rate=0.10)
    assert set(e.keys()) == {"npv", "pv10", "irr", "payout"}
    assert e["payout"] is not None


def test_portfolio_irr_handles_unequal_lengths():
    # Two wells with different lifespans; portfolio IRR should be well-defined.
    cf_a = np.array([-100.0, 30.0, 40.0, 50.0])
    cf_b = np.array([-50.0, 20.0, 20.0, 20.0, 10.0, 10.0])
    irr = portfolio_irr([cf_a, cf_b])
    assert irr is not None and np.isfinite(irr)


def test_portfolio_irr_empty_input():
    assert portfolio_irr([]) is None
