"""Tab 1 — Section Overview."""

import streamlit as st

from ui.charts import section_map


def render():
    section_wells = st.session_state.section_wells
    if section_wells is None:
        st.info("Select a section in the sidebar to see the section overview.")
        return

    wells_df = st.session_state.wells_df
    cfg      = st.session_state.cfg

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Wells in Section", len(section_wells))
    formations_present = section_wells["formation"].dropna().unique()
    col_b.metric("Formations", len(formations_present))
    col_c.metric("Section Acreage", f"{st.session_state.section_acreage:,.0f} ac")

    offset_wells = None
    if cfg and wells_df is not None:
        valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
        if not valid_sw.empty:
            center_lat = valid_sw["latitude"].mean()
            center_lon = valid_sw["longitude"].mean()
            try:
                from utils.geo import wells_within_radius
                offset_wells = wells_within_radius(
                    wells_df, center_lat, center_lon, cfg["offset_radius_mi"]
                )
                section_apis = set(section_wells["api"])
                offset_wells = offset_wells[~offset_wells["api"].isin(section_apis)]
            except Exception:
                offset_wells = None

    st.plotly_chart(
        section_map(section_wells, offset_wells=offset_wells),
        use_container_width=True,
    )

    st.markdown("#### Well Inventory")
    display_cols = [c for c in
        ["well_name", "api", "formation", "lateral_length", "first_prod_date", "operator", "status"]
        if c in section_wells.columns]
    st.dataframe(
        section_wells[display_cols].rename(columns={
            "well_name": "Well Name", "api": "API",
            "formation": "Formation", "lateral_length": "Lateral (ft)",
            "first_prod_date": "First Prod", "operator": "Operator",
            "status": "Status",
        }),
        use_container_width=True,
        hide_index=True,
    )
