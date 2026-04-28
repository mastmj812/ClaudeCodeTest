"""
Delaware Basin Property Evaluator
Main Streamlit entry point.
"""

import io
import streamlit as st
import pandas as pd
import numpy as np

from data.loader import load_well_header, load_production
from data.validators import validate_wells, validate_production, fix_quarterly_gas
from data.section_filter import get_section_wells
from ui.charts import section_map
from config import (
    DEFAULT_PRICE_DECK, DEFAULT_DEDUCTIONS, DEFAULT_DC_COSTS,
    DEFAULT_LOE_PER_BOE, DEFAULT_DISCOUNT_RATE, DEFAULT_SPACING,
    DEFAULT_OFFSET_RADIUS_MI, DEFAULT_MAX_WELL_AGE_YR, FORMATIONS,
    MIN_LATERAL_FT,
)


# ── Cached heavy computations ──────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _cached_fit_wells(section_wells_json: str, section_prod_json: str):
    """Cache decline curve fits — only re-runs when section changes."""
    from engineering.decline import fit_all_section_wells
    sw = pd.read_json(io.StringIO(section_wells_json), orient="split")
    sp = pd.read_json(io.StringIO(section_prod_json), orient="split")
    # JSON parsing may read all-digit API strings as integers — restore as zero-padded strings
    for df in [sw, sp]:
        if "api" in df.columns:
            df["api"] = df["api"].astype(str).str.zfill(14)
    # Re-parse dates after JSON round-trip
    for col in ["first_prod_date", "spud_date"]:
        if col in sw.columns:
            sw[col] = pd.to_datetime(sw[col], errors="coerce")
    if "prod_date" in sp.columns:
        sp["prod_date"] = pd.to_datetime(sp["prod_date"], errors="coerce")
    return fit_all_section_wells(sw, sp)


@st.cache_data(show_spinner=False)
def _cached_type_curve(
    wells_json: str, prod_json: str,
    formation: str,
    center_lat: float, center_lon: float,
    radius_miles: float, max_well_age_yr: int,
    section_apis_tuple: tuple,
    formation_names_tuple: tuple,
):
    """Cache type curve per formation + filter settings."""
    from engineering.type_curve import get_offset_wells, build_type_curve
    wells_df = pd.read_json(io.StringIO(wells_json), orient="split")
    prod_df  = pd.read_json(io.StringIO(prod_json),  orient="split")
    # JSON parsing may read all-digit API strings as integers — restore as zero-padded strings
    for df in [wells_df, prod_df]:
        if "api" in df.columns:
            df["api"] = df["api"].astype(str).str.zfill(14)
    for col in ["first_prod_date", "spud_date"]:
        if col in wells_df.columns:
            wells_df[col] = pd.to_datetime(wells_df[col], errors="coerce")
    if "prod_date" in prod_df.columns:
        prod_df["prod_date"] = pd.to_datetime(prod_df["prod_date"], errors="coerce")
    offsets = get_offset_wells(
        wells_df, list(formation_names_tuple), center_lat, center_lon,
        radius_miles, max_well_age_yr, set(section_apis_tuple),
    )
    return build_type_curve(offsets, prod_df), offsets

try:
    from utils.geo import read_shapefile_zip, wells_within_radius
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

