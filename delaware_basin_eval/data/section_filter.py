"""
Filter the full well header DataFrame down to a user-specified section.

Two code paths:
  1. Text identifier  — matches Section/Township/Range (PLSS) OR Abstract number
  2. Shapefile        — spatial join using a GeoDataFrame polygon

Returns (section_wells, section_acreage_acres).
"""

import re
import pandas as pd
import numpy as np
from config import PLSS_SECTION_ACRES

try:
    from utils.geo import wells_in_polygon, polygon_area_acres, centroid_latlon
    HAS_GEO = True
except Exception:
    HAS_GEO = False


# ── Text-based filter ──────────────────────────────────────────────────────

def _parse_section_id(text: str) -> dict:
    """
    Parse a freeform section identifier into components.

    Recognizes patterns like:
      "Section 15 Township 1S Range 26E"
      "T1S R26E Sec 15"
      "Abstract 123"
      "15-1S-26E"
    Returns dict with any of: section, township, range, abstract
    """
    text = text.strip()
    parts: dict[str, str] = {}

    # Abstract
    m = re.search(r"\babs(?:tract)?\s*[#:]?\s*(\d+)", text, re.IGNORECASE)
    if m:
        parts["abstract"] = m.group(1).zfill(4)

    # Section
    m = re.search(r"\bse?c(?:tion)?\s*[#:]?\s*(\d+)", text, re.IGNORECASE)
    if m:
        parts["section"] = m.group(1).zfill(2)

    # Township  (e.g., T1S, T01S, 1S)
    m = re.search(r"\bT\s*(\d+)\s*([NS])", text, re.IGNORECASE)
    if m:
        parts["township"] = f"{m.group(1).zfill(2)}{m.group(2).upper()}"

    # Range  (e.g., R26E, 26E)
    m = re.search(r"\bR\s*(\d+)\s*([EW])", text, re.IGNORECASE)
    if m:
        parts["range"] = f"{m.group(1).zfill(2)}{m.group(2).upper()}"

    # Compact form: 15-1S-26E
    m = re.match(r"^(\d+)-(\d+[NS])-(\d+[EW])$", text.upper().replace(" ", ""))
    if m and not parts:
        parts["section"]  = m.group(1).zfill(2)
        parts["township"] = m.group(2).zfill(3)
        parts["range"]    = m.group(3).zfill(3)

    return parts


def _normalize_series_for_match(series: pd.Series) -> pd.Series:
    """Strip, upper, zero-pad numbers in a string series for fuzzy matching."""
    return (
        series.astype(str)
              .str.strip()
              .str.upper()
              .str.replace(r"\s+", "", regex=True)
    )


def filter_by_text(wells_df: pd.DataFrame, identifier: str) -> pd.DataFrame:
    """
    Filter wells_df to rows matching the parsed section identifier.
    Checks both PLSS columns (section/township/range) and abstract column.
    Returns all matching rows (union of both systems).
    """
    parts = _parse_section_id(identifier)
    if not parts:
        return pd.DataFrame(columns=wells_df.columns)

    masks = []

    # PLSS match
    plss_cols = [c for c in ["section", "township", "range"] if c in parts and c in wells_df.columns]
    if plss_cols:
        plss_mask = pd.Series(True, index=wells_df.index)
        for col in plss_cols:
            norm_col  = _normalize_series_for_match(wells_df[col].fillna(""))
            norm_val  = parts[col].upper().replace(" ", "")
            # flexible: strip leading zeros for comparison
            plss_mask &= norm_col.str.lstrip("0").eq(norm_val.lstrip("0"))
        if plss_mask.any():
            masks.append(plss_mask)

    # Abstract match
    if "abstract" in parts and "abstract" in wells_df.columns:
        norm_abs  = _normalize_series_for_match(wells_df["abstract"].fillna(""))
        norm_val  = parts["abstract"].upper().replace(" ", "")
        abs_mask  = norm_abs.str.lstrip("0").eq(norm_val.lstrip("0"))
        if abs_mask.any():
            masks.append(abs_mask)

    if not masks:
        return pd.DataFrame(columns=wells_df.columns)

    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m

    return wells_df[combined].copy()


# ── Shapefile-based filter ─────────────────────────────────────────────────

def filter_by_shapefile(wells_df: pd.DataFrame, polygon_gdf) -> pd.DataFrame:
    """Filter wells_df to those inside the uploaded shapefile polygon."""
    if not HAS_GEO:
        raise ImportError("geopandas is required for shapefile filtering.")
    return wells_in_polygon(wells_df, polygon_gdf)


# ── Section acreage estimate ───────────────────────────────────────────────

def estimate_section_acreage(polygon_gdf=None) -> float:
    """Return acreage: from polygon geometry if available, else 640-acre default."""
    if polygon_gdf is not None and HAS_GEO:
        acres = polygon_area_acres(polygon_gdf)
        if acres > 0:
            return acres
    return PLSS_SECTION_ACRES


# ── Combined entry point ───────────────────────────────────────────────────

def get_section_wells(
    wells_df: pd.DataFrame,
    identifier: str = "",
    polygon_gdf=None,
) -> tuple[pd.DataFrame, float]:
    """
    Returns (section_wells_df, section_acreage_acres).
    Uses shapefile if provided, otherwise text identifier.
    """
    if polygon_gdf is not None:
        section_wells = filter_by_shapefile(wells_df, polygon_gdf)
        acreage = estimate_section_acreage(polygon_gdf)
    elif identifier.strip():
        section_wells = filter_by_text(wells_df, identifier)
        acreage = PLSS_SECTION_ACRES
    else:
        return pd.DataFrame(columns=wells_df.columns), PLSS_SECTION_ACRES

    return section_wells.reset_index(drop=True), acreage
