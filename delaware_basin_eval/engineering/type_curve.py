"""
Type curve construction from offset wells.

Workflow:
  1. Filter offset wells (get_offset_wells — unchanged)
  2. Fit Arps decline to each well for oil, gas, and water independently
  3. Normalize qi to 10,000 ft lateral for each stream
  4. Compute P10/P50/P90 per month for oil; P50 for gas and water
  5. Return suggested_params (median fits) as starting point for UI editing
"""

import io
import numpy as np
import pandas as pd
from engineering.normalization import normalize_production
from engineering.decline import fit_decline, generate_stream_profile
from utils.geo import haversine_miles
from config import NORM_LATERAL_FT, TERMINAL_DI_ANNUAL, B_FACTOR_CAP, MIN_MONTHS_FOR_FIT


def get_offset_wells(
    wells_df: pd.DataFrame,
    formation_names: list,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    max_well_age_yr: int,
    section_apis: set | None = None,
) -> pd.DataFrame:
    """
    Filter wells_df to offset type-curve candidates:
      - Formation is in formation_names (user-selected raw names)
      - First production date within the last max_well_age_yr years
      - Within radius_miles of center_lat/center_lon
      - Not in section_apis (exclude in-section wells from comps)
      - Has a valid lateral_length >= MIN_LATERAL_FT
    """
    from config import MIN_LATERAL_FT

    df = wells_df.copy()
    counts = {"total": len(df)}

    df = df[df["formation"].fillna("").isin(formation_names)]
    counts["after_formation"] = len(df)

    cutoff = pd.Timestamp.now() - pd.DateOffset(years=max_well_age_yr)
    if "first_prod_date" in df.columns:
        df = df[df["first_prod_date"].notna() & (df["first_prod_date"] >= cutoff)]
    counts["after_age"] = len(df)

    if section_apis:
        df = df[~df["api"].isin(section_apis)]
    counts["after_section_exclude"] = len(df)

    if "lateral_length" in df.columns:
        df = df[df["lateral_length"].fillna(0) >= MIN_LATERAL_FT]
    counts["after_lateral"] = len(df)

    valid = df.dropna(subset=["latitude", "longitude"])
    if valid.empty:
        valid.attrs["filter_counts"] = counts
        return valid

    dists = haversine_miles(center_lat, center_lon, valid["latitude"].values, valid["longitude"].values)
    valid = valid[dists <= radius_miles].copy()
    counts["after_radius"] = len(valid)

    valid.attrs["filter_counts"] = counts
    return valid.reset_index(drop=True)


