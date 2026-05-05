"""Tab 2 — Existing Well Value."""

import pandas as pd
import streamlit as st

from ui import cache
from ui.charts import decline_curve_grid
from economics.cashflow import build_existing_well_cashflow
from economics.metrics import well_economics
from engineering.decline import generate_stream_profile
from config import TERMINAL_DI_ANNUAL, MAX_PROJECTION_MONTHS


def render():
    section_wells = st.session_state.section_wells
    section_prod  = st.session_state.section_prod
    cfg           = st.session_state.cfg

    if section_wells is None:
        st.info("Select a section in the sidebar to analyze existing well value.")
        return
    if cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
        return

    with st.spinner("Fitting decline curves…"):
        decline_results = cache.fit_wells(st.session_state.data_version)

    overrides = st.session_state.well_params_override

    econ_rows = []
    well_plot_data = []
    all_cashflows = []

    for res in decline_results:
        api = str(res["api"]).zfill(14)
        wprod = section_prod[section_prod["api"] == api]

        ov = overrides.get(api)
        if ov:
            res = {**res,
                   "qi": ov["qi"],
                   "Di_monthly": ov["di_annual"] / 12.0,
                   "b": ov["b"],
                   "success": True}

        if res["success"] and not wprod.empty:
            cf = build_existing_well_cashflow(res, wprod, cfg)
            econ = well_economics(cf, cfg["discount_rate"])
            all_cashflows.append(cf)
        else:
            econ = {"npv": None, "pv10": None, "irr": None, "payout": None}

        status = ("✏️ Override" if ov else "✅") if res["success"] else f"⚠️ {res.get('warning','')}"
        econ_rows.append({
            "Well Name":      res["well_name"],
            "Formation":      res["formation"],
            "qi (BOPD)":      round(res["qi"], 0) if res["success"] else None,
            "Di (mo)":        round(res["Di_monthly"], 4) if res["success"] else None,
            "b":              round(res["b"], 2) if res["success"] else None,
            "EUR (MBOE)":     round(res["eur"] / 1000, 1) if res["success"] else None,
            "NPV ($MM)":      round(econ["npv"] / 1e6, 2) if econ["npv"] is not None else None,
            "PV10 ($MM)":     round(econ["pv10"] / 1e6, 2) if econ["pv10"] is not None else None,
            "IRR (%)":        round(econ["irr"] * 100, 1) if econ["irr"] is not None else None,
            "Payout (mo)":    econ["payout"],
            "Status":         status,
        })

        if res.get("actual_months") and len(res["actual_months"]) > 0:
            if res["success"]:
                n_actual = len(res["actual_months"])
                if ov:
                    # Overridden wells: generate from t=0 then slice from n_actual
                    # so the projection continues from the well's current position
                    # on the overridden curve, not a fresh peak start.
                    all_vols = generate_stream_profile(
                        qi=res["qi"], di_annual=res["Di_monthly"] * 12,
                        b=res["b"], dt_annual=TERMINAL_DI_ANNUAL,
                        ramp_months=0, n_months=n_actual + MAX_PROJECTION_MONTHS,
                    )
                    proj_vols_fwd = all_vols[n_actual:]
                    proj_months_chart = list(range(n_actual, n_actual + len(proj_vols_fwd)))
                    proj_rates_chart = (proj_vols_fwd / 30.44).tolist()
                else:
                    # Non-overridden: use fit_decline's pre-computed projection,
                    # which already starts from the last actual data point.
                    proj_months_chart = list(res["proj_months"]) if res["proj_months"] is not None else None
                    proj_rates_chart  = list(res["proj_rates"])  if res["proj_rates"]  is not None else None
            else:
                proj_months_chart = None
                proj_rates_chart = None

            well_plot_data.append({
                "well_name":     res["well_name"],
                "actual_months": res["actual_months"],
                "actual_rates":  res["actual_rates"],
                "fit_months":    list(range(len(res["actual_months"]))) if res["success"] else None,
                "fit_rates":     list(res["fit_rates"]) if res["success"] and res["fit_rates"] is not None else None,
                "proj_months":   proj_months_chart,
                "proj_rates":    proj_rates_chart,
            })

    total_npv  = sum(r["NPV ($MM)"]  or 0 for r in econ_rows)
    total_pv10 = sum(r["PV10 ($MM)"] or 0 for r in econ_rows)
    total_eur  = sum(r["EUR (MBOE)"] or 0 for r in econ_rows)
    successful = sum(1 for r in econ_rows if r["Status"] in ("✅", "✏️ Override"))
    st.session_state.existing_well_npv = total_npv * 1_000_000

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Total PV10",  f"${total_pv10:.1f}MM")
    col_b.metric("Total NPV",   f"${total_npv:.1f}MM")
    col_c.metric("Total EUR",   f"{total_eur:,.0f} MBOE")
    col_d.metric("Wells Fit",   f"{successful}/{len(decline_results)}")

    if well_plot_data:
        st.plotly_chart(decline_curve_grid(well_plot_data[:12]), use_container_width=True)
        if len(well_plot_data) > 12:
            st.caption(f"Showing first 12 of {len(well_plot_data)} wells.")

    st.markdown("#### Well-Level Economics")
    st.dataframe(pd.DataFrame(econ_rows), use_container_width=True, hide_index=True)

    with st.expander("✏️ Edit Decline Parameters", expanded=False):
        st.caption(
            "Override the auto-fitted decline parameters for any well. "
            "Click a cell to edit. Changes take effect after clicking **Apply Overrides**."
        )
        param_rows = []
        for res in decline_results:
            api = str(res["api"]).zfill(14)
            ov = overrides.get(api)
            param_rows.append({
                "_api":          api,
                "Well Name":     res["well_name"],
                "qi (BOPD)":     round(ov["qi"] if ov else (res["qi"] if res["success"] else 0.0), 1),
                "Di annual (%)": round((ov["di_annual"] if ov else (res["Di_monthly"] * 12 if res["success"] else 0.10)) * 100, 2),
                "b":             round(ov["b"] if ov else (res["b"] if res["success"] else 1.2), 3),
            })

        edited_params = st.data_editor(
            pd.DataFrame(param_rows),
            column_config={
                "_api":          st.column_config.TextColumn("API", disabled=True),
                "Well Name":     st.column_config.TextColumn(disabled=True),
                "qi (BOPD)":     st.column_config.NumberColumn(min_value=0.0, step=10.0),
                "Di annual (%)": st.column_config.NumberColumn(min_value=0.1, max_value=500.0, step=1.0),
                "b":             st.column_config.NumberColumn(min_value=0.01, max_value=2.0, step=0.05),
            },
            hide_index=True,
            use_container_width=True,
            key="well_param_editor",
        )

        if st.button("Apply Overrides", type="primary"):
            new_overrides = {}
            for _, row in edited_params.iterrows():
                new_overrides[row["_api"]] = {
                    "qi":        float(row["qi (BOPD)"]),
                    "di_annual": float(row["Di annual (%)"]) / 100.0,
                    "b":         float(row["b"]),
                }
            st.session_state.well_params_override = new_overrides
            st.rerun()
