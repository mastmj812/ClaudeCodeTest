"""
Undrilled cashflow: capex prepend, lateral scaling, and the D&C fallback
warning surfaced when a formation is missing from cfg["dc_costs"] (added in
commit d2c7304 as part of the silent-fallback cleanup).
"""

import numpy as np
import pytest

from economics.cashflow import build_undrilled_well_cashflow, DC_FALLBACK_MM


def _cfg(**overrides) -> dict:
    cfg = dict(
        oil_price=70.0,
        gas_price=2.50,
        ngl_yield=0.0,
        ngl_price=0.0,
        nri=1.0,
        oil_severance=0.0,
        gas_severance=0.0,
        ad_valorem=0.0,
        loe_oil=0.0,
        loe_gas=0.0,
        loe_water=0.0,
        loe_fixed=0.0,
        lateral_length=10_000,
        dc_costs={"Wolfcamp A": 10.0},
    )
    cfg.update(overrides)
    return cfg


def _profiles(n=12, oil=100.0):
    """Flat 12-month profile of oil only — clean numbers for assertions."""
    return (
        np.full(n, oil),    # oil_bbl
        np.zeros(n),        # gas_mcf
        np.zeros(n),        # water_bbl
    )


def test_returns_tuple_of_cashflow_and_warnings():
    oil, gas, water = _profiles()
    cf, warnings = build_undrilled_well_cashflow(oil, gas, water, _cfg(), "Wolfcamp A")
    assert isinstance(cf, np.ndarray)
    assert isinstance(warnings, list)


def test_capex_prepended_at_index_0():
    oil, gas, water = _profiles()
    cf, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(), "Wolfcamp A")
    # First entry is -D&C in dollars
    assert cf[0] == pytest.approx(-10.0 * 1_000_000)


def test_dc_fallback_emits_warning_for_unknown_formation():
    # "Mystery Bench" is not in cfg["dc_costs"]; should fall back and warn.
    oil, gas, water = _profiles()
    cf, warnings = build_undrilled_well_cashflow(oil, gas, water, _cfg(), "Mystery Bench")
    assert any(w.startswith("dc_fallback:Mystery Bench") for w in warnings)
    assert cf[0] == pytest.approx(-DC_FALLBACK_MM * 1_000_000)


def test_dc_no_warning_when_formation_present():
    oil, gas, water = _profiles()
    _, warnings = build_undrilled_well_cashflow(oil, gas, water, _cfg(), "Wolfcamp A")
    assert all(not w.startswith("dc_fallback:") for w in warnings)


def test_lateral_length_scales_revenue():
    # Half-length lateral → half the oil per month → half the post-capex cashflow.
    oil, gas, water = _profiles(n=12, oil=100.0)
    cf_full,  _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(lateral_length=10_000), "Wolfcamp A")
    cf_half,  _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(lateral_length=5_000),  "Wolfcamp A")
    # Skip index 0 (capex, identical) — compare a representative month
    assert cf_half[1] == pytest.approx(cf_full[1] / 2.0)


def test_post_capex_revenue_matches_oil_price_at_full_nri():
    # 100 bbl/mo * $70/bbl = $7000/mo gross. Net = same with NRI=1, no taxes, no LOE.
    oil, gas, water = _profiles(n=12, oil=100.0)
    cf, _ = build_undrilled_well_cashflow(oil, gas, water, _cfg(), "Wolfcamp A")
    # cf[0] is capex; cf[1] is month-0 net.
    assert cf[1] == pytest.approx(7000.0)


def test_missing_dc_costs_dict_uses_fallback():
    # Defensive: cfg without "dc_costs" at all should not raise.
    oil, gas, water = _profiles()
    cfg = _cfg()
    cfg.pop("dc_costs")
    cf, warnings = build_undrilled_well_cashflow(oil, gas, water, cfg, "Wolfcamp A")
    assert any(w.startswith("dc_fallback:") for w in warnings)
    assert cf[0] == pytest.approx(-DC_FALLBACK_MM * 1_000_000)
