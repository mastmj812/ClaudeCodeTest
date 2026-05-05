"""Tab 4 — Undrilled Economics."""

import numpy as np
import pandas as pd
import streamlit as st

from ui import cache
from ui.charts import npv_waterfall, tornado_chart
from engineering.spacing import remaining_locations
from engineering.type_curve import generate_type_curve_profile
from economics.cashflow import build_undrilled_well_cashflow
from economics.metrics import well_economics, portfolio_irr


def _build_tc_params(formation: str, tc: dict) -> dict:
    """Pick user-adjusted tc_params for this formation, or fall back to suggested."""
    if formation in st.session_state.tc_params:
        return st.session_state.tc_params[formation]
    sp = tc.get("suggested_params", {})
    return {
        "oil":   {**sp.get("oil",   {}), "ramp_months": 0, "q_ramp": 0.0},
        "gas":   {**sp.get("gas",   {}), "ramp_months": 0, "q_ramp": 0.0},
        "water": {**sp.get("water", {}), "ramp_months": 0, "q_ramp": 0.0},
    }


def _undrilled_well_cf(tc_p: dict, alt_cfg: dict, formation: str):
    """Generate the 3-stream profiles, build cashflow for one undrilled well."""
    oil   = generate_type_curve_profile(tc_p["oil"],   600)
    gas   = generate_type_curve_profile(tc_p["gas"],   600)
    water = generate_type_curve_profile(tc_p["water"], 600)
    return build_undrilled_well_cashflow(oil, gas, water, alt_cfg, formation)


