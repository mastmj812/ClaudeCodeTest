"""
Non-blocking data quality checks.
Returns a list of warning strings to display in the UI.
Never raises — callers decide whether to abort.
"""

import pandas as pd
import numpy as np
from config import FORMATION_ALIASES, FORMATIONS


def validate_wells(df: pd.DataFrame) -> list[str]:
    warnings = []

    # Missing critical columns
    for col in ["latitude", "longitude", "formation", "lateral_length", "first_prod_date"]:
        pct_missing = df[col].isna().mean() * 100 if col in df.columns else 100.0
        if pct_missing > 50:
            warnings.append(f"Well header: '{col}' is missing for {pct_missing:.0f}% of wells.")

    # Suspicious lateral lengths
    if "lateral_length" in df.columns:
        bad = df["lateral_length"].between(1, 999).sum()
        if bad:
            warnings.append(
                f"{bad} wells have lateral length < 1,000 ft — likely verticals or data errors."
            )

    # Unmapped formation names
    if "formation" in df.columns:
        known = set(FORMATIONS) | set(FORMATION_ALIASES.values())
        unmapped = df.loc[df["formation"].notna(), "formation"].unique()
        unmapped = [f for f in unmapped if f not in known]
        if unmapped:
            warnings.append(
                f"Unmapped formation names (will appear as-is): {', '.join(unmapped[:10])}"
                + (" …" if len(unmapped) > 10 else "")
            )

    # Duplicate APIs
    dups = df["api"].duplicated().sum()
    if dups:
        warnings.append(f"{dups} duplicate API numbers in well header — keeping first occurrence.")

    return warnings


def validate_production(df: pd.DataFrame) -> list[str]:
    warnings = []

    # Missing days_on
    if "days_on" in df.columns:
        pct_missing = df["days_on"].isna().mean() * 100
        if pct_missing > 20:
            warnings.append(
                f"Production: 'days_on_production' missing for {pct_missing:.0f}% of rows — "
                "daily rate calculation will be impaired."
            )

    # Detect quarterly gas reporting: within each API, flag if gas is 0 for 2 months then spikes
    if "gas_mcf" in df.columns and "api" in df.columns:
        quarterly_count = 0
        for _, grp in df.groupby("api"):
            gas = grp["gas_mcf"].fillna(0).values
            if len(gas) < 3:
                continue
            zeros = gas == 0
            spikes = np.diff(gas) > gas[:-1] * 1.5
            # rough heuristic: at least 10% of months look like quarterly pattern
            pattern = np.sum(zeros[:-1] & spikes) / len(gas)
            if pattern > 0.10:
                quarterly_count += 1
        if quarterly_count:
            warnings.append(
                f"{quarterly_count} wells appear to have quarterly gas reporting. "
                "Gas production has been redistributed evenly across 3-month windows."
            )

    # Negative production values
    for col in ["oil_bbl", "gas_mcf", "water_bbl"]:
        if col in df.columns:
            neg = (df[col] < 0).sum()
            if neg:
                warnings.append(f"{neg} rows have negative {col} — set to 0.")

    return warnings


def fix_quarterly_gas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Redistribute quarterly gas spikes evenly across the preceding 3-month window.
    Operates in-place on a copy.
    """
    df = df.copy()
    if "gas_mcf" not in df.columns:
        return df

    def _fix_group(grp):
        grp = grp.sort_values("prod_date").copy()
        gas = grp["gas_mcf"].fillna(0).values
        for i in range(2, len(gas)):
            if gas[i - 1] == 0 and gas[i - 2] == 0 and gas[i] > 0:
                total = gas[i]
                gas[i - 2] = total / 3
                gas[i - 1] = total / 3
                gas[i] = total / 3
        grp["gas_mcf"] = gas
        return grp

    parts = [_fix_group(grp) for _, grp in df.groupby("api")]
    if parts:
        return pd.concat(parts).reset_index(drop=True)
    return df
