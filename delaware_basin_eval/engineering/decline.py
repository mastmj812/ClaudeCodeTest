"""
Arps hyperbolic decline curve fitting and EUR projection.
"""

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from config import (
    B_FACTOR_CAP, TERMINAL_DI_ANNUAL, ECONOMIC_LIMIT_BOPD,
    MAX_PROJECTION_MONTHS, MIN_MONTHS_FOR_FIT,
)


def _hyperbolic(t: np.ndarray, qi: float, Di: float, b: float) -> np.ndarray:
    """Arps hyperbolic rate equation. t in months, Di is monthly nominal."""
    return qi / (1.0 + b * Di * t) ** (1.0 / b)


def _exponential(t: np.ndarray, qi: float, Di: float) -> np.ndarray:
    """Arps exponential rate. t in months, Di is monthly nominal."""
    return qi * np.exp(-Di * t)


def fit_decline(monthly_rates: np.ndarray, months: np.ndarray | None = None) -> dict:
    """
    Fit an Arps hyperbolic decline to monthly oil rate data.

    Parameters
    ----------
    monthly_rates : array of daily oil rates (BOPD), one per month
    months        : optional month indices (0-based); defaults to 0,1,2,...

    Returns dict with:
      qi, Di_monthly, b, success, warning, fit_rates, proj_months, proj_rates, eur
    """
    rates = np.asarray(monthly_rates, dtype=float)
    if months is None:
        months = np.arange(len(rates), dtype=float)

    result = {
        "qi": float(np.nanmax(rates)) if len(rates) else 0.0,
        "Di_monthly": 0.10,
        "b": 1.2,
        "success": False,
        "warning": None,
        "fit_rates": None,
        "proj_months": None,
        "proj_rates": None,
        "eur": 0.0,
    }

    valid_mask = np.isfinite(rates) & (rates > 0)
    if valid_mask.sum() < MIN_MONTHS_FOR_FIT:
        result["warning"] = f"Insufficient data ({valid_mask.sum()} valid months < {MIN_MONTHS_FOR_FIT})"
        return result

    t_fit = months[valid_mask].astype(float)
    q_fit = rates[valid_mask].astype(float)

    # Normalize t to start at 0
    t0    = t_fit[0]
    t_fit = t_fit - t0
    qi0   = q_fit[0]

    try:
        popt, _ = curve_fit(
            _hyperbolic,
            t_fit, q_fit,
            p0=[qi0, 0.10, 1.2],
            bounds=([0, 1e-6, 0.01], [qi0 * 5, 0.99, 2.0]),
            maxfev=10_000,
        )
        qi, Di, b = popt
    except Exception as e:
        result["warning"] = f"Curve fit failed: {e}"
        return result

    # Clamp b
    warn_b = None
    if b > 1.5:
        warn_b = f"b-factor = {b:.2f} (>1.5, clamped to {B_FACTOR_CAP})"
    if b > B_FACTOR_CAP:
        b = B_FACTOR_CAP
        try:
            popt2, _ = curve_fit(
                lambda t, qi, Di: _hyperbolic(t, qi, Di, b),
                t_fit, q_fit,
                p0=[qi0, 0.10],
                bounds=([0, 1e-6], [qi0 * 5, 0.99]),
                maxfev=10_000,
            )
            qi, Di = popt2
        except Exception:
            pass

    result.update({"qi": qi, "Di_monthly": Di, "b": b, "success": True, "warning": warn_b})

    # Fitted rates over actual data span
    result["fit_rates"] = _hyperbolic(t_fit, qi, Di, b)

    # Projection from last data month forward
    proj_months, proj_rates = _project(qi, Di, b, t_start=t_fit[-1], t0_offset=t0)
    result["proj_months"] = proj_months
    result["proj_rates"]  = proj_rates
    result["eur"]         = _calc_eur(qi, Di, b, existing_cum=None)

    return result


