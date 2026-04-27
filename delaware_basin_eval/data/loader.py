"""
CSV ingestion for Enverus/Drillinginfo exports.

Outputs two clean DataFrames with canonical column names:
  - wells_df:  one row per well (header data)
  - prod_df:   one row per well-month (production data)

All downstream modules expect these canonical names.
"""

import io
import pandas as pd
import numpy as np
from config import FORMATION_ALIASES

# ── Canonical column names ─────────────────────────────────────────────────
WELL_CANONICAL = {
    "api":            ["api", "api number", "api_number", "api14", "api 14"],
    "well_name":      ["well name", "well_name", "wellname", "lease name"],
    "operator":       ["operator", "operator name", "current operator"],
    "county":         ["county", "county name"],
    "latitude":       ["latitude", "surface latitude", "surf_lat", "lat"],
    "longitude":      ["longitude", "surface longitude", "surf_long", "lon", "long"],
    "formation":      ["formation", "producing formation", "reservoir", "zone"],
    "lateral_length": ["lateral length", "lateral_length", "perf interval", "completed lateral length",
                       "lateral length (ft)", "lateral_length_ft"],
    "measured_depth": ["measured depth", "measured_depth", "total depth", "td", "md (ft)"],
    "tvd":            ["true vertical depth", "tvd", "tvd (ft)"],
    "spud_date":      ["spud date", "spud_date", "spudded"],
    "first_prod_date":["first production date", "first_prod_date", "first production", "ip date",
                       "production start date"],
    "section":        ["section", "sec", "section number"],
    "township":       ["township", "twp", "twp_num"],
    "range":          ["range", "rng", "rng_num"],
    "abstract":       ["abstract", "abstract number", "abstract_number", "abs"],
    "survey":         ["survey", "survey name"],
    "status":         ["well status", "status", "well_status"],
    "well_type":      ["well type", "welltype", "well_type"],
}

PROD_CANONICAL = {
    "api":            ["api", "api number", "api_number", "api14", "api 14"],
    "prod_date":      ["date", "production date", "prod_date", "month", "production month"],
    "oil_bbl":        ["oil", "oil (bbl)", "oil_bbl", "liquid (bbl)", "liquids (bbl)", "oil production (bbl)"],
    "gas_mcf":        ["gas", "gas (mcf)", "gas_mcf", "gas production (mcf)", "casinghead gas (mcf)"],
    "water_bbl":      ["water", "water (bbl)", "water_bbl", "water production (bbl)"],
    "days_on":        ["days on production", "days_on", "days on", "producing days", "days prod"],
}


def _normalize_cols(df: pd.DataFrame, mapping: dict[str, list[str]]) -> pd.DataFrame:
    """Rename df columns to canonical names using case-insensitive matching."""
    col_lower = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for canonical, aliases in mapping.items():
        for alias in aliases:
            if alias.lower() in col_lower and canonical not in rename.values():
                rename[col_lower[alias.lower()]] = canonical
                break
    return df.rename(columns=rename)


def _standardize_api(series: pd.Series) -> pd.Series:
    """Normalize API to 14-digit zero-padded string without dashes."""
    s = series.astype(str).str.replace("-", "", regex=False).str.strip()
    # If 10 digits (common DI format), prepend state code 42 and pad
    s = s.apply(lambda x: x.zfill(14) if len(x) <= 14 else x[:14])
    return s


def _normalize_formation(series: pd.Series) -> pd.Series:
    """Map raw formation names to canonical names; unmapped values kept as-is."""
    return series.str.strip().apply(
        lambda x: FORMATION_ALIASES.get(str(x).lower().strip(), x) if pd.notna(x) else x
    )


def load_well_header(file) -> pd.DataFrame:
    """
    Load an Enverus well header CSV.
    file: file-like object or path string
    Returns a DataFrame with canonical column names.
    """
    df = _read_csv(file)
    df = _normalize_cols(df, WELL_CANONICAL)

    if "api" not in df.columns:
        raise ValueError("Well header file must contain an API column.")

    df["api"] = _standardize_api(df["api"])

    if "formation" in df.columns:
        df["formation"] = _normalize_formation(df["formation"])

    # Parse dates
    for col in ["spud_date", "first_prod_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Coerce numerics
    for col in ["lateral_length", "measured_depth", "tvd", "latitude", "longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Add missing columns as NaN so downstream code doesn't need to guard
    for col in WELL_CANONICAL:
        if col not in df.columns:
            df[col] = np.nan

    return df


def load_production(file) -> pd.DataFrame:
    """
    Load an Enverus production history CSV.
    Returns a DataFrame with canonical column names and a daily_oil_rate column.
    """
    df = _read_csv(file)
    df = _normalize_cols(df, PROD_CANONICAL)

    if "api" not in df.columns:
        raise ValueError("Production file must contain an API column.")
    if "prod_date" not in df.columns:
        raise ValueError("Production file must contain a date column.")

    df["api"] = _standardize_api(df["api"])
    df["prod_date"] = pd.to_datetime(df["prod_date"], errors="coerce")

    for col in ["oil_bbl", "gas_mcf", "water_bbl", "days_on"]:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").clip(lower=0)

    df = df.dropna(subset=["api", "prod_date"])
    df = df.sort_values(["api", "prod_date"]).reset_index(drop=True)

    # Daily rates — used for decline fitting
    df["daily_oil_rate"] = np.where(
        df["days_on"].gt(0), df["oil_bbl"] / df["days_on"], np.nan
    )
    df["daily_gas_rate"] = np.where(
        df["days_on"].gt(0), df["gas_mcf"] / df["days_on"], np.nan
    )

    # Remove duplicate API + date combinations (keep first)
    df = df.drop_duplicates(subset=["api", "prod_date"], keep="first")

    return df


def _read_csv(file) -> pd.DataFrame:
    """Read CSV handling both file paths and uploaded file objects."""
    if hasattr(file, "read"):
        content = file.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return pd.read_csv(io.StringIO(content), low_memory=False)
    return pd.read_csv(file, low_memory=False)
