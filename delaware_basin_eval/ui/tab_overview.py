"""Tab 1 — Section Overview."""

import streamlit as st

from ui.charts import section_map
from data.directional import attach_heels


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
    valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
    aoi_gdf = st.session_state.get("offset_aoi_gdf")
    if valid_sw.empty:
        st.warning(
            "Section wells have no coordinates — map and offset distance "
            "calculations are unavailable."
        )
    elif cfg and wells_df is not None:
        center_lat = valid_sw["latitude"].mean()
        center_lon = valid_sw["longitude"].mean()
        try:
            from utils.geo import filter_offsets
            offset_wells = filter_offsets(
                wells_df, center_lat, center_lon,
                cfg["offset_radius_mi"], aoi_gdf=aoi_gdf,
            )
            section_apis = set(section_wells["api"])
            offset_wells = offset_wells[~offset_wells["api"].isin(section_apis)]
        except Exception:
            offset_wells = None

    # Lazily extract heel coords for the wells we're about to draw (no-op if no
    # directional surveys uploaded). Falls back to surface→BH for missing APIs.
    from data.directional import ensure_heels_for
    needed_apis = set(section_wells["api"])
    if offset_wells is not None and not offset_wells.empty:
        needed_apis |= set(offset_wells["api"])
    if st.session_state.get("dir_surveys_path") and needed_apis:
        with st.spinner("Loading heel coordinates from directional surveys…"):
            heels = ensure_heels_for(needed_apis)
    else:
        heels = st.session_state.get("heels", {}) or {}

    section_wells_for_map = attach_heels(section_wells, heels)
    offset_wells_for_map  = attach_heels(offset_wells, heels) if offset_wells is not None else None

    polygon_geojson = st.session_state.get("offset_aoi_geojson") if aoi_gdf is not None else None

    if not valid_sw.empty:
        st.plotly_chart(
            section_map(
                section_wells_for_map,
                offset_wells=offset_wells_for_map,
                polygon_geojson=polygon_geojson,
            ),
            use_container_width=True,
        )

    st.markdown("#### Well Inventory")
    st.caption(
        "Uncheck **Include** for any well whose lateral runs off the acreage "
        "of interest, then click **Apply selection** to remove it from the section."
    )

    display_cols = [c for c in
        ["well_name", "api", "formation", "lateral_length", "first_prod_date", "operator", "status"]
        if c in section_wells.columns]

    editor_df = section_wells[display_cols].copy()
    editor_df.insert(0, "Include", True)

    edited = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        disabled=display_cols,
        column_config={
            "Include": st.column_config.CheckboxColumn(
                "Include", default=True,
                help="Uncheck to drop this well from the section selection.",
            ),
            "well_name":       st.column_config.TextColumn("Well Name"),
            "api":             st.column_config.TextColumn("API"),
            "formation":       st.column_config.TextColumn("Formation"),
            "lateral_length":  st.column_config.NumberColumn("Lateral (ft)", format="%d"),
            "first_prod_date": st.column_config.DateColumn("First Prod"),
            "operator":        st.column_config.TextColumn("Operator"),
            "status":          st.column_config.TextColumn("Status"),
        },
        key=f"section_well_editor_v{st.session_state.data_version}",
    )

    n_excluded = int((~edited["Include"]).sum())
    if n_excluded > 0:
        plural = "s" if n_excluded != 1 else ""
        if st.button(
            f"Apply selection (remove {n_excluded} well{plural})",
            type="primary",
        ):
            keep_apis = set(section_wells.loc[edited["Include"].values, "api"])
            st.session_state.section_wells = (
                section_wells[section_wells["api"].isin(keep_apis)].reset_index(drop=True)
            )
            if st.session_state.section_prod is not None:
                st.session_state.section_prod = st.session_state.section_prod[
                    st.session_state.section_prod["api"].isin(keep_apis)
                ].copy()
            st.session_state.data_version += 1
            st.rerun()
