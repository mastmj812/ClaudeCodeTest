"""Tab 3 — Type Curve & Remaining Locations."""

from datetime import date as _date

import numpy as np
import streamlit as st

from ui import cache
from ui.charts import (
    section_map, type_curve_chart, stream_type_curve_chart,
    formation_well_count_chart, cumulative_type_curve_chart,
)
from engineering.type_curve import generate_type_curve_profile, export_type_curve_csv
from engineering.spacing import remaining_locations
from config import FORMATIONS


def _stream_inputs(col, selected_formation: str, stream_key: str, label: str, qi_unit: str) -> dict:
    """Render number inputs for one stream; return updated params dict."""
    p = st.session_state.tc_params[selected_formation][stream_key]
    with col:
        st.markdown(f"**{label}**")
        new_p = {
            "ramp_months": st.number_input(
                "Ramp months", min_value=0, max_value=24,
                value=int(p.get("ramp_months", 0)), step=1,
                key=f"ramp_{selected_formation}_{stream_key}",
            ),
            "q_ramp": st.number_input(
                f"Ramp start rate ({qi_unit})", min_value=0.0,
                value=float(p.get("q_ramp", 0.0)), step=10.0, format="%.1f",
                key=f"q_ramp_{selected_formation}_{stream_key}",
            ),
            "qi": st.number_input(
                f"qi ({qi_unit})", min_value=0.0,
                value=float(p.get("qi", 100.0)), step=10.0, format="%.1f",
                key=f"qi_{selected_formation}_{stream_key}",
            ),
            "di_annual": st.number_input(
                "Di annual (%)", min_value=1.0, max_value=500.0,
                value=float(p.get("di_annual", 0.80) * 100), step=5.0, format="%.1f",
                key=f"di_{selected_formation}_{stream_key}",
            ) / 100.0,
            "b": st.number_input(
                "b factor", min_value=0.01, max_value=2.0,
                value=float(p.get("b", 1.2)), step=0.05, format="%.2f",
                key=f"b_{selected_formation}_{stream_key}",
            ),
            "dt_annual": st.number_input(
                "Dt annual (%)", min_value=0.1, max_value=30.0,
                value=float(p.get("dt_annual", 0.06) * 100), step=0.5, format="%.1f",
                key=f"dt_{selected_formation}_{stream_key}",
            ) / 100.0,
        }
    return new_p