def build_type_curve(
    offset_wells: pd.DataFrame,
    prod_df: pd.DataFrame,
    max_months: int = 120,
) -> dict:
    """
    Build a type curve from offset wells using per-well Arps fits for all 3 streams.

    Returns dict with:
      p10, p50, p90       : oil percentile arrays (BOPD / 10k ft, shape max_months)
      gas_p50, water_p50  : gas/water P50 arrays (MCF/d and BWPD per 10k ft)
      traces              : per-well normalized oil traces for chart
      n_wells, excluded, median_lateral
      suggested_params    : median fitted params per stream — starting point for UI
    """
    empty = np.full(max_months, np.nan)
    empty_result = {
        "p10": empty.copy(), "p50": empty.copy(), "p90": empty.copy(),
        "gas_p50": empty.copy(), "water_p50": empty.copy(),
        "cum_p10": empty.copy(), "cum_p50": empty.copy(), "cum_p90": empty.copy(),
        "cum_gas_p50": empty.copy(), "cum_water_p50": empty.copy(),
        "traces": [], "n_wells": 0, "excluded": 0, "median_lateral": 0.0,
        "suggested_params": _default_suggested_params(),
    }

    if offset_wells.empty:
        return empty_result

    # Per-well fit results keyed by stream
    oil_fits, gas_fits, water_fits = [], [], []
    oil_matrix   = np.full((len(offset_wells), max_months), np.nan)
    gas_matrix   = np.full((len(offset_wells), max_months), np.nan)
    water_matrix = np.full((len(offset_wells), max_months), np.nan)
    traces = []
    excluded = 0
    laterals = []

    for idx, (_, well) in enumerate(offset_wells.iterrows()):
        api    = well["api"]
        lat_ft = well.get("lateral_length", np.nan)

        wprod = prod_df[prod_df["api"] == api].sort_values("prod_date")
        wprod_filtered = wprod[wprod["days_on"].fillna(0) >= 15].copy()
        if not wprod_filtered.empty:
            wprod = wprod_filtered

        if wprod.empty:
            excluded += 1
            continue

        days = wprod["days_on"].fillna(30.44).replace(0, 30.44).values.astype(float)
        months = np.arange(len(days), dtype=float)

        # Oil
        oil_rates = wprod["daily_oil_rate"].fillna(0).values.astype(float)
        if oil_rates.max() <= 0:
            excluded += 1
            continue

        norm_oil = normalize_production(oil_rates, lat_ft)
        if norm_oil is None:
            excluded += 1
            continue

        n = min(len(norm_oil), max_months)
        oil_matrix[idx, :n] = norm_oil[:n]
        laterals.append(lat_ft)

        # Skip month 0 for fitting when possible — first prod month is often
        # partial (1-31 days) and inflates or deflates the apparent qi.
        if len(oil_rates) > MIN_MONTHS_FOR_FIT:
            oil_fit = fit_decline(oil_rates[1:], months[1:])
        else:
            oil_fit = fit_decline(oil_rates, months)
        if oil_fit["success"]:
            norm_qi = oil_fit["qi"] * (NORM_LATERAL_FT / lat_ft)
            oil_fits.append({
                "qi": norm_qi,
                "di_annual": oil_fit["Di_monthly"] * 12,
                "b": oil_fit["b"],
            })

        traces.append({
            "well_name": well.get("well_name", api),
            "months":    list(range(n)),
            "rates":     norm_oil[:n].tolist(),
        })

        # Gas
        if "gas_mcf" in wprod.columns:
            gas_rates = (wprod["gas_mcf"].fillna(0) / days).values.astype(float)
            gas_rates = np.clip(gas_rates, 0, None)
            if gas_rates.max() > 0:
                norm_gas = normalize_production(gas_rates, lat_ft)
                if norm_gas is not None:
                    ng = min(len(norm_gas), max_months)
                    gas_matrix[idx, :ng] = norm_gas[:ng]
                    if len(gas_rates) > MIN_MONTHS_FOR_FIT:
                        gas_fit = fit_decline(gas_rates[1:], months[1:])
                    else:
                        gas_fit = fit_decline(gas_rates, months)
                    if gas_fit["success"]:
                        norm_qi_g = gas_fit["qi"] * (NORM_LATERAL_FT / lat_ft)
                        gas_fits.append({
                            "qi": norm_qi_g,
                            "di_annual": gas_fit["Di_monthly"] * 12,
                            "b": gas_fit["b"],
                        })

        # Water
        if "water_bbl" in wprod.columns:
            water_rates = (wprod["water_bbl"].fillna(0) / days).values.astype(float)
            water_rates = np.clip(water_rates, 0, None)
            if water_rates.max() > 0:
                norm_water = normalize_production(water_rates, lat_ft)
                if norm_water is not None:
                    nw = min(len(norm_water), max_months)
                    water_matrix[idx, :nw] = norm_water[:nw]
                    if len(water_rates) > MIN_MONTHS_FOR_FIT:
                        water_fit = fit_decline(water_rates[1:], months[1:])
                    else:
                        water_fit = fit_decline(water_rates, months)
                    if water_fit["success"]:
                        norm_qi_w = water_fit["qi"] * (NORM_LATERAL_FT / lat_ft)
                        water_fits.append({
                            "qi": norm_qi_w,
                            "di_annual": water_fit["Di_monthly"] * 12,
                            "b": water_fit["b"],
                        })

    # Remove all-NaN rows
    has_data = ~np.all(np.isnan(oil_matrix), axis=1)
    oil_matrix   = oil_matrix[has_data]
    gas_matrix   = gas_matrix[has_data]
    water_matrix = water_matrix[has_data]

    if oil_matrix.shape[0] == 0:
        empty_result["traces"] = traces
        empty_result["excluded"] = excluded
        return empty_result

    _dpm = 30.44
    with np.errstate(all="ignore"):
        p10 = np.nanpercentile(oil_matrix,   10, axis=0)
        p50 = np.nanpercentile(oil_matrix,   50, axis=0)
        p90 = np.nanpercentile(oil_matrix,   90, axis=0)
        gas_p50   = np.nanpercentile(gas_matrix,   50, axis=0)
        water_p50 = np.nanpercentile(water_matrix, 50, axis=0)

        # Cumulative per-well arrays (BBL per 10k ft). nancumsum treats NaN
        # as 0 — so wells with shorter histories get an artificially flat tail.
        # Restore NaN at originally-missing positions so nanpercentile
        # correctly drops those wells from per-month statistics.
        oil_nan_mask   = np.isnan(oil_matrix)
        gas_nan_mask   = np.isnan(gas_matrix)
        water_nan_mask = np.isnan(water_matrix)
        cum_oil   = np.nancumsum(oil_matrix   * _dpm, axis=1)
        cum_gas   = np.nancumsum(gas_matrix   * _dpm, axis=1)
        cum_water = np.nancumsum(water_matrix * _dpm, axis=1)
        cum_oil[oil_nan_mask]     = np.nan
        cum_gas[gas_nan_mask]     = np.nan
        cum_water[water_nan_mask] = np.nan
        cum_p10       = np.nanpercentile(cum_oil,   10, axis=0)
        cum_p50       = np.nanpercentile(cum_oil,   50, axis=0)
        cum_p90       = np.nanpercentile(cum_oil,   90, axis=0)
        cum_gas_p50   = np.nanpercentile(cum_gas,   50, axis=0)
        cum_water_p50 = np.nanpercentile(cum_water, 50, axis=0)

    # Smooth all three percentile bands consistently. Smoothing only P50
    # would leave a ragged P10/P90 envelope that can crisscross the median.
    p10 = _rolling_median(p10, window=3)
    p50 = _rolling_median(p50, window=3)
    p90 = _rolling_median(p90, window=3)

    suggested = _derive_suggested_params(oil_fits, gas_fits, water_fits)

    return {
        "p10":             p10,
        "p50":             p50,
        "p90":             p90,
        "gas_p50":         gas_p50,
        "water_p50":       water_p50,
        "cum_p10":         cum_p10,
        "cum_p50":         cum_p50,
        "cum_p90":         cum_p90,
        "cum_gas_p50":     cum_gas_p50,
        "cum_water_p50":   cum_water_p50,
        "traces":          traces,
        "n_wells":         int(oil_matrix.shape[0]),
        "excluded":        excluded,
        "median_lateral":  float(np.median(laterals)) if laterals else 0.0,
        "suggested_params": suggested,
    }