st.set_page_config(
    page_title="Delaware Basin Evaluator",
    page_icon="🛢️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Session state defaults ─────────────────────────────────────────────────
def _init_state():
    defaults = {
        "wells_df":       None,
        "prod_df":        None,
        "section_wells":  None,
        "section_prod":   None,
        "section_acreage": 640.0,
        "polygon_gdf":    None,
        "well_warnings":  [],
        "prod_warnings":  [],
        # economics config (populated by sidebar)
        "cfg": None,
        # per-formation offset name selections (canonical → list of raw names)
        "formation_name_map": {},
        # user-defined formation mapping (raw ENVInterval value → canonical name)
        "formation_mapping": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🛢️ Delaware Basin Evaluator")
    st.markdown("---")

    # 1. Data upload
    with st.expander("📂 1. Upload Data", expanded=st.session_state.wells_df is None):
        well_file = st.file_uploader(
            "Well Header CSV",
            type=["csv"],
            help="Full Texas Delaware well header export from Enverus / Drillinginfo.",
        )
        prod_file = st.file_uploader(
            "Production History CSV",
            type=["csv"],
            help="Full Texas Delaware production export from Enverus / Drillinginfo.",
        )
        if well_file and prod_file:
            if st.button("Load Data", type="primary", use_container_width=True):
                with st.spinner("Loading well header…"):
                    try:
                        wells = load_well_header(well_file)
                        st.session_state.well_warnings = validate_wells(wells)
                        st.session_state.wells_df = wells
                    except Exception as e:
                        st.error(f"Failed to load well header: {e}")
                        st.stop()
                with st.spinner("Loading production history…"):
                    try:
                        prod = load_production(prod_file)
                        st.session_state.prod_warnings = validate_production(prod)
                        prod = fix_quarterly_gas(prod)
                        st.session_state.prod_df = prod
                    except Exception as e:
                        st.error(f"Failed to load production: {e}")
                        st.stop()
                st.session_state.section_wells = None
                st.session_state.section_prod  = None
                st.success(
                    f"Loaded {len(st.session_state.wells_df):,} wells · "
                    f"{len(st.session_state.prod_df):,} production rows"
                )
                st.rerun()

    # 2. Formation mapping
    if st.session_state.wells_df is not None:
        mapping_done = bool(st.session_state.formation_mapping)
        with st.expander("🏷️ 2. Formation Mapping", expanded=not mapping_done):
            st.caption(
                "Assign each formation name in your data to a canonical zone. "
                "Use the **Maps to** dropdown on each row — then click **Apply**."
            )
            raw_formations = sorted(
                st.session_state.wells_df["_raw_formation"].dropna().unique().tolist()
            ) if "_raw_formation" in st.session_state.wells_df.columns else sorted(
                st.session_state.wells_df["formation"].dropna().unique().tolist()
            )
            canonical_options = FORMATIONS + ["Other"]
            existing = st.session_state.formation_mapping

            rows = []
            for raw in raw_formations:
                if raw in existing:
                    mapped = existing[raw]
                elif raw in FORMATIONS:
                    mapped = raw
                else:
                    from config import FORMATION_ALIASES as _FA
                    mapped = _FA.get(raw.lower().strip(), "Other")
                    if mapped not in canonical_options:
                        mapped = "Other"
                rows.append({"Formation in your data": raw, "Maps to": mapped})

            edited = st.data_editor(
                pd.DataFrame(rows),
                column_config={
                    "Formation in your data": st.column_config.TextColumn(disabled=True),
                    "Maps to": st.column_config.SelectboxColumn(
                        options=canonical_options,
                        required=True,
                    ),
                },
                hide_index=True,
                use_container_width=True,
                key="formation_mapping_editor",
            )

            if st.button("Apply Mapping", type="primary", use_container_width=True):
                mapping = dict(zip(edited["Formation in your data"], edited["Maps to"]))
                st.session_state.formation_mapping = mapping
                raw_col = "_raw_formation" if "_raw_formation" in st.session_state.wells_df.columns else "formation"
                st.session_state.wells_df["formation"] = (
                    st.session_state.wells_df[raw_col]
                    .astype(str)
                    .map(lambda x: mapping.get(x, x))
                )
                st.session_state.section_wells = None
                st.session_state.section_prod  = None
                st.session_state.formation_name_map = {}
                st.success("Mapping applied. Re-select your section.")
                st.rerun()

    # 3. Section selection
    if st.session_state.wells_df is not None:
        with st.expander("📍 3. Select Section", expanded=st.session_state.section_wells is None):
            section_id = st.text_input(
                "Section identifier",
                placeholder="e.g.  T1S R26E Sec 15  or  Abstract 1234",
                help=(
                    "Enter Section/Township/Range (PLSS) or Abstract number. "
                    "Examples: 'T1S R26E Sec 15', 'T01S-R26E-15', 'Abstract 1234'"
                ),
            )
            shp_file = None
            if HAS_GEO:
                shp_file = st.file_uploader(
                    "Or upload boundary shapefile (.zip)",
                    type=["zip"],
                    help="Zip must contain .shp, .dbf, .shx, and optionally .prj files.",
                )
            else:
                st.caption("Install geopandas to enable shapefile upload.")

            if st.button("Filter to Section", type="primary", use_container_width=True):
                polygon_gdf = None
                if shp_file and HAS_GEO:
                    try:
                        polygon_gdf = read_shapefile_zip(shp_file)
                        st.session_state.polygon_gdf = polygon_gdf
                    except Exception as e:
                        st.error(f"Shapefile error: {e}")
                        st.stop()

                section_wells, acreage = get_section_wells(
                    st.session_state.wells_df,
                    identifier=section_id,
                    polygon_gdf=polygon_gdf,
                )
                if section_wells.empty:
                    st.warning("No wells found matching that identifier. Check your input.")
                else:
                    # Join production for section wells
                    section_apis = set(section_wells["api"])
                    section_prod = st.session_state.prod_df[
                        st.session_state.prod_df["api"].isin(section_apis)
                    ].copy()

                    st.session_state.section_wells  = section_wells
                    st.session_state.section_prod   = section_prod
                    st.session_state.section_acreage = acreage
                    st.success(
                        f"{len(section_wells)} wells found · {acreage:,.0f} acres"
                    )
                    st.rerun()

    # 4. Offset filter
    if st.session_state.section_wells is not None:
        with st.expander("🔍 4. Offset Filter"):
            offset_radius = st.slider(
                "Offset radius (miles)", 1, 25,
                int(DEFAULT_OFFSET_RADIUS_MI), 1,
            )
            max_well_age = st.slider(
                "Max well age for type curve (years)", 1, 10,
                DEFAULT_MAX_WELL_AGE_YR, 1,
            )

    # 5. Price deck
    if st.session_state.section_wells is not None:
        with st.expander("💲 5. Price Deck"):
            oil_price  = st.number_input("Oil ($/BBL)",    value=DEFAULT_PRICE_DECK["oil_price"],  step=1.0)
            gas_price  = st.number_input("Gas ($/MMBTU)",  value=DEFAULT_PRICE_DECK["gas_price"],  step=0.10)
            ngl_yield  = st.number_input("NGL yield (BBL/MMCF)", value=DEFAULT_PRICE_DECK["ngl_yield"], step=1.0)
            ngl_price  = st.number_input("NGL ($/BBL)",    value=DEFAULT_PRICE_DECK["ngl_price"],  step=1.0)

    # 6. Revenue deductions
    if st.session_state.section_wells is not None:
        with st.expander("📉 6. Revenue Deductions"):
            nri            = st.slider("NRI", 0.60, 0.90, float(DEFAULT_DEDUCTIONS["nri"]), 0.01)
            oil_sev        = st.number_input("Oil severance (%)",
                                             value=DEFAULT_DEDUCTIONS["oil_severance"] * 100, step=0.1) / 100
            gas_sev        = st.number_input("Gas severance (%)",
                                             value=DEFAULT_DEDUCTIONS["gas_severance"] * 100, step=0.1) / 100
            ad_val         = st.number_input("Ad valorem (%)",
                                             value=DEFAULT_DEDUCTIONS["ad_valorem"] * 100, step=0.1) / 100

    # 7. Well costs (D&C by formation)
    if st.session_state.section_wells is not None:
        with st.expander("🏗️ 7. Well Costs (D&C $MM)"):
            dc_cost_rows = [{"Formation": f, "D&C Cost ($MM)": DEFAULT_DC_COSTS[f]} for f in FORMATIONS]
            dc_df = st.data_editor(
                pd.DataFrame(dc_cost_rows),
                hide_index=True,
                use_container_width=True,
                disabled=["Formation"],
            )
            dc_costs = dict(zip(dc_df["Formation"], dc_df["D&C Cost ($MM)"]))

    # 8. LOE, discount, spacing
    if st.session_state.section_wells is not None:
        with st.expander("⚙️ 8. LOE, Discount & Spacing"):
            loe_per_boe    = st.number_input("LOE ($/BOE)", value=DEFAULT_LOE_PER_BOE, step=0.50,
                                             help="Lease operating expense per barrel of oil equivalent produced. Scales with monthly production volume.")
            discount_rate  = st.number_input("Discount rate (%)", value=DEFAULT_DISCOUNT_RATE * 100, step=0.5) / 100
            lateral_length = st.number_input("Assumed lateral length (ft)", value=10000, step=500)

            st.markdown("**Well spacing (acres/well)**")
            spacing_rows = [{"Formation": f, "Acres/Well": DEFAULT_SPACING[f]} for f in FORMATIONS]
            spacing_df = st.data_editor(
                pd.DataFrame(spacing_rows),
                hide_index=True,
                use_container_width=True,
                disabled=["Formation"],
            )
            spacing = dict(zip(spacing_df["Formation"], spacing_df["Acres/Well"]))

    # Build config dict if section is selected
    if st.session_state.section_wells is not None:
        try:
            st.session_state.cfg = {
                "oil_price":     oil_price,
                "gas_price":     gas_price,
                "ngl_yield":     ngl_yield,
                "ngl_price":     ngl_price,
                "nri":           nri,
                "oil_severance": oil_sev,
                "gas_severance": gas_sev,
                "ad_valorem":    ad_val,
                "dc_costs":      dc_costs,
                "loe_per_boe":   loe_per_boe,
                "discount_rate": discount_rate,
                "lateral_length": lateral_length,
                "spacing":       spacing,
                "offset_radius_mi": offset_radius,
                "max_well_age_yr":  max_well_age,
            }
        except NameError:
            pass  # sidebar widgets not yet rendered


# ── Main area ──────────────────────────────────────────────────────────────
wells_df    = st.session_state.wells_df
prod_df     = st.session_state.prod_df
section_wells = st.session_state.section_wells
section_prod  = st.session_state.section_prod
cfg           = st.session_state.cfg

if wells_df is None:
    st.markdown("## Delaware Basin Property Evaluator")
    st.markdown(
        "Upload your **Well Header** and **Production History** CSVs in the sidebar to begin. "
        "Both should be full Texas Delaware Basin exports from Enverus / Drillinginfo."
    )
    st.markdown("""
**What this tool does:**
1. **Section Overview** — map all wells in a section and nearby offsets
2. **Existing Well Value** — decline-curve-based PV10 / NPV for producing wells
3. **Type Curve & Remaining Locations** — P50 type curve from offset wells, remaining drillable slots
4. **Undrilled Economics** — NPV, IRR, payout, PV10 for each undrilled location
    """)
    st.stop()

# Data quality warnings
all_warnings = st.session_state.well_warnings + st.session_state.prod_warnings
if all_warnings:
    with st.expander(f"⚠️ {len(all_warnings)} data quality notice(s)", expanded=False):
        for w in all_warnings:
            st.warning(w)

# ── Tabs ───────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📍 Section Overview",
    "📈 Existing Well Value",
    "🔬 Type Curve & Locations",
    "💰 Undrilled Economics",
])