def render():
    section_wells = st.session_state.section_wells
    cfg           = st.session_state.cfg

    if section_wells is None:
        st.info("Select a section in the sidebar to evaluate undrilled economics.")
        return
    if cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
        return

    valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
    center_lat = valid_sw["latitude"].mean() if not valid_sw.empty else 31.5
    center_lon = valid_sw["longitude"].mean() if not valid_sw.empty else -104.0
    section_apis = set(section_wells["api"])

    rem_df = remaining_locations(section_wells, st.session_state.section_acreage, cfg["wells_per_section"])

    if not st.session_state.formation_name_map:
        st.info(
            "Visit the **Type Curve & Locations** tab first and configure the comp set for each "
            "formation you want to evaluate. Economics will compute using the default comp set "
            "(same formation name) if you skip that step."
        )

    try:
        with st.spinner("Computing undrilled well economics…"):
            econ_rows = []
            formation_npvs = {}
            all_undrilled_cf = []

            for _, row in rem_df.iterrows():
                formation   = row["Formation"]
                n_remaining = int(row["Remaining"])
                if n_remaining == 0:
                    continue

                fnames = st.session_state.formation_name_map.get(formation, [formation])
                if not fnames:
                    econ_rows.append({
                        "Formation": formation, "Remaining Wells": n_remaining,
                        "NPV/Well ($MM)": None, "IRR/Well (%)": None,
                        "Payout/Well (mo)": None, "PV10/Well ($MM)": None,
                        "Total NPV ($MM)": None, "Type Curve Wells": 0,
                        "Note": "No comp set — configure in Type Curve tab",
                    })
                    continue

                tc, _ = cache.type_curve(
                    st.session_state.data_version,
                    formation, center_lat, center_lon,
                    cfg["offset_radius_mi"], cfg["max_well_age_yr"],
                    tuple(sorted(section_apis)),
                    tuple(sorted(fnames)),
                )

                if tc["n_wells"] == 0:
                    econ_rows.append({
                        "Formation": formation, "Remaining Wells": n_remaining,
                        "NPV/Well ($MM)": None, "IRR/Well (%)": None,
                        "Payout/Well (mo)": None, "PV10/Well ($MM)": None,
                        "Total NPV ($MM)": None, "Type Curve Wells": 0,
                        "Note": "No qualifying offset wells found",
                    })
                    continue

                tc_p = _build_tc_params(formation, tc)
                cf_one = _undrilled_well_cf(tc_p, cfg, formation)
                econ   = well_economics(cf_one, cfg["discount_rate"])

                npv_val  = econ["npv"]  if (econ["npv"]  is not None and np.isfinite(econ["npv"]))  else None
                pv10_val = econ["pv10"] if (econ["pv10"] is not None and np.isfinite(econ["pv10"])) else None
                irr_val  = econ["irr"]  if (econ["irr"]  is not None and np.isfinite(econ["irr"]))  else None
                total_npv = (npv_val or 0) * n_remaining
                formation_npvs[formation] = total_npv

                for _ in range(n_remaining):
                    all_undrilled_cf.append(cf_one)

                econ_rows.append({
                    "Formation":        formation,
                    "Remaining Wells":  n_remaining,
                    "Type Curve Wells": tc["n_wells"],
                    "NPV/Well ($MM)":   round(npv_val  / 1e6, 2) if npv_val  is not None else None,
                    "IRR/Well (%)":     round(irr_val  * 100,  1) if irr_val  is not None else None,
                    "Payout/Well (mo)": econ["payout"],
                    "PV10/Well ($MM)":  round(pv10_val / 1e6, 2) if pv10_val is not None else None,
                    "Total NPV ($MM)":  round(total_npv / 1e6, 2),
                })

        total_undrilled_npv  = sum(v for v in formation_npvs.values() if np.isfinite(v))
        total_undrilled_pv10 = sum(
            (r["PV10/Well ($MM)"] or 0) * (r["Remaining Wells"] or 0)
            for r in econ_rows if r.get("PV10/Well ($MM)") is not None
        )
        port_irr = portfolio_irr(all_undrilled_cf) if all_undrilled_cf else None

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total Undrilled NPV",  f"${total_undrilled_npv/1e6:.1f}MM")
        col_b.metric("Total Undrilled PV10", f"${total_undrilled_pv10:.1f}MM")
        col_c.metric("Portfolio IRR",
                     f"{port_irr*100:.1f}%" if port_irr is not None else "N/A")

        st.markdown("#### Undrilled Location Economics by Formation")
        if econ_rows:
            st.dataframe(pd.DataFrame(econ_rows), use_container_width=True, hide_index=True)

        if formation_npvs:
            existing_npv = st.session_state.get("existing_well_npv", 0.0) or 0.0
            st.plotly_chart(
                npv_waterfall(formation_npvs, existing_npv),
                use_container_width=True,
            )

        if formation_npvs:
            st.markdown("#### NPV Sensitivity (±20%)")
            sensitivity_inputs = {
                "Oil Price":  ("oil_price", cfg["oil_price"]),
                "Gas Price":  ("gas_price", cfg["gas_price"]),
                "D&C Cost":   ("dc_costs",  None),
                "LOE (all)":  ("loe_all",   None),
                "NRI":        ("nri",        cfg["nri"]),
            }

            sens_rows = []
            base_npv  = total_undrilled_npv
            _fname_map = dict(st.session_state.formation_name_map)
            _tc_params_snap = dict(st.session_state.tc_params)
            _data_version_snap = st.session_state.data_version
            _apis_t_snap = tuple(sorted(section_apis))

            for label, (key, base_val) in sensitivity_inputs.items():
                if key == "dc_costs":
                    low_cfg  = {**cfg, "dc_costs": {f: v * 0.80 for f, v in cfg["dc_costs"].items()}}
                    high_cfg = {**cfg, "dc_costs": {f: v * 1.20 for f, v in cfg["dc_costs"].items()}}
                elif key == "loe_all":
                    low_cfg  = {**cfg, "loe_oil": cfg["loe_oil"] * 0.80, "loe_gas": cfg["loe_gas"] * 0.80,
                                "loe_water": cfg["loe_water"] * 0.80, "loe_fixed": cfg["loe_fixed"] * 0.80}
                    high_cfg = {**cfg, "loe_oil": cfg["loe_oil"] * 1.20, "loe_gas": cfg["loe_gas"] * 1.20,
                                "loe_water": cfg["loe_water"] * 1.20, "loe_fixed": cfg["loe_fixed"] * 1.20}
                else:
                    low_cfg  = {**cfg, key: base_val * 0.80}
                    high_cfg = {**cfg, key: base_val * 1.20}

                def _quick_npv(alt_cfg, _rem=rem_df, _clat=center_lat, _clon=center_lon,
                               _fm=_fname_map, _tcp=_tc_params_snap,
                               _dv=_data_version_snap, _apis_t=_apis_t_snap):
                    total = 0.0
                    for _, r2 in _rem.iterrows():
                        n2 = int(r2["Remaining"])
                        if n2 == 0:
                            continue
                        fm2 = r2["Formation"]
                        fn2 = _fm.get(fm2, [fm2])
                        if not fn2:
                            continue
                        tc2, _ = cache.type_curve(
                            _dv, fm2, _clat, _clon,
                            alt_cfg["offset_radius_mi"], alt_cfg["max_well_age_yr"],
                            _apis_t, tuple(sorted(fn2)),
                        )
                        if tc2["n_wells"] == 0:
                            continue
                        tc_p2 = _tcp.get(fm2)
                        if tc_p2 is None:
                            sp2 = tc2.get("suggested_params", {})
                            tc_p2 = {
                                "oil":   {**sp2.get("oil",   {}), "ramp_months": 0, "q_ramp": 0.0},
                                "gas":   {**sp2.get("gas",   {}), "ramp_months": 0, "q_ramp": 0.0},
                                "water": {**sp2.get("water", {}), "ramp_months": 0, "q_ramp": 0.0},
                            }
                        cf2 = _undrilled_well_cf(tc_p2, alt_cfg, fm2)
                        e2  = well_economics(cf2, alt_cfg["discount_rate"])
                        v2  = e2["npv"]
                        if v2 is not None and np.isfinite(v2):
                            total += v2 * n2
                    return total

                with st.spinner(f"Sensitivity: {label}…"):
                    low_npv  = _quick_npv(low_cfg)
                    high_npv = _quick_npv(high_cfg)

                sens_rows.append({
                    "label":    label,
                    "low_npv":  low_npv,
                    "base_npv": base_npv,
                    "high_npv": high_npv,
                })

            if sens_rows:
                st.plotly_chart(tornado_chart(sens_rows), use_container_width=True)

    except Exception as _e4:
        st.error("An error occurred while computing undrilled economics.")
        st.exception(_e4)