def _default_suggested_params() -> dict:
    return {
        "oil":   {"qi": 500.0, "di_annual": 0.80, "b": 1.2, "dt_annual": TERMINAL_DI_ANNUAL, "q_ramp": 0.0},
        "gas":   {"qi": 750.0, "di_annual": 0.80, "b": 1.2, "dt_annual": TERMINAL_DI_ANNUAL, "q_ramp": 0.0},
        "water": {"qi": 200.0, "di_annual": 0.60, "b": 1.0, "dt_annual": TERMINAL_DI_ANNUAL, "q_ramp": 0.0},
    }


def _derive_suggested_params(
    oil_fits: list, gas_fits: list, water_fits: list
) -> dict:
    def _median_params(fits: list, defaults: dict) -> dict:
        if not fits:
            return defaults.copy()
        return {
            "qi":        float(np.median([f["qi"] for f in fits])),
            "di_annual": float(np.clip(np.median([f["di_annual"] for f in fits]), 0.01, 5.0)),
            "b":         float(np.clip(np.median([f["b"] for f in fits]), 0.01, B_FACTOR_CAP)),
            "dt_annual": TERMINAL_DI_ANNUAL,
            "q_ramp":    0.0,
        }

    defaults = _default_suggested_params()
    return {
        "oil":   _median_params(oil_fits,   defaults["oil"]),
        "gas":   _median_params(gas_fits,   defaults["gas"]),
        "water": _median_params(water_fits, defaults["water"]),
    }


def generate_type_curve_profile(stream_params: dict, n_months: int = 600) -> np.ndarray:
    """
    Generate a monthly volume profile from user-specified stream parameters.

    stream_params keys: qi, di_annual, b, dt_annual, ramp_months
    Returns np.ndarray of monthly volumes (BBL or MCF, per 10k ft lateral).
    """
    return generate_stream_profile(
        qi=stream_params["qi"],
        di_annual=stream_params["di_annual"],
        b=stream_params["b"],
        dt_annual=stream_params["dt_annual"],
        ramp_months=int(stream_params.get("ramp_months", 0)),
        n_months=n_months,
        q_ramp=float(stream_params.get("q_ramp", 0.0)),
    )


def export_type_curve_csv(
    formation: str,
    oil_profile: np.ndarray,
    gas_profile: np.ndarray,
    water_profile: np.ndarray,
    days_per_month: float = 30.44,
) -> str:
    """
    Return a CSV string for the active type curve (normalized to 10,000 ft lateral).

    Columns: month, oil_bopd_10k, gas_mcfd_10k, water_bpd_10k
    Rates are daily averages (volume / days_per_month).
    """
    n = max(len(oil_profile), len(gas_profile), len(water_profile))

    def _pad(arr):
        a = np.array(arr, dtype=float)
        if len(a) < n:
            a = np.concatenate([a, np.zeros(n - len(a))])
        return a

    oil   = _pad(oil_profile)
    gas   = _pad(gas_profile)
    water = _pad(water_profile)

    df = pd.DataFrame({
        "month":         np.arange(1, n + 1),
        "oil_bopd_10k":  np.round(oil / days_per_month, 2),
        "gas_mcfd_10k":  np.round(gas / days_per_month, 2),
        "water_bpd_10k": np.round(water / days_per_month, 2),
    })

    # Trim trailing all-zero rows
    last_nonzero = (df[["oil_bopd_10k", "gas_mcfd_10k", "water_bpd_10k"]].sum(axis=1) > 0)
    if last_nonzero.any():
        df = df.loc[:last_nonzero[last_nonzero].index[-1]]

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _rolling_median(arr: np.ndarray, window: int = 3) -> np.ndarray:
    """Apply a rolling median (centered) to a 1D array, preserving NaN gaps."""
    result = arr.copy()
    half = window // 2
    for i in range(len(arr)):
        start = max(0, i - half)
        end   = min(len(arr), i + half + 1)
        chunk = arr[start:end]
        valid = chunk[~np.isnan(chunk)]
        if len(valid):
            result[i] = np.median(valid)
    return result