# ── Tab 1: Section Overview ────────────────────────────────────────────────
with tab1:
    if section_wells is None:
        st.info("Select a section in the sidebar to see the section overview.")
    else:
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Wells in Section", len(section_wells))
        formations_present = section_wells["formation"].dropna().unique()
        col_b.metric("Formations", len(formations_present))
        col_c.metric("Section Acreage", f"{st.session_state.section_acreage:,.0f} ac")

        # Offset wells for map background
        offset_wells = None
        if cfg and wells_df is not None:
            valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
            if not valid_sw.empty:
                center_lat = valid_sw["latitude"].mean()
                center_lon = valid_sw["longitude"].mean()
                try:
                    offset_wells = wells_within_radius(
                        wells_df, center_lat, center_lon, cfg["offset_radius_mi"]
                    )
                    # Exclude in-section wells from offset display
                    section_apis = set(section_wells["api"])
                    offset_wells = offset_wells[~offset_wells["api"].isin(section_apis)]
                except Exception:
                    offset_wells = None

        st.plotly_chart(
            section_map(section_wells, offset_wells=offset_wells),
            use_container_width=True,
        )

        # Well inventory table
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

with tab2:
    if section_wells is None:
        st.info("Select a section in the sidebar to analyze existing well value.")
    elif cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
    else:
        from economics.cashflow import build_existing_well_cashflow
        from economics.metrics import well_economics
        from ui.charts import decline_curve_grid

        with st.spinner("Fitting decline curves…"):
            decline_results = _cached_fit_wells(
                section_wells.to_json(orient="split", date_format="iso"),
                section_prod.to_json(orient="split", date_format="iso"),
            )

        # Build per-well economics
        econ_rows = []
        well_plot_data = []
        all_cashflows = []

        for res in decline_results:
            api = str(res["api"]).zfill(14)
            wprod = section_prod[section_prod["api"] == api]
            if res["success"] and not wprod.empty:
                cf = build_existing_well_cashflow(res, wprod, cfg)
                econ = well_economics(cf, cfg["discount_rate"])
                all_cashflows.append(cf)
            else:
                econ = {"npv": None, "pv10": None, "irr": None, "payout": None}

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
                "Status":         "✅" if res["success"] else f"⚠️ {res.get('warning','')}",
            })

            if res.get("actual_months") and len(res["actual_months"]) > 0:
                well_plot_data.append({
                    "well_name":   res["well_name"],
                    "actual_months": res["actual_months"],
                    "actual_rates":  res["actual_rates"],
                    "fit_months":    list(range(len(res["actual_months"]))) if res["success"] else None,
                    "fit_rates":     list(res["fit_rates"]) if res["success"] and res["fit_rates"] is not None else None,
                    "proj_months":   list(res["proj_months"]) if res["success"] and res["proj_months"] is not None else None,
                    "proj_rates":    list(res["proj_rates"]) if res["success"] and res["proj_rates"] is not None else None,
                })

        # Aggregate metrics
        total_npv  = sum(r["NPV ($MM)"]  or 0 for r in econ_rows)
        total_pv10 = sum(r["PV10 ($MM)"] or 0 for r in econ_rows)
        total_eur  = sum(r["EUR (MBOE)"] or 0 for r in econ_rows)
        successful = sum(1 for r in econ_rows if r["Status"] == "✅")

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Total PV10",         f"${total_pv10:.1f}MM")
        col_b.metric("Total NPV",          f"${total_npv:.1f}MM")
        col_c.metric("Total EUR",          f"{total_eur:,.0f} MBOE")
        col_d.metric("Wells Fit",          f"{successful}/{len(decline_results)}")

        # Decline curve chart
        if well_plot_data:
            st.plotly_chart(decline_curve_grid(well_plot_data[:12]), use_container_width=True)
            if len(well_plot_data) > 12:
                st.caption(f"Showing first 12 of {len(well_plot_data)} wells.")

        # Economics table
        st.markdown("#### Well-Level Economics")
        econ_df = pd.DataFrame(econ_rows)
        st.dataframe(econ_df, use_container_width=True, hide_index=True)

