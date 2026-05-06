"""Tab 3 — Type Curve & Remaining Locations."""

from datetime import date as _date

import numpy as np
import streamlit as st

from ui import cache
from ui.charts import (
    section_map, type_curve_chart, formation_well_count_chart,
    cumulative_type_curve_chart,
)
from engineering.type_curve import generate_type_curve_profile, export_type_curve_csv
from engineering.decline import (
    nominal_annual_to_effective_annual,
    effective_annual_to_nominal_annual,
)
from engineering.spacing import remaining_locations
from config import FORMATIONS


# Per-stream display config
_STREAM_CONFIG = {
    "oil": {
        "label":           "Oil",
        "qi_unit":         "BOPD/10kft",
        "rate_y_title":    "Oil Rate (BOPD / 10,000 ft lateral)",
        "cum_unit_short": "MBO",
        "cum_unit_scale": 1_000.0,
    },
    "gas": {
        "label":           "Gas",
        "qi_unit":         "MCF/d/10kft",
        "rate_y_title":    "Gas Rate (MCF/d / 10,000 ft lateral)",
        "cum_unit_short": "MMCF",
        "cum_unit_scale": 1_000.0,
    },
    "water": {
        "label":           "Water",
        "qi_unit":         "BWPD/10kft",
        "rate_y_title":    "Water Rate (BWPD / 10,000 ft lateral)",
        "cum_unit_short": "MBW",
        "cum_unit_scale": 1_000.0,
    },
}


def _stream_param_inputs(selected_formation: str, stream_key: str) -> dict:
    """Render the 6 parameter inputs for one stream as a horizontal row.
    Returns the updated params dict (with `di_annual`/`dt_annual` stored
    as nominal annual rates so internal math is unchanged; UI shows
    effective annual decline)."""
    p = st.session_state.tc_params[selected_formation][stream_key]
    cfg = _STREAM_CONFIG[stream_key]
    qi_unit = cfg["qi_unit"]
    b_now = float(p.get("b", 1.2))

    cols = st.columns(6)
    with cols[0]:
        ramp_months = st.number_input(
            "Ramp months", min_value=0, max_value=24,
            value=int(p.get("ramp_months", 0)), step=1,
            key=f"ramp_{selected_formation}_{stream_key}",
        )
    with cols[1]:
        q_ramp = st.number_input(
            f"Ramp start ({qi_unit})", min_value=0.0,
            value=float(p.get("q_ramp", 0.0)), step=10.0, format="%.1f",
            key=f"q_ramp_{selected_formation}_{stream_key}",
        )
    with cols[2]:
        qi = st.number_input(
            f"qi ({qi_unit})", min_value=0.0,
            value=float(p.get("qi", 100.0)), step=10.0, format="%.1f",
            key=f"qi_{selected_formation}_{stream_key}",
        )
    with cols[3]:
        # Convert stored nominal annual → effective for the input
        di_eff_default = nominal_annual_to_effective_annual(
            float(p.get("di_annual", 0.80)), b_now,
        ) * 100.0
        di_eff_pct = st.number_input(
            "Di annual (effective %)", min_value=0.1, max_value=99.9,
            value=float(min(max(di_eff_default, 0.1), 99.9)),
            step=1.0, format="%.1f",
            key=f"di_{selected_formation}_{stream_key}",
            help="Effective annual decline rate. Always between 0% and 100%. "
                 "Differs from nominal: a 70% effective decline ≈ 250%+ nominal.",
        )
    with cols[4]:
        b = st.number_input(
            "b factor", min_value=0.01, max_value=2.0,
            value=b_now, step=0.05, format="%.2f",
            key=f"b_{selected_formation}_{stream_key}",
        )
    with cols[5]:
        dt_eff_default = nominal_annual_to_effective_annual(
            float(p.get("dt_annual", 0.06)), b_now,
        ) * 100.0
        dt_eff_pct = st.number_input(
            "Dt annual (effective %)", min_value=0.1, max_value=30.0,
            value=float(min(max(dt_eff_default, 0.1), 30.0)),
            step=0.5, format="%.1f",
            key=f"dt_{selected_formation}_{stream_key}",
            help="Terminal effective decline. Switches from hyperbolic to "
                 "exponential when instantaneous decline reaches this level.",
        )

    # Convert effective % entered by user → nominal annual for storage
    di_annual = effective_annual_to_nominal_annual(di_eff_pct / 100.0, b)
    dt_annual = effective_annual_to_nominal_annual(dt_eff_pct / 100.0, b)

    return {
        "ramp_months": ramp_months,
        "q_ramp":      q_ramp,
        "qi":          qi,
        "di_annual":   di_annual,
        "b":           b,
        "dt_annual":   dt_annual,
    }


