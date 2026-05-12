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

    # Lazily extract heel coords for the *section* wells only. Offset wells
    # change every time the radius/AOI is edited, so including them here makes
    # every Offset Filter tweak force a fresh 200MB+ surveys CSV pass. Offsets
    # render as surface→BH (the existing fallback in _lateral_line_coords),
    # which is fine for visual context.
    from data.directional import ensure_heels_for
    needed_apis = set(section_wells["api"])
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
        "Uncheck **Include** to drop a well from the section. "
        "Edit **WI** / **NRI** to override the sidebar defaults for that well (leave blank to use defaults). "
        "Click **Apply changes** to commit."
    )

    readonly_cols = [c for c in
        ["well_name", "api", "formation", "lateral_length", "first_prod_date", "operator", "status"]
        if c in section_wells.columns]

    editor_df = section_wells[readonly_cols].copy()
    editor_df.insert(0, "Include", True)
    editor_df["WI"]  = section_wells["wi"]  if "wi"  in section_wells.columns else float("nan")
    editor_df["NRI"] = section_wells["nri"] if "nri" in section_wells.columns else float("nan")

    cfg_wi  = float(cfg.get("wi",  1.00)) if cfg else 1.00
    cfg_nri = float(cfg.get("nri", 0.75)) if cfg else 0.75

    edited = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        disabled=readonly_cols,
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
            "WI": st.column_config.NumberColumn(
                "WI", format="%.4f", min_value=0.0, max_value=1.0, step=0.0001,
                help=f"Working interest (0–1). Blank → default {cfg_wi:.2f}.",
            ),
            "NRI": st.column_config.NumberColumn(
                "NRI", format="%.4f", min_value=0.0, max_value=1.0, step=0.0001,
                help=f"Net revenue interest (0–1). Blank → default {cfg_nri:.2f}.",
            ),
        },
        key=f"section_well_editor_v{st.session_state.data_version}",
    )

    import numpy as _np
    orig_wi  = section_wells["wi"]  if "wi"  in section_wells.columns else None
    orig_nri = section_wells["nri"] if "nri" in section_wells.columns else None
    edited_wi  = edited["WI"]
    edited_nri = edited["NRI"]

    def _series_differs(a, b) -> bool:
        if a is None:
            return b.notna().any()
        # Treat NaN==NaN as equal
        return not a.fillna(_np.inf).equals(b.fillna(_np.inf))

    interest_changed = _series_differs(orig_wi, edited_wi) or _series_differs(orig_nri, edited_nri)
    n_excluded = int((~edited["Include"]).sum())

    if n_excluded > 0 or interest_changed:
        bits = []
        if n_excluded > 0:
            plural = "s" if n_excluded != 1 else ""
            bits.append(f"remove {n_excluded} well{plural}")
        if interest_changed:
            bits.append("update WI/NRI")
        label = "Apply changes (" + " · ".join(bits) + ")"
        if st.button(label, type="primary"):
            keep_mask = edited["Include"].values
            keep_apis = set(section_wells.loc[keep_mask, "api"])
            updated = section_wells.copy()
            updated["wi"]  = edited_wi.values
            updated["nri"] = edited_nri.values
            updated = updated[updated["api"].isin(keep_apis)].reset_index(drop=True)
            st.session_state.section_wells = updated
            if st.session_state.section_prod is not None:
                st.session_state.section_prod = st.session_state.section_prod[
                    st.session_state.section_prod["api"].isin(keep_apis)
                ].copy()
            st.session_state.data_version += 1
            st.rerun()
