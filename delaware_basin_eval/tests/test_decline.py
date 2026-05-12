"""
Decline-curve math: ramp behavior, hyperbolic/exponential rate formulas,
fit roundtrip on synthetic data, and Di unit conversions.
"""

import numpy as np
import pytest

from engineering.decline import (
    _hyperbolic,
    _exponential,
    fit_decline,
    generate_stream_profile,
    project_monthly_volumes,
    nominal_annual_to_effective_annual,
    effective_annual_to_nominal_annual,
)


# ── Rate primitives ────────────────────────────────────────────────────────

def test_hyperbolic_at_t0_equals_qi():
    assert _hyperbolic(0.0, qi=1000.0, Di=0.10, b=1.0) == pytest.approx(1000.0)


def test_hyperbolic_decays_monotonically():
    t = np.arange(0, 60, dtype=float)
    rates = _hyperbolic(t, qi=1000.0, Di=0.10, b=1.0)
    assert np.all(np.diff(rates) < 0)


def test_exponential_at_t0_equals_qi():
    assert _exponential(0.0, qi=500.0, Di=0.05) == pytest.approx(500.0)


def test_exponential_half_life():
    # exp(-Di * t) = 0.5 → t = ln(2) / Di
    Di = 0.05
    t_half = np.log(2) / Di
    assert _exponential(t_half, qi=500.0, Di=Di) == pytest.approx(250.0)


# ── Ramp regression (fix in commit d2c7304) ────────────────────────────────

def _rates(volumes, days=30.44):
    return np.asarray(volumes) / days


def test_ramp_months_1_starts_at_qi_not_q_ramp():
    # Regression: with ramp_months=1 (the documented default), the first month
    # must equal qi. Pre-fix it was q_ramp (a one-month "dip" before decline).
    vol = generate_stream_profile(
        qi=1000.0, di_annual=0.80, b=1.0, dt_annual=0.06,
        ramp_months=1, n_months=4, q_ramp=200.0,
    )
    assert _rates(vol)[0] == pytest.approx(1000.0)


def test_ramp_months_0_treated_as_no_ramp():
    vol = generate_stream_profile(
        qi=1000.0, di_annual=0.80, b=1.0, dt_annual=0.06,
        ramp_months=0, n_months=3, q_ramp=200.0,
    )
    assert _rates(vol)[0] == pytest.approx(1000.0)


def test_ramp_months_3_linear_interpolation():
    # q_ramp=200, qi=1000, 3-month ramp: expect 200, 600, 1000 across months 0,1,2.
    vol = generate_stream_profile(
        qi=1000.0, di_annual=0.80, b=1.0, dt_annual=0.06,
        ramp_months=3, n_months=6, q_ramp=200.0,
    )
    r = _rates(vol)
    assert r[0] == pytest.approx(200.0)
    assert r[1] == pytest.approx(600.0)
    assert r[2] == pytest.approx(1000.0)
    # Month 3 should be hyperbolic at t=1, strictly less than qi
    assert r[3] < r[2]


def test_ramp_months_2_endpoints():
    vol = generate_stream_profile(
        qi=1000.0, di_annual=0.80, b=1.0, dt_annual=0.06,
        ramp_months=2, n_months=4, q_ramp=200.0,
    )
    r = _rates(vol)
    assert r[0] == pytest.approx(200.0)
    assert r[1] == pytest.approx(1000.0)


def test_project_monthly_volumes_first_month_is_decline_not_qi():
    # project_monthly_volumes starts the time index at 1, so month 0 of the
    # returned array is _hyperbolic(1, qi, Di, b), strictly < qi.
    vol = project_monthly_volumes(qi=1000.0, Di_monthly=0.10, b=1.0, n_months=3)
    r = _rates(vol)
    assert r[0] < 1000.0
    assert r[0] == pytest.approx(1000.0 / (1.0 + 1.0 * 0.10 * 1.0), rel=1e-6)


# ── Fit roundtrip ──────────────────────────────────────────────────────────

def test_fit_decline_recovers_synthetic_parameters():
    qi_true, Di_true, b_true = 1500.0, 0.08, 1.2
    months = np.arange(24, dtype=float)
    rates = _hyperbolic(months, qi=qi_true, Di=Di_true, b=b_true)
    fit = fit_decline(rates, months)
    assert fit["success"]
    assert fit["qi"] == pytest.approx(qi_true, rel=0.05)
    assert fit["Di_monthly"] == pytest.approx(Di_true, rel=0.05)
    assert fit["b"] == pytest.approx(b_true, rel=0.05)


def test_fit_decline_fails_gracefully_on_too_few_points():
    rates = np.array([100.0, 90.0, 80.0])  # < MIN_MONTHS_FOR_FIT
    fit = fit_decline(rates)
    assert not fit["success"]
    assert fit["warning"] is not None


# ── Unit conversions ───────────────────────────────────────────────────────

def test_di_conversion_roundtrip():
    for de in (0.10, 0.30, 0.65, 0.90):
        for b in (0.5, 1.0, 1.5):
            nominal = effective_annual_to_nominal_annual(de, b)
            back = nominal_annual_to_effective_annual(nominal, b)
            assert back == pytest.approx(de, abs=1e-6), f"de={de} b={b}"