with tab3:
    if section_wells is None:
        st.info("Select a section in the sidebar to view the type curve and remaining locations.")
    elif cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
    else:
        from ui.charts import type_curve_chart

        # All raw formation names available in the dataset
        all_data_formations = sorted(wells_df["formation"].dropna().unique().tolist())

        # Formation selector — all canonical formations, not just what's in section
        extra = [f for f in all_data_formations if f not in FORMATIONS]
        formation_options = FORMATIONS + extra

        top_left, top_right = st.columns([2, 3])
        with top_left:
            selected_formation = st.selectbox("Formation for type curve", options=formation_options)

        with top_right:
            # Per-formation offset name selection, persisted in session state
            saved_names = st.session_state.formation_name_map.get(selected_formation)
            if saved_names is None:
                # Default: the formation itself if it exists in the data
                saved_names = [selected_formation] if selected_formation in all_data_formations else []
            valid_defaults = [n for n in saved_names if n in all_data_formations]
            offset_names = st.multiselect(
                "Offset well formation names for comp set",
                options=all_data_formations,
                default=valid_defaults,
                help=(
                    "Pick every raw ENVInterval value that represents this zone "
                    "(e.g. 'Wolfcamp B', 'WC-B', 'WF-B'). "
                    "This selection is saved per formation and used in Undrilled Economics."
                ),
            )
            st.session_state.formation_name_map[selected_formation] = offset_names

        valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
        center_lat = valid_sw["latitude"].mean() if not valid_sw.empty else 31.5
        center_lon = valid_sw["longitude"].mean() if not valid_sw.empty else -104.0

        # Run type curve computation
        tc, offsets = None, None
        if offset_names:
            with st.spinner(f"Finding {selected_formation} offset wells…"):
                tc, offsets = _cached_type_curve(
                    wells_df.to_json(orient="split", date_format="iso"),
                    prod_df.to_json(orient="split", date_format="iso"),
                    selected_formation,
                    center_lat, center_lon,
                    cfg["offset_radius_mi"], cfg["max_well_age_yr"],
                    tuple(sorted(section_wells["api"].tolist())),
                    tuple(sorted(offset_names)),
                )
        else:
            st.info("Select at least one formation name in the comp set to build a type curve.")

        # Map — section wells + comp set offset wells
        st.plotly_chart(
            section_map(section_wells, offset_wells=offsets if offsets is not None else None),
            use_container_width=True,
        )

        col_left, col_right = st.columns([3, 2])

        with col_left:
            if tc is not None:
                if tc["n_wells"] == 0:
                    st.warning(
                        f"No qualifying offset wells found for {selected_formation} within "
                        f"{cfg['offset_radius_mi']} miles. Try increasing the radius or max well age."
                    )
                    fc = offsets.attrs.get("filter_counts", {}) if offsets is not None else {}
                    if fc:
                        st.caption(
                            f"Filter breakdown — "
                            f"Total wells: {fc.get('total', '?')} → "
                            f"After formation: {fc.get('after_formation', '?')} → "
                            f"After age ({cfg['max_well_age_yr']}yr): {fc.get('after_age', '?')} → "
                            f"After section exclude: {fc.get('after_section_exclude', '?')} → "
                            f"After lateral ≥ {MIN_LATERAL_FT:,} ft: {fc.get('after_lateral', '?')} → "
                            f"After radius: {fc.get('after_radius', '?')}"
                        )
                else:
                    st.plotly_chart(
                        type_curve_chart(
                            tc["traces"], tc["p10"], tc["p50"], tc["p90"],
                            formation=selected_formation,
                            n_wells=tc["n_wells"],
                        ),
                        use_container_width=True,
                    )
                    if tc["excluded"] > 0:
                        st.caption(
                            f"{tc['excluded']} wells excluded (missing lateral length or production)."
                        )

        with col_right:
            st.markdown("#### Offset Well Stats")
            if tc is not None and tc["n_wells"] > 0:
                st.metric("Wells in comp set", tc["n_wells"])
                st.metric("Median lateral length", f"{tc['median_lateral']:,.0f} ft")
                if offsets is not None and not offsets.empty and "first_prod_date" in offsets.columns:
                    dates = offsets["first_prod_date"].dropna()
                    if not dates.empty:
                        st.metric(
                            "Comp date range",
                            f"{dates.min().strftime('%m/%Y')} – {dates.max().strftime('%m/%Y')}",
                        )

            st.markdown("#### Remaining Locations")
            from engineering.spacing import remaining_locations
            rem_df = remaining_locations(
                section_wells,
                st.session_state.section_acreage,
                cfg["spacing"],
            )
            st.dataframe(rem_df, use_container_width=True, hide_index=True)
            total_remaining = rem_df["Remaining"].sum()
            st.metric("Total Undrilled Locations", int(total_remaining))