def _project(
    qi: float,
    Di: float,
    b: float,
    t_start: float = 0.0,
    t0_offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Project production from t_start until economic limit or MAX_PROJECTION_MONTHS.
    Returns (months_absolute, rates).
    """
    economic_limit_daily = ECONOMIC_LIMIT_BOPD  # BOPD
    terminal_Di_monthly  = TERMINAL_DI_ANNUAL / 12.0

    months_rel = []
    rates_out  = []
    t = t_start

    # Find when hyperbolic Di drops to terminal
    # Di_instantaneous(t) = Di / (1 + b*Di*t)
    # Set equal to terminal_Di → solve for t_switch
    if b > 0 and Di > terminal_Di_monthly:
        t_switch = (Di / terminal_Di_monthly - 1.0) / (b * Di)
    else:
        t_switch = float("inf")

    for _ in range(MAX_PROJECTION_MONTHS):
        t += 1.0
        if t <= t_switch:
            rate = _hyperbolic(t, qi, Di, b)
        else:
            # Switch to exponential with terminal Di
            q_switch = _hyperbolic(t_switch, qi, Di, b)
            rate = _exponential(t - t_switch, q_switch, terminal_Di_monthly)

        if rate < economic_limit_daily:
            break
        months_rel.append(t + t0_offset)
        rates_out.append(rate)

    return np.array(months_rel), np.array(rates_out)


def _calc_eur(qi: float, Di: float, b: float, existing_cum=None) -> float:
    """
    Approximate EUR in total BOE from t=0.
    Uses analytical integral for hyperbolic + exponential tail.
    """
    terminal_Di_monthly = TERMINAL_DI_ANNUAL / 12.0
    if Di <= 0 or b <= 0:
        return 0.0

    if Di > terminal_Di_monthly:
        t_switch = (Di / terminal_Di_monthly - 1.0) / (b * Di)
        q_switch = _hyperbolic(t_switch, qi, Di, b)
        # Integral of hyperbolic from 0 to t_switch:
        # ∫₀ᵀ qi*(1+b*Di*t)^(-1/b) dt = qi/((b-1)*Di) * [(1+b*Di*T)^(1-1/b) - 1]
        cum_hyp = (qi / ((b - 1) * Di)) * (
            (1 + b * Di * t_switch) ** (1 - 1 / b) - 1
        ) * 30.44
        # Integral of exponential from t_switch to ∞
        cum_exp = (q_switch / terminal_Di_monthly) * 30.44
        return cum_hyp + cum_exp
    else:
        # Fully exponential from t=0
        return (qi / Di) * 30.44


def project_monthly_volumes(
    qi: float,
    Di_monthly: float,
    b: float,
    n_months: int,
    days_per_month: float = 30.44,
) -> np.ndarray:
    """
    Return an array of monthly oil volumes (BBL) for n_months starting from t=1.
    Used for cash flow generation.
    """
    terminal_Di_monthly = TERMINAL_DI_ANNUAL / 12.0
    t_switch = float("inf")
    if b > 0 and Di_monthly > terminal_Di_monthly:
        t_switch = (Di_monthly / terminal_Di_monthly - 1.0) / (b * Di_monthly)

    volumes = []
    for t in range(1, n_months + 1):
        if t <= t_switch:
            rate = _hyperbolic(float(t), qi, Di_monthly, b)
        else:
            q_switch = _hyperbolic(t_switch, qi, Di_monthly, b)
            rate = _exponential(float(t) - t_switch, q_switch, terminal_Di_monthly)
        vol = max(rate, 0.0) * days_per_month
        volumes.append(vol)
        if rate < ECONOMIC_LIMIT_BOPD:
            break

    # Pad to n_months with zeros
    while len(volumes) < n_months:
        volumes.append(0.0)
    return np.array(volumes[:n_months])


def generate_stream_profile(
    qi: float,
    di_annual: float,
    b: float,
    dt_annual: float,
    ramp_months: int,
    n_months: int,
    q_ramp: float = 0.0,
    days_per_month: float = 30.44,
) -> np.ndarray:
    """
    Generate monthly volumes for one stream (oil BBL, gas MCF, or water BBL).

    Parameters
    ----------
    qi          : peak daily rate (BOPD, MCF/d, or BWPD) at start of decline
    di_annual   : initial nominal annual decline rate (decimal, e.g. 0.80)
    b           : Arps b-factor
    dt_annual   : terminal annual decline rate (decimal) — switches from hyperbolic
                  to exponential when instantaneous Di reaches this level
    ramp_months : months in the linear ramp from q_ramp → qi before decline begins
    q_ramp      : starting rate at month 0 of the ramp (0 = well comes on at zero)
    n_months    : total profile length to return
    days_per_month : days per month for rate→volume conversion

    Returns
    -------
    np.ndarray of shape (n_months,) — monthly volumes
    """
    Di_monthly = di_annual / 12.0
    terminal_Di_monthly = dt_annual / 12.0

    t_switch = float("inf")
    if b > 0 and Di_monthly > terminal_Di_monthly:
        t_switch = (Di_monthly / terminal_Di_monthly - 1.0) / (b * Di_monthly)

    volumes = []
    decline_t = 0.0  # time within the decline segment (starts after ramp)

    for month in range(n_months):
        if month < ramp_months:
            # Linear ramp: q_ramp at month 0, qi at month (ramp_months-1)
            frac = month / max(ramp_months - 1, 1)
            rate = q_ramp + (qi - q_ramp) * frac
        else:
            decline_t += 1.0
            if decline_t <= t_switch:
                rate = _hyperbolic(decline_t, qi, Di_monthly, b)
            else:
                q_switch = _hyperbolic(t_switch, qi, Di_monthly, b)
                rate = _exponential(decline_t - t_switch, q_switch, terminal_Di_monthly)
            rate = max(rate, 0.0)
            if rate < ECONOMIC_LIMIT_BOPD:
                rate = 0.0

        volumes.append(rate * days_per_month)

    return np.array(volumes)


def fit_all_section_wells(
    section_wells: pd.DataFrame,
    section_prod: pd.DataFrame,
    days_per_month: float = 30.44,
) -> list[dict]:
    """
    Fit decline curves for every well in section_wells.
    Returns a list of result dicts (one per well), each including
    well metadata from section_wells.
    """
    results = []
    for _, well in section_wells.iterrows():
        api  = well["api"]
        wprod = section_prod[section_prod["api"] == api].sort_values("prod_date")

        base = {
            "api":         api,
            "well_name":   well.get("well_name", api),
            "formation":   well.get("formation", "Unknown"),
            "lateral_length": well.get("lateral_length", None),
            "first_prod_date": well.get("first_prod_date", None),
        }

        if wprod.empty:
            base.update({"success": False, "warning": "No production data", "eur": 0.0,
                         "qi": 0.0, "Di_monthly": 0.0, "b": 0.0,
                         "fit_rates": None, "proj_months": None, "proj_rates": None,
                         "actual_months": [], "actual_rates": []})
            results.append(base)
            continue

        # Use only months with enough days on production
        valid = wprod[wprod["days_on"].fillna(0) >= 15].copy()
        if valid.empty:
            valid = wprod.copy()

        rates  = valid["daily_oil_rate"].values
        months = np.arange(len(rates), dtype=float)

        fit    = fit_decline(rates, months)
        base.update(fit)
        base["actual_months"] = list(months)
        base["actual_rates"]  = list(rates)
        results.append(base)

    return results
