"""
Revenue math: oil/gas/NGL gross, severance, ad valorem, NRI, and a regression
test for the gas-units fix in commit d2c7304 (line 31 was dead code overwritten
by line 33; the surviving math is $/MCF, not $/MMBTU with a BTU factor).
"""

import numpy as np
import pytest

from economics.revenue import calc_monthly_revenue


def _base_cfg(**overrides) -> dict:
    cfg = dict(
        oil_price=70.0,
        gas_price=2.50,
        ngl_yield=25.0,    # BBL / MMCF
        ngl_price=22.0,
        nri=1.0,
        oil_severance=0.0,
        gas_severance=0.0,
        ad_valorem=0.0,
    )
    cfg.update(overrides)
    return cfg


def test_gas_price_treated_as_per_mcf():
    # Regression: gas revenue = gas_mcf * gas_price (no BTU factor)
    r = calc_monthly_revenue(
        oil_bbl=np.array([0.0]),
        gas_mcf=np.array([1000.0]),
        cfg=_base_cfg(),
    )
    assert r["gross_gas_rev"][0] == pytest.approx(1000.0 * 2.50)


def test_oil_revenue_at_full_nri():
    r = calc_monthly_revenue(
        oil_bbl=np.array([100.0]),
        gas_mcf=np.array([0.0]),
        cfg=_base_cfg(),
    )
    assert r["gross_oil_rev"][0] == pytest.approx(7000.0)
    assert r["net_revenue"][0] == pytest.approx(7000.0)


def test_ngl_yield_uses_mcf_to_mmcf_conversion():
    # 1000 MCF = 1 MMCF; at 25 BBL/MMCF that's 25 BBL of NGL.
    r = calc_monthly_revenue(
        oil_bbl=np.array([0.0]),
        gas_mcf=np.array([1000.0]),
        cfg=_base_cfg(),
    )
    assert r["ngl_bbl"][0] == pytest.approx(25.0)
    assert r["gross_ngl_rev"][0] == pytest.approx(25.0 * 22.0)


def test_nri_scales_total_revenue_only():
    # With severance=0, ad_valorem=0, net = gross_total * nri.
    cfg = _base_cfg(nri=0.75)
    r = calc_monthly_revenue(
        oil_bbl=np.array([100.0]),
        gas_mcf=np.array([0.0]),
        cfg=cfg,
    )
    assert r["net_revenue"][0] == pytest.approx(7000.0 * 0.75)


def test_severance_applied_to_gross_revenue_then_scaled_by_nri():
    cfg = _base_cfg(nri=1.0, oil_severance=0.046)
    r = calc_monthly_revenue(
        oil_bbl=np.array([100.0]),
        gas_mcf=np.array([0.0]),
        cfg=cfg,
    )
    expected_sev = 7000.0 * 0.046 * 1.0
    expected_net = 7000.0 - expected_sev
    assert r["severance"][0] == pytest.approx(expected_sev)
    assert r["net_revenue"][0] == pytest.approx(expected_net)


def test_ad_valorem_applied_to_net_of_nri():
    cfg = _base_cfg(nri=0.75, ad_valorem=0.01)
    r = calc_monthly_revenue(
        oil_bbl=np.array([100.0]),
        gas_mcf=np.array([0.0]),
        cfg=cfg,
    )
    gross_after_nri = 7000.0 * 0.75
    expected_av = gross_after_nri * 0.01
    expected_net = gross_after_nri - expected_av
    assert r["ad_valorem"][0] == pytest.approx(expected_av)
    assert r["net_revenue"][0] == pytest.approx(expected_net)


def test_arrays_are_broadcastable_length():
    # Length is preserved across all output arrays
    n = 24
    r = calc_monthly_revenue(
        oil_bbl=np.full(n, 100.0),
        gas_mcf=np.full(n, 500.0),
        cfg=_base_cfg(),
    )
    for key in ("gross_oil_rev", "gross_gas_rev", "gross_ngl_rev",
                "severance", "ad_valorem", "net_revenue", "ngl_bbl"):
        assert len(r[key]) == n, f"length mismatch on {key}"
