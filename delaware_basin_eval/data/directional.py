"""
Directional survey ingestion: extract heel-point lat/lon per well so the map
can draw lateral sticks as heel→bottom-hole rather than surface→bottom-hole.

Heel = first survey station (sorted by MeasuredDepth_FT) where Inclination_DEG
crosses the configured threshold (default 80°). Wells without surveys, or wells
that never reach the threshold, get no heel — callers fall back to surface→BH.

Lazy + persistent caching:
  - extract_heels reads the (potentially huge) directional surveys CSV in
    chunks, keeping only rows for `target_apis`. It returns *both* the found
    heels AND the full set of APIs it scanned, so the cache can record which
    APIs were attempted but didn't have a heel.
  - The parquet cache stores one row per scanned API; rows with NaN lat/lon
    represent "scanned, no heel found" so subsequent runs short-circuit.
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
    """Strip hyphens and zero-pad to 14 (vectorized — runs per CSV chunk)."""
    s = series.astype(str).str.replace("-", "", regex=False).str.strip()
    # slice(0,14) is a no-op for strings <14, truncates strings >14;
    # zfill(14) left-pads anything shorter.
    return s.str.slice(0, 14).str.zfill(14)


def extract_heels(
    csv_source,
    target_apis: set[str],
    inclination_deg: float = HEEL_INCLINATION_DEG,
    chunksize: int = 200_000,
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """
    Read the directional surveys CSV in chunks, keep only rows whose
    normalized API_UWI is in target_apis with CoordinateSource == 'ACTUAL',
    then for each API pick the first station (by MeasuredDepth_FT) where
    inclination >= threshold.

    csv_source: file path string OR file-like object (e.g. UploadedFile).
    Returns (heels_found, scanned_apis) where scanned_apis is the full input
    set (so the cache layer can record which APIs were attempted).
    """
    scanned = set(target_apis)
    if not scanned:
        return {}, set()

    keep_frames: list[pd.DataFrame] = []

    reader = pd.read_csv(
        csv_source,
        usecols=_CSV_USECOLS,
        chunksize=chunksize,
        low_memory=False,
    )
    for chunk in reader:
        chunk["api"] = _normalize_api(chunk["API_UWI"])
        m = (
            chunk["api"].isin(scanned)
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
        return {}, scanned

    all_keep = pd.concat(keep_frames, ignore_index=True)
    all_keep = all_keep.sort_values(["api", "MeasuredDepth_FT"])
    first = all_keep.drop_duplicates(subset=["api"], keep="first")

    heels = {
        a: (float(lat), float(lon))
        for a, lat, lon in zip(first["api"], first["Latitude"], first["Longitude"])
    }
    return heels, scanned


def _read_cache(cache_path: Path) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """
    Return (heels, scanned) from the parquet cache.
    - heels: API → (lat, lon) for rows with valid coords.
    - scanned: all APIs present in the parquet, including rows with NaN coords
      (representing "we already looked and there's no heel").
    """
    if not cache_path.exists():
        return {}, set()
    try:
        df = pd.read_parquet(cache_path)
    except Exception as exc:
        msg = (
            f"Heels cache at {cache_path} could not be read ({exc.__class__.__name__}: {exc}) — "
            f"re-extracting from the surveys CSV. Delete the file if this persists."
        )
        try:
            import streamlit as st
            st.warning(msg)
        except Exception:
            print(msg)
        return {}, set()

    apis = df["api"].astype(str)
    scanned = set(apis)
    valid_mask = df["heel_lat"].notna() & df["heel_lon"].notna()
    valid = df.loc[valid_mask]
    heels = {
        a: (float(lat), float(lon))
        for a, lat, lon in zip(
            valid["api"].astype(str),
            valid["heel_lat"],
            valid["heel_lon"],
        )
    }
    return heels, scanned


def _write_cache(
    cache_path: Path,
    heels: dict[str, tuple[float, float]],
    scanned: set[str],
) -> None:
    """
    Persist heels (one row per scanned API). APIs in `scanned` without a heel
    are written with NaN coords so subsequent loads know not to re-scan them.
    """
    if not scanned and not heels:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    all_apis = set(scanned) | set(heels.keys())
    rows = []
    for api in all_apis:
        if api in heels:
            lat, lon = heels[api]
            rows.append((api, lat, lon))
        else:
            rows.append((api, float("nan"), float("nan")))
    df = pd.DataFrame(rows, columns=["api", "heel_lat", "heel_lon"])
    df.to_parquet(cache_path, index=False)


def load_or_compute_heels(
    csv_source,
    target_apis: set[str],
    inclination_deg: float = HEEL_INCLINATION_DEG,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[str, tuple[float, float]]:
    """
    Return {api: (heel_lat, heel_lon)} for target_apis, using a parquet cache.
    Only APIs that have never been scanned trigger a CSV read.

    Pass csv_source=None to use only what's already cached (offline mode).
    """
    cached, scanned = _read_cache(cache_path)
    needed = set(target_apis) - scanned

    if not needed or csv_source is None:
        return {a: cached[a] for a in target_apis if a in cached}

    new_heels, new_scanned = extract_heels(csv_source, needed, inclination_deg=inclination_deg)
    merged_heels   = {**cached, **new_heels}
    merged_scanned = scanned | new_scanned
    _write_cache(cache_path, merged_heels, merged_scanned)
    cached = merged_heels

    return {a: cached[a] for a in target_apis if a in cached}


def ensure_heels_for(apis: set[str]) -> dict[str, tuple[float, float]]:
    """
    Lazily ensure heel coords are available for the given API set.
    Reads `st.session_state.dir_surveys_path` (set by the sidebar uploader);
    incrementally extends `st.session_state.heels` for any missing APIs.

    Tracks `st.session_state.heels_scanned` (a set) alongside `heels` so APIs
    that were scanned but had no heel don't retrigger CSV reads on every rerun.

    No-op if no directional surveys path is configured. Idempotent.
    Returns the (possibly updated) heels dict.
    """
    import streamlit as st  # local import — module is also used outside Streamlit
    existing  = st.session_state.get("heels", {}) or {}
    scanned   = st.session_state.get("heels_scanned", set()) or set()
    if not apis:
        return existing
    csv_path = st.session_state.get("dir_surveys_path")
    if not csv_path:
        return existing
    needed = set(apis) - scanned
    if not needed:
        return existing
    new_heels = load_or_compute_heels(csv_path, needed)
    # Whatever the CSV scan returned, every API in `needed` has now been
    # examined — record them all as scanned so we never re-scan, even those
    # without a heel.
    st.session_state.heels_scanned = scanned | needed
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