with tab4:
    if section_wells is None:
        st.info("Select a section in the sidebar to evaluate undrilled economics.")
    elif cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
    else:
        from engineering.spacing import remaining_locations
        from economics.cashflow import build_undrilled_well_cashflow
        from economics.metrics import well_economics, portfolio_irr
        from ui.charts import npv_waterfall, tornado_chart

        valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
        center_lat = valid_sw["latitude"].mean() if not valid_sw.empty else 31.5
        center_lon = valid_sw["longitude"].mean() if not valid_sw.empty else -104.0
        section_apis = set(section_wells["api"])

        rem_df = remaining_locations(section_wells, st.session_state.section_acreage, cfg["spacing"])

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

                    tc, _ = _cached_type_curve(
                        wells_df.to_json(orient="split", date_format="iso"),
                        prod_df.to_json(orient="split", date_format="iso"),
                        formation, center_lat, center_lon,
                        cfg["offset_radius_mi"], cfg["max_well_age_yr"],
                        tuple(sorted(section_apis)),
                        tuple(sorted(fnames)),
                    )
                    p50 = tc["p50"]

                    if tc["n_wells"] == 0 or np.all(np.isnan(p50)):
                        econ_rows.append({
                            "Formation": formation, "Remaining Wells": n_remaining,
                            "NPV/Well ($MM)": None, "IRR/Well (%)": None,
                            "Payout/Well (mo)": None, "PV10/Well ($MM)": None,
                            "Total NPV ($MM)": None, "Type Curve Wells": 0,
                            "Note": "No qualifying offset wells found",
                        })
                        continue

                    cf_one = build_undrilled_well_cashflow(p50, cfg, formation)
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

            # Grand total metrics
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
                st.plotly_chart(
                    npv_waterfall(formation_npvs, 0.0),
                    use_container_width=True,
                )

            if formation_npvs:
                st.markdown("#### NPV Sensitivity (±20%)")
                sensitivity_inputs = {
                    "Oil Price":  ("oil_price",   cfg["oil_price"]),
                    "Gas Price":  ("gas_price",   cfg["gas_price"]),
                    "D&C Cost":   ("dc_costs",    None),
                    "LOE":        ("loe_per_boe", cfg["loe_per_boe"]),
                    "NRI":        ("nri",         cfg["nri"]),
                }

                sens_rows = []
                base_npv  = total_undrilled_npv
                _fname_map = dict(st.session_state.formation_name_map)

                for label, (key, base_val) in sensitivity_inputs.items():
                    if key == "dc_costs":
                        low_cfg  = {**cfg, "dc_costs": {f: v * 0.80 for f, v in cfg["dc_costs"].items()}}
                        high_cfg = {**cfg, "dc_costs": {f: v * 1.20 for f, v in cfg["dc_costs"].items()}}
                    else:
                        low_cfg  = {**cfg, key: base_val * 0.80}
                        high_cfg = {**cfg, key: base_val * 1.20}

                    def _quick_npv(alt_cfg, _rem=rem_df, _clat=center_lat, _clon=center_lon, _fm=_fname_map):
                        total = 0.0
                        wj = wells_df.to_json(orient="split", date_format="iso")
                        pj = prod_df.to_json(orient="split", date_format="iso")
                        apis_t = tuple(sorted(section_apis))
                        for _, r2 in _rem.iterrows():
                            n2 = int(r2["Remaining"])
                            if n2 == 0:
                                continue
                            fm2 = r2["Formation"]
                            fn2 = _fm.get(fm2, [fm2])
                            if not fn2:
                                continue
                            tc2, _ = _cached_type_curve(
                                wj, pj, fm2, _clat, _clon,
                                alt_cfg["offset_radius_mi"], alt_cfg["max_well_age_yr"],
                                apis_t, tuple(sorted(fn2)),
                            )
                            if tc2["n_wells"] == 0:
                                continue
                            cf2 = build_undrilled_well_cashflow(tc2["p50"], alt_cfg, fm2)
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

# ── Raw data preview ───────────────────────────────────────────────────────
with st.expander("🔍 Raw data preview", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Well Header** — {len(wells_df):,} wells")
        st.dataframe(wells_df.head(200), use_container_width=True)
    with c2:
        st.markdown(f"**Production** — {len(prod_df):,} rows")
        st.dataframe(prod_df.head(200), use_container_width=True)
