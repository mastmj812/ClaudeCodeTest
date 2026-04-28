"""
Monthly cash flow builder for both existing and undrilled wells.
"""

import numpy as np
import pandas as pd
from engineering.decline import project_monthly_volumes, fit_decline
from economics.revenue import calc_monthly_revenue
from config import MAX_PROJECTION_MONTHS


def build_existing_well_cashflow(
    decline_result: dict,
    prod_df_for_well: pd.DataFrame,
    cfg: dict,
    days_per_month: float = 30.44,
) -> np.ndarray:
    """
    Build a monthly net cash flow array for an existing well.

    Uses actual production for past months, then the fitted decline
    projection for future months.

    Returns a 1-D array of net monthly cash flows ($).
    """
    if not decline_result.get("success"):
        return np.array([])

    # ── Historical production (actual) ────────────────────────────────────
    hist = prod_df_for_well.sort_values("prod_date").copy()
    hist_filtered = hist[hist["days_on"].fillna(0) >= 15]
    if not hist_filtered.empty:
        hist = hist_filtered
    oil_hist = hist["oil_bbl"].fillna(0).values
    gas_hist = hist["gas_mcf"].fillna(0).values
    rev_hist  = calc_monthly_revenue(oil_hist, gas_hist, cfg)
    boe_hist  = oil_hist + gas_hist / 6.0
    loe_hist  = boe_hist * cfg["loe_per_boe"]
    net_hist  = rev_hist["net_revenue"] - loe_hist

    # ── Projected production (future) ─────────────────────────────────────
    qi  = decline_result["qi"]
    Di  = decline_result["Di_monthly"]
    b   = decline_result["b"]
    n_proj = MAX_PROJECTION_MONTHS

    oil_proj = project_monthly_volumes(qi, Di, b, n_proj, days_per_month)
    # Estimate gas from historical GOR
    if hist["oil_bbl"].sum() > 0:
        gor = hist["gas_mcf"].sum() / hist["oil_bbl"].sum()
    else:
        gor = 1.5  # MCF/BBL default
    gas_proj = oil_proj * gor

    rev_proj  = calc_monthly_revenue(oil_proj, gas_proj, cfg)
    boe_proj  = oil_proj + gas_proj / 6.0
    loe_proj  = boe_proj * cfg["loe_per_boe"]
    net_proj  = rev_proj["net_revenue"] - loe_proj

    # Trim trailing zeros
    last_nonzero = np.nonzero(oil_proj)[0]
    if len(last_nonzero):
        net_proj = net_proj[: last_nonzero[-1] + 1]

    return np.concatenate([net_hist, net_proj])


def build_undrilled_well_cashflow(
    type_curve_p50: np.ndarray,
    cfg: dict,
    formation: str,
    days_per_month: float = 30.44,
) -> np.ndarray:
    """
    Build a monthly cash flow for an undrilled well using the P50 type curve.

    type_curve_p50: daily oil rates (BOPD / 10,000 ft lateral), array by month.
    The lateral length in cfg is used to scale back to actual expected rates.
    D&C capex is included as a negative cash flow at month 0.

    Returns a 1-D array starting with -dc_cost at index 0.
    """
    lateral = cfg.get("lateral_length", 10_000)
    scale   = lateral / 10_000.0
    oil_rates_daily = np.asarray(type_curve_p50, dtype=float) * scale  # BOPD

    # Daily rate → monthly volume
    oil_bbl = oil_rates_daily * days_per_month

    # Gas GOR: use a typical Delaware Basin default (1.5 MCF/BBL) if not provided
    gor = cfg.get("type_curve_gor", 1.5)
    gas_mcf = oil_bbl * gor

    rev  = calc_monthly_revenue(oil_bbl, gas_mcf, cfg)
    boe  = oil_bbl + gas_mcf / 6.0
    loe  = boe * cfg["loe_per_boe"]
    net  = rev["net_revenue"] - loe

    # Prepend D&C capex (in $MM → convert to $)
    dc_cost = cfg["dc_costs"].get(formation, 10.0) * 1_000_000
    cashflows = np.concatenate([[-dc_cost], net])

    return cashflows