def _render_phase_section(
    selected_formation: str,
    stream_key: str,
    tc: dict,
    n_months: int = 600,
):
    """Render one phase section: parameter row + rate chart + cumulative chart.
    Returns the active monthly-volume profile for that stream."""
    cfg = _STREAM_CONFIG[stream_key]
    label = cfg["label"]

    st.markdown(f"### {label}")

    # Empty-state hint when offset comp set has no data for this stream
    p50_key = "p50" if stream_key == "oil" else f"{stream_key}_p50"
    p50_arr = tc.get(p50_key)
    if p50_arr is not None and np.all(np.isnan(p50_arr)):
        st.caption(
            f"⚠️ No {label.lower()} data in the comp set — the offset wells "
            f"have no recorded {label.lower()} production. Statistical band "
            f"will be empty; the active curve still drives undrilled economics."
        )

    # 1. Parameter inputs (horizontal)
    new_p = _stream_param_inputs(selected_formation, stream_key)
    params = st.session_state.tc_params[selected_formation]
    if new_p != params[stream_key]:
        params[stream_key] = new_p
        st.session_state.tc_params[selected_formation] = params

    active = generate_type_curve_profile(params[stream_key], n_months)

    # 2. Rate chart
    p10_key = "p10" if stream_key == "oil" else f"{stream_key}_p10"
    p50_key = "p50" if stream_key == "oil" else f"{stream_key}_p50"
    p90_key = "p90" if stream_key == "oil" else f"{stream_key}_p90"
    rate_traces = tc.get("traces", []) if stream_key == "oil" else []  # oil-only
    st.plotly_chart(
        type_curve_chart(
            offset_traces=rate_traces,
            p10=tc.get(p10_key, np.full(120, np.nan)),
            p50=tc.get(p50_key, np.full(120, np.nan)),
            p90=tc.get(p90_key, np.full(120, np.nan)),
            formation=selected_formation,
            n_wells=tc.get("n_wells", 0),
            active_curve=active,
            phase_label=label,
            y_title=cfg["rate_y_title"],
        ),
        use_container_width=True,
    )

    # 3. Cumulative chart
    cum_p10_key = "cum_p10" if stream_key == "oil" else f"cum_{stream_key}_p10"
    cum_p50_key = "cum_p50" if stream_key == "oil" else f"cum_{stream_key}_p50"
    cum_p90_key = "cum_p90" if stream_key == "oil" else f"cum_{stream_key}_p90"
    st.plotly_chart(
        cumulative_type_curve_chart(
            offset_traces=rate_traces,
            cum_p10=tc.get(cum_p10_key, np.full(120, np.nan)),
            cum_p50=tc.get(cum_p50_key, np.full(120, np.nan)),
            cum_p90=tc.get(cum_p90_key, np.full(120, np.nan)),
            formation=selected_formation,
            active_curve=active,
            phase_label=label,
            cum_unit_short=cfg["cum_unit_short"],
            cum_unit_scale=cfg["cum_unit_scale"],
        ),
        use_container_width=True,
    )

    return active


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
    if valid_sw.empty:
        st.warning(
            "Section wells have no coordinates — map and offset distance "
            "calculations are unavailable. Type curve will use the basin "
            "centroid as a placeholder."
        )
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

        # ── Comp-set stats + CSV export bar (above the 3 phase sections) ────
        N_MONTHS = 600
        # Pre-build profiles to compute EUR for the stats card and for export
        params = st.session_state.tc_params[selected_formation]
        active_oil_preview   = generate_type_curve_profile(params["oil"],   N_MONTHS)
        active_gas_preview   = generate_type_curve_profile(params["gas"],   N_MONTHS)
        active_water_preview = generate_type_curve_profile(params["water"], N_MONTHS)

        stat_a, stat_b, stat_c, stat_d, stat_e = st.columns([1, 1, 1, 1.2, 1.4])
        with stat_a:
            st.metric("Wells in comp set", tc["n_wells"])
        with stat_b:
            st.metric("Median lateral", f"{tc['median_lateral']:,.0f} ft")
        with stat_c:
            eur_per_ft = float(np.nansum(active_oil_preview)) / 10_000
            st.metric("EUR/ft (oil)", f"{eur_per_ft:.1f} BO/ft")
        with stat_d:
            if offsets is not None and not offsets.empty and "first_prod_date" in offsets.columns:
                dates = offsets["first_prod_date"].dropna()
                if not dates.empty:
                    st.metric("Comp date range",
                              f"{dates.min().strftime('%m/%Y')} – {dates.max().strftime('%m/%Y')}")
        with stat_e:
            st.write("")  # vertical alignment
            csv_str = export_type_curve_csv(
                selected_formation, active_oil_preview, active_gas_preview, active_water_preview
            )
            st.download_button(
                label="Export type curve CSV",
                data=csv_str,
                file_name=f"type_curve_{selected_formation.replace(' ','_')}_{_date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        if not offset_names:
            st.caption("Using default comp set (same formation name).")
        if tc["excluded"] > 0:
            st.caption(f"{tc['excluded']} wells excluded (missing lateral or production data).")

        # ── Three symmetric phase sections ───────────────────────────────────
        st.markdown("---")
        _render_phase_section(selected_formation, "oil",   tc, n_months=N_MONTHS)
        st.markdown("---")
        _render_phase_section(selected_formation, "gas",   tc, n_months=N_MONTHS)
        st.markdown("---")
        _render_phase_section(selected_formation, "water", tc, n_months=N_MONTHS)

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
