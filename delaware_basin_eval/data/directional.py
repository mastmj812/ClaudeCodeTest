"""
Directional survey ingestion: extract heel-point lat/lon per well so the map
can draw lateral sticks as heel→bottom-hole rather than surface→bottom-hole.

Heel = first survey station (sorted by MeasuredDepth_FT) where Inclination_DEG
crosses the configured threshold (default 80°). Wells without surveys, or wells
that never reach the threshold, get no heel — callers fall back to surface→BH.

Lazy + persistent caching:
  - extract_heels reads the (potentially huge) directional surveys CSV in
    chunks, keeping only rows for `target_apis`.
  - load_or_compute_heels checks an on-disk parquet first; only APIs not yet
    in the parquet trigger a CSV read; new heels are appended back.
"""

from pathlib import Path
import pandas as pd
import numpy as np


HEEL_INCLINATION_DEG = 80.0
DEFAULT_CACHE_PATH = Path("data_cache") / "heels.parquet"
_CSV_USECOLS = [
    "API_UWI", "MeasuredDepth_FT", "CoordinateSource",
    "Inclination_DEG", "Latitude", "Longitude",
]


def _normalize_api(series: pd.Series) -> pd.Series:
    """Strip hyphens and zero-pad to 14 to match the canonical wells_df api format."""
    s = series.astype(str).str.replace("-", "", regex=False).str.strip()
    return s.apply(lambda x: x.zfill(14) if len(x) <= 14 else x[:14])


def extract_heels(
    csv_source,
    target_apis: set[str],
    inclination_deg: float = HEEL_INCLINATION_DEG,
    chunksize: int = 200_000,
) -> dict[str, tuple[float, float]]:
    """
    Read the directional surveys CSV in chunks, keep only rows whose
    normalized API_UWI is in target_apis with CoordinateSource == 'ACTUAL',
    then for each API pick the first station (by MeasuredDepth_FT) where
    inclination >= threshold.

    csv_source: file path string OR file-like object (e.g. UploadedFile).
    Returns {api_14: (heel_lat, heel_lon)}.
    """
    if not target_apis:
        return {}

    keep_frames: list[pd.DataFrame] = []
    target_set = set(target_apis)

    reader = pd.read_csv(
        csv_source,
        usecols=_CSV_USECOLS,
        chunksize=chunksize,
        low_memory=False,
    )
    for chunk in reader:
        chunk["api"] = _normalize_api(chunk["API_UWI"])
        m = (
            chunk["api"].isin(target_set)
            & (chunk["CoordinateSource"].astype(str).str.upper() == "ACTUAL")
            & chunk["Inclination_DEG"].notna()
            & (chunk["Inclination_DEG"] >= inclination_deg)
            & chunk["Latitude"].notna()
            & chunk["Longitude"].notna()
        )
        keep = chunk.loc[m, ["api", "MeasuredDepth_FT", "Latitude", "Longitude"]]
        if not keep.empty:
            keep_frames.append(keep)

    if not keep_frames:
        return {}

    all_keep = pd.concat(keep_frames, ignore_index=True)
    # First station crossing threshold for each API = lowest measured depth
    all_keep = all_keep.sort_values(["api", "MeasuredDepth_FT"])
    first = all_keep.drop_duplicates(subset=["api"], keep="first")

    return {
        row["api"]: (float(row["Latitude"]), float(row["Longitude"]))
        for _, row in first.iterrows()
    }


def _read_cache(cache_path: Path) -> dict[str, tuple[float, float]]:
    if not cache_path.exists():
        return {}
    try:
        df = pd.read_parquet(cache_path)
        return {
            r["api"]: (float(r["heel_lat"]), float(r["heel_lon"]))
            for _, r in df.iterrows()
        }
    except Exception:
        return {}


def _write_cache(cache_path: Path, heels: dict[str, tuple[float, float]]) -> None:
    if not heels:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [(api, lat, lon) for api, (lat, lon) in heels.items()],
        columns=["api", "heel_lat", "heel_lon"],
    )
    df.to_parquet(cache_path, index=False)


def load_or_compute_heels(
    csv_source,
    target_apis: set[str],
    inclination_deg: float = HEEL_INCLINATION_DEG,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[str, tuple[float, float]]:
    """
    Return {api: (heel_lat, heel_lon)} for target_apis, using a parquet cache.
    Only APIs missing from the cache trigger a CSV read.

    Pass csv_source=None to use only what's already cached (offline mode).
    """
    cached = _read_cache(cache_path)
    needed = set(target_apis) - set(cached.keys())

    if not needed:
        return {a: cached[a] for a in target_apis if a in cached}

    if csv_source is None:
        # No CSV available — return what we have
        return {a: cached[a] for a in target_apis if a in cached}

    new_heels = extract_heels(csv_source, needed, inclination_deg=inclination_deg)
    if new_heels:
        merged = {**cached, **new_heels}
        _write_cache(cache_path, merged)
        cached = merged

    return {a: cached[a] for a in target_apis if a in cached}


def ensure_heels_for(apis: set[str]) -> dict[str, tuple[float, float]]:
    """
    Lazily ensure heel coords are available for the given API set.
    Reads `st.session_state.dir_surveys_path` (set by the sidebar uploader);
    incrementally extends `st.session_state.heels` for any missing APIs.

    No-op if no directional surveys path is configured. Idempotent.
    Returns the (possibly updated) heels dict.
    """
    import streamlit as st  # local import — module is also used outside Streamlit
    existing = st.session_state.get("heels", {}) or {}
    if not apis:
        return existing
    csv_path = st.session_state.get("dir_surveys_path")
    if not csv_path:
        return existing
    needed = set(apis) - set(existing.keys())
    if not needed:
        return existing
    new_heels = load_or_compute_heels(csv_path, needed)
    if new_heels:
        existing = {**existing, **new_heels}
        st.session_state.heels = existing
    return existing


def attach_heels(df: pd.DataFrame, heels: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """
    Return a copy of df with `latitude_heel` and `longitude_heel` columns
    populated from the heels dict (NaN for APIs without a heel). If df has no
    `api` column or heels is empty, returns df unchanged.
    """
    if df is None or df.empty or not heels or "api" not in df.columns:
        return df
    out = df.copy()
    out["latitude_heel"]  = out["api"].map(lambda a: heels.get(a, (np.nan, np.nan))[0])
    out["longitude_heel"] = out["api"].map(lambda a: heels.get(a, (np.nan, np.nan))[1])
    return out
