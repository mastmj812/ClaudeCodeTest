"""
Streamlit cache layer.

Cache key strategy: each cached function takes `data_version` (an int from
session state) as its first arg. The integer is bumped whenever the
underlying DataFrames change (load, formation mapping apply, section
change), which invalidates all entries derived from those DataFrames.
Inside the function we read from st.session_state directly — for the same
data_version the state is guaranteed identical, so caching is sound and
we avoid the JSON serialize/re-parse roundtrip on every call.
"""

import streamlit as st
import pandas as pd


@st.cache_data(show_spinner=False)
def fit_wells(data_version: int):
    """Cache decline curve fits for the current section."""
    from engineering.decline import fit_all_section_wells
    return fit_all_section_wells(
        st.session_state.section_wells,
        st.session_state.section_prod,
    )


@st.cache_data(show_spinner=False)
def map_offsets(
    data_version: int,
    formation_names_tuple: tuple,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    section_apis_tuple: tuple,
) -> pd.DataFrame:
    """
    Return all formation-matching wells within radius for map display.
    No age or lateral-length filtering — shows maximum context on the map.
    """
    from utils.geo import haversine_miles
    wells_df = st.session_state.wells_df

    df = wells_df[wells_df["formation"].fillna("").isin(list(formation_names_tuple))].copy()
    df = df[~df["api"].isin(set(section_apis_tuple))]

    valid = df.dropna(subset=["latitude", "longitude"])
    if valid.empty:
        return valid.reset_index(drop=True)

    dists = haversine_miles(center_lat, center_lon, valid["latitude"].values, valid["longitude"].values)
    return valid[dists <= radius_miles].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def formation_well_counts(
    data_version: int,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    max_well_age_yr: int,
    section_apis_tuple: tuple,
) -> dict:
    """
    Return {canonical_formation: qualifying_well_count} across all formations
    in the offset radius. Uses the same filter criteria as get_offset_wells().
    """
    from engineering.type_curve import get_offset_wells
    wells_df = st.session_state.wells_df

    section_apis = set(section_apis_tuple)
    counts = {}
    for formation in wells_df["formation"].dropna().unique():
        offsets = get_offset_wells(
            wells_df, [formation], center_lat, center_lon,
            radius_miles, max_well_age_yr, section_apis,
        )
        if len(offsets) > 0:
            counts[formation] = len(offsets)
    return counts


@st.cache_data(show_spinner=False)
def type_curve(
    data_version: int,
    formation: str,
    center_lat: float, center_lon: float,
    radius_miles: float, max_well_age_yr: int,
    section_apis_tuple: tuple,
    formation_names_tuple: tuple,
):
    """Cache type curve per formation + filter settings."""
    from engineering.type_curve import get_offset_wells, build_type_curve
    wells_df = st.session_state.wells_df
    prod_df  = st.session_state.prod_df
    offsets = get_offset_wells(
        wells_df, list(formation_names_tuple), center_lat, center_lon,
        radius_miles, max_well_age_yr, set(section_apis_tuple),
    )
    return build_type_curve(offsets, prod_df), offsets
