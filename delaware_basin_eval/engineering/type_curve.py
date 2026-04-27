"""
Type curve construction from offset wells.

Workflow:
  1. Filter offset wells (same formation, age < max_yr, within radius)
  2. Normalize each well's production to 10,000 ft lateral
  3. Build NaN matrix aligned to month-0 (first production month)
  4. Compute P10 / P50 / P90 per month using nanpercentile
  5. Smooth P50 with 3-month rolling median
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from engineering.normalization import normalize_production
from utils.geo import haversine_miles


def get_offset_wells(
    wells_df: pd.DataFrame,
    formation: str,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    max_well_age_yr: int,
    section_apis: set | None = None,
) -> pd.DataFrame:
    """
    Filter wells_df to offset type-curve candidates:
      - Same canonical formation (exact match after normalization)
      - First production date within the last max_well_age_yr years
      - Within radius_miles of center_lat/center_lon
      - Not in section_apis (exclude in-section wells from comps)
      - Has a valid lateral_length >= MIN_LATERAL_FT
    """
    from config import MIN_LATERAL_FT

    df = wells_df.copy()

    # Formation filter
    df = df[df["formation"].fillna("") == formation]

    # Age filter
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=max_well_age_yr)
    if "first_prod_date" in df.columns:
        df = df[df["first_prod_date"].notna() & (df["first_prod_date"] >= cutoff)]

    # Exclude in-section wells
    if section_apis:
        df = df[~df["api"].isin(section_apis)]

    # Lateral length filter
    if "lateral_length" in df.columns:
        df = df[df["lateral_length"].fillna(0) >= MIN_LATERAL_FT]

    # Spatial filter
    valid = df.dropna(subset=["latitude", "longitude"])
    if valid.empty:
        return valid

    dists = haversine_miles(center_lat, center_lon, valid["latitude"].values, valid["longitude"].values)
    valid = valid[dists <= radius_miles].copy()

    return valid.reset_index(drop=True)


def build_type_curve(
    offset_wells: pd.DataFrame,
    prod_df: pd.DataFrame,
    max_months: int = 120,
) -> dict:
    """
    Build a type curve from offset wells.

    Returns:
      {
        p10, p50, p90: np.ndarray of shape (max_months,)   — BOPD / 10k ft
        traces: list of {well_name, months, rates}         — individual normalized traces
        n_wells: int,
        excluded: int,
        median_lateral: float,
      }
    """
    if offset_wells.empty:
        empty = np.full(max_months, np.nan)
        return {"p10": empty, "p50": empty, "p90": empty,
                "traces": [], "n_wells": 0, "excluded": 0, "median_lateral": 0.0}

    matrix = np.full((len(offset_wells), max_months), np.nan)
    traces = []
    excluded = 0
    laterals = []

    for idx, (_, well) in enumerate(offset_wells.iterrows()):
        api    = well["api"]
        lat_ft = well.get("lateral_length", np.nan)

        wprod = prod_df[prod_df["api"] == api].sort_values("prod_date")
        wprod = wprod[wprod["days_on"].fillna(0) >= 15]

        if wprod.empty:
            excluded += 1
            continue

        rates = wprod["daily_oil_rate"].fillna(0).values.astype(float)
        if len(rates) == 0 or rates.max() == 0:
            excluded += 1
            continue

        norm_rates = normalize_production(rates, lat_ft)
        if norm_rates is None:
            excluded += 1
            continue

        n = min(len(norm_rates), max_months)
        matrix[idx, :n] = norm_rates[:n]
        laterals.append(lat_ft)

        months = np.arange(n)
        traces.append({
            "well_name": well.get("well_name", api),
            "months":    months.tolist(),
            "rates":     norm_rates[:n].tolist(),
        })

    # Remove all-NaN rows
    has_data = ~np.all(np.isnan(matrix), axis=1)
    matrix = matrix[has_data]

    if matrix.shape[0] == 0:
        empty = np.full(max_months, np.nan)
        return {"p10": empty, "p50": empty, "p90": empty,
                "traces": traces, "n_wells": 0, "excluded": excluded, "median_lateral": 0.0}

    with np.errstate(all="ignore"):
        p10 = np.nanpercentile(matrix, 10, axis=0)
        p50 = np.nanpercentile(matrix, 50, axis=0)
        p90 = np.nanpercentile(matrix, 90, axis=0)

    # Smooth P50 with 3-month rolling median
    p50 = _rolling_median(p50, window=3)

    return {
        "p10":            p10,
        "p50":            p50,
        "p90":            p90,
        "traces":         traces,
        "n_wells":        int(matrix.shape[0]),
        "excluded":       excluded,
        "median_lateral": float(np.median(laterals)) if laterals else 0.0,
    }


def _rolling_median(arr: np.ndarray, window: int = 3) -> np.ndarray:
    """Apply a rolling median (forward-only) to a 1D array, preserving NaN gaps."""
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