def render():
    section_wells = st.session_state.section_wells
    wells_df      = st.session_state.wells_df
    cfg           = st.session_state.cfg

    if section_wells is None:
        st.info("Select a section in the sidebar to view the type curve and remaining locations.")
        return
    if cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
        return

    all_data_formations = sorted(wells_df["formation"].dropna().unique().tolist())
    extra = [f for f in all_data_formations if f not in FORMATIONS]
    formation_options = FORMATIONS + extra

    valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
    center_lat = valid_sw["latitude"].mean() if not valid_sw.empty else 31.5
    center_lon = valid_sw["longitude"].mean() if not valid_sw.empty else -104.0
    _section_apis_t = tuple(sorted(section_wells["api"].tolist()))
    _data_version = st.session_state.data_version

    # Formation selector + comp set
    sel_col, comp_col = st.columns([2, 3])
    with sel_col:
        selected_formation = st.selectbox("Formation for type curve", options=formation_options)
    with comp_col:
        saved_names = st.session_state.formation_name_map.get(selected_formation)
        if saved_names is None:
            saved_names = [selected_formation] if selected_formation in all_data_formations else []
        valid_defaults = [n for n in saved_names if n in all_data_formations]
        offset_names = st.multiselect(
            "Offset well formation names for comp set",
            options=all_data_formations,
            default=valid_defaults,
            help="Pick every raw ENVInterval value that represents this zone.",
        )
        st.session_state.formation_name_map[selected_formation] = offset_names

    effective_fnames = offset_names if offset_names else (
        [selected_formation] if selected_formation in all_data_formations else []
    )

    # Top row: map + formation well-count chart
    map_col, count_col = st.columns([3, 2])

    with map_col:
        _map_fnames = effective_fnames if effective_fnames else [selected_formation]
        map_offsets = cache.map_offsets(
            _data_version, tuple(sorted(_map_fnames)),
            center_lat, center_lon, cfg["offset_radius_mi"], _section_apis_t,
        )
        st.plotly_chart(
            section_map(
                section_wells,
                offset_wells=map_offsets if not map_offsets.empty else None,
                radius_miles=cfg["offset_radius_mi"],
                center_lat=center_lat, center_lon=center_lon,
            ),
            use_container_width=True,
        )

    with count_col:
        with st.spinner("Counting offset wells by formation…"):
            well_counts = cache.formation_well_counts(
                _data_version, center_lat, center_lon,
                cfg["offset_radius_mi"], cfg["max_well_age_yr"], _section_apis_t,
            )
        st.plotly_chart(
            formation_well_count_chart(well_counts),
            use_container_width=True,
        )

    # Build type curve
    tc, offsets = None, None
    if effective_fnames:
        with st.spinner(f"Building {selected_formation} type curve…"):
            tc, offsets = cache.type_curve(
                _data_version,
                selected_formation, center_lat, center_lon,
                cfg["offset_radius_mi"], cfg["max_well_age_yr"],
                _section_apis_t, tuple(sorted(effective_fnames)),
            )

    if not effective_fnames:
        st.info("Select at least one formation name in the comp set to build a type curve.")
    elif tc is not None and tc["n_wells"] == 0:
        st.warning(
            f"No qualifying offset wells found for {selected_formation} within "
            f"{cfg['offset_radius_mi']} miles. Try increasing the radius or max well age."
        )
        fc = offsets.attrs.get("filter_counts", {}) if offsets is not None else {}
        if fc:
            st.caption(
                f"Filter: total {fc.get('total','?')} → formation {fc.get('after_formation','?')} "
                f"→ age {fc.get('after_age','?')} → excl. section {fc.get('after_section_exclude','?')} "
                f"→ lateral {fc.get('after_lateral','?')} → radius {fc.get('after_radius','?')}"
            )
    elif tc is not None:
        # Initialize tc_params from suggested_params on first load
        if selected_formation not in st.session_state.tc_params:
            sp = tc.get("suggested_params", {})
            st.session_state.tc_params[selected_formation] = {
                "oil":   {**sp.get("oil",   {}), "ramp_months": 0, "q_ramp": 0.0},
                "gas":   {**sp.get("gas",   {}), "ramp_months": 0, "q_ramp": 0.0},
                "water": {**sp.get("water", {}), "ramp_months": 0, "q_ramp": 0.0},
            }
        params = st.session_state.tc_params[selected_formation]

        st.markdown("#### Type Curve Parameters")
        p_col_oil, p_col_gas, p_col_water = st.columns(3)

        new_oil   = _stream_inputs(p_col_oil,   selected_formation, "oil",   "Oil",   "BOPD/10kft")
        new_gas   = _stream_inputs(p_col_gas,   selected_formation, "gas",   "Gas",   "MCF/d/10kft")
        new_water = _stream_inputs(p_col_water, selected_formation, "water", "Water", "BWPD/10kft")

        if (new_oil != params["oil"] or new_gas != params["gas"] or new_water != params["water"]):
            st.session_state.tc_params[selected_formation] = {
                "oil": new_oil, "gas": new_gas, "water": new_water,
            }
            params = st.session_state.tc_params[selected_formation]

        N_MONTHS = 600
        active_oil   = generate_type_curve_profile(params["oil"],   N_MONTHS)
        active_gas   = generate_type_curve_profile(params["gas"],   N_MONTHS)
        active_water = generate_type_curve_profile(params["water"], N_MONTHS)

        # Oil type curve chart + stats
        chart_col, stats_col = st.columns([3, 1])
        with chart_col:
            st.plotly_chart(
                type_curve_chart(
                    tc["traces"], tc["p10"], tc["p50"], tc["p90"],
                    formation=selected_formation, n_wells=tc["n_wells"],
                    active_curve=active_oil,
                ),
                use_container_width=True,
            )
            if not offset_names:
                st.caption("Using default comp set (same formation name).")
            if tc["excluded"] > 0:
                st.caption(f"{tc['excluded']} wells excluded (missing lateral or production data).")

        with stats_col:
            st.markdown("#### Comp Set Stats")
            st.metric("Wells in comp set", tc["n_wells"])
            st.metric("Median lateral", f"{tc['median_lateral']:,.0f} ft")
            eur_per_ft = float(np.nansum(active_oil)) / 10_000
            st.metric("EUR/ft (active)", f"{eur_per_ft:.1f} BO/ft")
            if offsets is not None and not offsets.empty and "first_prod_date" in offsets.columns:
                dates = offsets["first_prod_date"].dropna()
                if not dates.empty:
                    st.metric("Comp date range",
                              f"{dates.min().strftime('%m/%Y')} – {dates.max().strftime('%m/%Y')}")

            st.markdown("---")
            csv_str = export_type_curve_csv(
                selected_formation, active_oil, active_gas, active_water
            )
            st.download_button(
                label="Export CSV",
                data=csv_str,
                file_name=f"type_curve_{selected_formation.replace(' ','_')}_{_date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        # Cumulative oil chart
        st.plotly_chart(
            cumulative_type_curve_chart(
                offset_traces=tc["traces"],
                cum_p10=tc.get("cum_p10", np.full(120, np.nan)),
                cum_p50=tc.get("cum_p50", np.full(120, np.nan)),
                cum_p90=tc.get("cum_p90", np.full(120, np.nan)),
                formation=selected_formation,
                active_curve=active_oil,
            ),
            use_container_width=True,
        )

        # Gas/water mini-charts
        gas_chart_col, water_chart_col = st.columns(2)
        with gas_chart_col:
            st.plotly_chart(
                stream_type_curve_chart(
                    p50=tc.get("gas_p50", np.full(120, np.nan)),
                    active_curve=active_gas,
                    title=f"Gas — {selected_formation}",
                    y_title="Gas Rate (MCF/d / 10,000 ft lateral)",
                ),
                use_container_width=True,
            )
        with water_chart_col:
            st.plotly_chart(
                stream_type_curve_chart(
                    p50=tc.get("water_p50", np.full(120, np.nan)),
                    active_curve=active_water,
                    title=f"Water — {selected_formation}",
                    y_title="Water Rate (BWPD / 10,000 ft lateral)",
                ),
                use_container_width=True,
            )

    # Remaining locations
    st.markdown("---")
    st.markdown("#### Remaining Locations")
    rem_df = remaining_locations(
        section_wells, st.session_state.section_acreage, cfg["wells_per_section"],
    )
    rem_col, rem_metric_col = st.columns([3, 1])
    with rem_col:
        st.dataframe(rem_df, use_container_width=True, hide_index=True)
    with rem_metric_col:
        st.metric("Total Undrilled Locations", int(rem_df["Remaining"].sum()))
