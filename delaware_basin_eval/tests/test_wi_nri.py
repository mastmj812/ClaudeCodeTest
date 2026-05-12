"""
Per-well WI/NRI overrides — verify the override hierarchy:
  explicit per-well value > cfg default > module fallback

WI scales LOE for existing wells and LOE+D&C for undrilled wells.
NRI scales gross revenue (used inside revenue.calc_monthly_revenue).
"""

import numpy as np
import pandas as pd
import pytest

from economics.revenue import calc_monthly_revenue
from economics.cashflow import (
    build_existing_well_cashflow,
    build_undrilled_well_cashflow,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _cfg(**overrides) -> dict:
    cfg = dict(
        oil_price=70.0,
        gas_price=2.50,
        ngl_yield=0.0,
        ngl_price=0.0,
        nri=0.75,
        wi=1.00,
        oil_severance=0.0,
        gas_severance=0.0,
        ad_valorem=0.0,
        loe_oil=2.50,
        loe_gas=0.30,
        loe_water=1.25,
        loe_fixed=2000.0,
        lateral_length=10_000,
        dc_costs={"Wolfcamp A": 10.0},
        wor=1.5,
    )
    cfg.update(overrides)
    return cfg


def _fake_decline_result(qi=1000.0):
    return {
        "qi": qi,
        "Di_monthly": 0.05,
        "b": 1.0,
        "success": True,
    }


def _fake_hist(n_months=12) -> pd.DataFrame:
    return pd.DataFrame({
        "prod_date":  pd.date_range("2024-01-01", periods=n_months, freq="MS"),
        "oil_bbl":    np.full(n_months, 1000.0),
        "gas_mcf":    np.full(n_months, 2000.0),
        "water_bbl":  np.full(n_months, 500.0),
        "days_on":    np.full(n_months, 30.0),
    })


def _undrilled_profiles(n=12):
    return (
        np.full(n, 100.0),  # oil
        np.zeros(n),
        np.zeros(n),
    )


# ── Revenue NRI override ───────────────────────────────────────────────────

def test_revenue_nri_kwarg_overrides_cfg():
    cfg = _cfg(nri=0.75)
    base = calc_monthly_revenue(np.array([100.0]), np.array([0.0]), cfg)
    half = calc_monthly_revenue(np.array([100.0]), np.array([0.0]), cfg, nri=0.50)
    # Lower NRI → lower net revenue. With severance=0 and ad_valorem=0:
    # net = oil * price * nri
    assert base["net_revenue"][0] == pytest.approx(100.0 * 70.0 * 0.75)
    assert half["net_revenue"][0] == pytest.approx(100.0 * 70.0 * 0.50)


def test_revenue_nri_none_uses_cfg():
    cfg = _cfg(nri=0.65)
    r_none = calc_monthly_revenue(np.array([100.0]), np.array([0.0]), cfg, nri=None)
    r_cfg  = calc_monthly_revenue(np.array([100.0]), np.array([0.0]), cfg)
    assert np.allclose(r_none["net_revenue"], r_cfg["net_revenue"])


# ── Undrilled cashflow ─────────────────────────────────────────────────────

def test_undrilled_wi_scales_dc_capex():
    oil, gas, water = _undrilled_profiles()
    cf_full, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=1.0), "Wolfcamp A")
    cf_half, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=0.5), "Wolfcamp A")
    # cf[0] is the negative D&C capex
    assert cf_half[0] == pytest.approx(cf_full[0] / 2.0)


def test_undrilled_wi_kwarg_overrides_cfg():
    oil, gas, water = _undrilled_profiles()
    cf_cfg,  _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=1.0), "Wolfcamp A")
    cf_over, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=1.0), "Wolfcamp A", wi=0.5)
    assert cf_over[0] == pytest.approx(cf_cfg[0] / 2.0)


def test_undrilled_nri_kwarg_lowers_revenue():
    # Zero out LOE so net cashflow == revenue; then halving NRI halves net.
    cfg = _cfg(loe_oil=0.0, loe_gas=0.0, loe_water=0.0, loe_fixed=0.0)
    oil, gas, water = _undrilled_profiles()
    cf_full, _ = build_undrilled_well_cashflow(oil, gas, water, cfg, "Wolfcamp A", nri=1.0)
    cf_half, _ = build_undrilled_well_cashflow(oil, gas, water, cfg, "Wolfcamp A", nri=0.5)
    assert cf_half[1] == pytest.approx(cf_full[1] / 2.0)


def test_undrilled_nan_wi_falls_back_to_cfg():
    oil, gas, water = _undrilled_profiles()
    cf_nan, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=0.5), "Wolfcamp A", wi=float("nan"))
    cf_cfg, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(wi=0.5), "Wolfcamp A")
    assert cf_nan[0] == pytest.approx(cf_cfg[0])
    assert cf_nan[1] == pytest.approx(cf_cfg[1])


def test_undrilled_loe_scaled_by_wi():
    # Isolate LOE by zeroing prices so the only cash effect is -LOE * WI
    oil, gas, water = _undrilled_profiles(n=6)
    cfg = _cfg(oil_price=0.0, gas_price=0.0, ngl_yield=0.0,
               loe_oil=5.0, loe_gas=0.0, loe_water=0.0, loe_fixed=0.0)
    cf_full, _ = build_undrilled_well_cashflow(oil, gas, water, cfg, "Wolfcamp A", wi=1.0)
    cf_half, _ = build_undrilled_well_cashflow(oil, gas, water, cfg, "Wolfcamp A", wi=0.5)
    # Net = -(LOE * WI). Half WI → half LOE → half negative impact.
    assert cf_full[1] == pytest.approx(-100.0 * 5.0)        # 100 bbl * $5/bbl
    assert cf_half[1] == pytest.approx(-100.0 * 5.0 * 0.5)


# ── Existing-well cashflow ─────────────────────────────────────────────────

def test_existing_wi_scales_loe_only_not_revenue():
    cfg = _cfg(nri=1.0)  # full NRI keeps revenue clean
    decline = _fake_decline_result()
    hist = _fake_hist()
    cf_full, _ = build_existing_well_cashflow(decline, hist, cfg, wi=1.0, nri=1.0)
    cf_half, _ = build_existing_well_cashflow(decline, hist, cfg, wi=0.5, nri=1.0)
    # Per-month cashflow: net_revenue - (LOE * WI). Revenue stays the same;
    # only the LOE component shrinks at lower WI → net is *higher* (less LOE
    # being subtracted).
    assert cf_half[0] > cf_full[0]


def test_existing_nri_scales_revenue():
    cfg = _cfg()
    decline = _fake_decline_result()
    hist = _fake_hist()
    cf_full, _ = build_existing_well_cashflow(decline, hist, cfg, wi=1.0, nri=1.0)
    cf_half, _ = build_existing_well_cashflow(decline, hist, cfg, wi=1.0, nri=0.5)
    # Lower NRI means a smaller gross revenue minus the same LOE; net should be lower.
    assert cf_half[0] < cf_full[0]


def test_existing_nan_overrides_fall_back_to_cfg():
    cfg = _cfg(wi=0.75, nri=0.80)
    decline = _fake_decline_result()
    hist = _fake_hist()
    cf_nan, _ = build_existing_well_cashflow(decline, hist, cfg,
                                             wi=float("nan"), nri=float("nan"))
    cf_cfg, _ = build_existing_well_cashflow(decline, hist, cfg)
    assert np.allclose(cf_nan, cf_cfg)
