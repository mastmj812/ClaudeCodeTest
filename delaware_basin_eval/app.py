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
    DEFAULT_LOE_OIL_PER_BBL, DEFAULT_LOE_GAS_PER_MCF,
    DEFAULT_LOE_WATER_PER_BBL, DEFAULT_LOE_FIXED_PER_MO,
    DEFAULT_WOR, DEFAULT_DISCOUNT_RATE, DEFAULT_WELLS_PER_SECTION,
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
def _cached_map_offsets(
    wells_json: str,
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
    wells_df = pd.read_json(io.StringIO(wells_json), orient="split")
    if "api" in wells_df.columns:
        wells_df["api"] = wells_df["api"].astype(str).str.zfill(14)
    for col in ["first_prod_date", "spud_date"]:
        if col in wells_df.columns:
            wells_df[col] = pd.to_datetime(wells_df[col], errors="coerce")

    df = wells_df[wells_df["formation"].fillna("").isin(list(formation_names_tuple))].copy()
    df = df[~df["api"].isin(set(section_apis_tuple))]

    valid = df.dropna(subset=["latitude", "longitude"])
    if valid.empty:
        return valid.reset_index(drop=True)

    dists = haversine_miles(center_lat, center_lon, valid["latitude"].values, valid["longitude"].values)
    return valid[dists <= radius_miles].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _cached_formation_well_counts(
    wells_json: str,
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
    wells_df = pd.read_json(io.StringIO(wells_json), orient="split")
    if "api" in wells_df.columns:
        wells_df["api"] = wells_df["api"].astype(str).str.zfill(14)
    for col in ["first_prod_date", "spud_date"]:
        if col in wells_df.columns:
            wells_df[col] = pd.to_datetime(wells_df[col], errors="coerce")

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
        # existing well total NPV ($) computed in Tab 2, consumed in Tab 4
        "existing_well_npv": 0.0,
        # per-well decline param overrides {api → {qi, di_annual, b}}
        "well_params_override": {},
        # per-formation type curve params {formation → {oil, gas, water: {qi, di_annual, b, dt_annual, ramp_months}}}
        "tc_params": {},
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

    # 8. LOE, discount, well density
    if st.session_state.section_wells is not None:
        with st.expander("⚙️ 8. LOE, Discount & Well Density"):
            st.markdown("**Variable LOE**")
            _c1, _c2 = st.columns(2)
            with _c1:
                loe_oil   = st.number_input("Oil LOE ($/BBL)",   value=DEFAULT_LOE_OIL_PER_BBL,   step=0.25)
                loe_gas   = st.number_input("Gas LOE ($/MCF)",   value=DEFAULT_LOE_GAS_PER_MCF,   step=0.05)
            with _c2:
                loe_water = st.number_input("Water LOE ($/BBL)", value=DEFAULT_LOE_WATER_PER_BBL, step=0.25)
                loe_fixed = st.number_input("Fixed LOE ($/mo)",  value=DEFAULT_LOE_FIXED_PER_MO,  step=100.0,
                                            help="Flat monthly charge per well: pump lease, lift equipment, allocated labor")
            wor = st.number_input(
                "Water-Oil Ratio (undrilled)", value=DEFAULT_WOR, step=0.25, min_value=0.0,
                help="BBL water per BBL oil — used to estimate water disposal cost for undrilled wells. Existing wells use actual production data.",
            )
            discount_rate  = st.number_input("Discount rate (%)", value=DEFAULT_DISCOUNT_RATE * 100, step=0.5) / 100
            lateral_length = st.number_input("Assumed lateral length (ft)", value=10000, step=500)

            st.markdown("**Wells per section (640 acres)**")
            wps_rows = [{"Formation": f, "Wells/Section": DEFAULT_WELLS_PER_SECTION[f]} for f in FORMATIONS]
            wps_df = st.data_editor(
                pd.DataFrame(wps_rows),
                hide_index=True,
                use_container_width=True,
                disabled=["Formation"],
            )
            wells_per_section = dict(zip(wps_df["Formation"], wps_df["Wells/Section"]))

    # Build config dict if section is selected
    if st.session_state.section_wells is not None:
        try:
            st.session_state.cfg = {
                "oil_price":        oil_price,
                "gas_price":        gas_price,
                "ngl_yield":        ngl_yield,
                "ngl_price":        ngl_price,
                "nri":              nri,
                "oil_severance":    oil_sev,
                "gas_severance":    gas_sev,
                "ad_valorem":       ad_val,
                "dc_costs":         dc_costs,
                "loe_oil":          loe_oil,
                "loe_gas":          loe_gas,
                "loe_water":        loe_water,
                "loe_fixed":        loe_fixed,
                "wor":              wor,
                "discount_rate":    discount_rate,
                "lateral_length":   lateral_length,
                "wells_per_section": wells_per_section,
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
        from engineering.decline import generate_stream_profile
        from config import TERMINAL_DI_ANNUAL, MAX_PROJECTION_MONTHS

        with st.spinner("Fitting decline curves…"):
            decline_results = _cached_fit_wells(
                section_wells.to_json(orient="split", date_format="iso"),
                section_prod.to_json(orient="split", date_format="iso"),
            )

        overrides = st.session_state.well_params_override

        # Build per-well economics — apply any user overrides on top of auto-fit
        econ_rows = []
        well_plot_data = []
        all_cashflows = []

        for res in decline_results:
            api = str(res["api"]).zfill(14)
            wprod = section_prod[section_prod["api"] == api]

            # Apply override if present
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
                # Regenerate proj_rates from (possibly overridden) params for chart
                if res["success"]:
                    proj_vols = generate_stream_profile(
                        qi=res["qi"], di_annual=res["Di_monthly"] * 12,
                        b=res["b"], dt_annual=TERMINAL_DI_ANNUAL,
                        ramp_months=0, n_months=MAX_PROJECTION_MONTHS,
                    )
                    proj_rates = proj_vols / 30.44
                    n_actual = len(res["actual_months"])
                    proj_months_chart = [n_actual + i for i in range(len(proj_rates))]
                    proj_rates_chart = proj_rates.tolist()
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

        # Aggregate metrics
        total_npv  = sum(r["NPV ($MM)"]  or 0 for r in econ_rows)
        total_pv10 = sum(r["PV10 ($MM)"] or 0 for r in econ_rows)
        total_eur  = sum(r["EUR (MBOE)"] or 0 for r in econ_rows)
        successful = sum(1 for r in econ_rows if r["Status"] in ("✅", "✏️ Override"))
        st.session_state.existing_well_npv = total_npv * 1_000_000

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Total PV10",         f"${total_pv10:.1f}MM")
        col_b.metric("Total NPV",          f"${total_npv:.1f}MM")
        col_c.metric("Total EUR",          f"{total_eur:,.0f} MBOE")
        col_d.metric("Wells Fit",          f"{successful}/{len(decline_results)}")

        if well_plot_data:
            st.plotly_chart(decline_curve_grid(well_plot_data[:12]), use_container_width=True)
            if len(well_plot_data) > 12:
                st.caption(f"Showing first 12 of {len(well_plot_data)} wells.")

        st.markdown("#### Well-Level Economics")
        econ_df = pd.DataFrame(econ_rows)
        st.dataframe(econ_df, use_container_width=True, hide_index=True)

        # Editable decline parameters
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

with tab3:
    if section_wells is None:
        st.info("Select a section in the sidebar to view the type curve and remaining locations.")
    elif cfg is None:
        st.info("Configure economics in the sidebar to proceed.")
    else:
        from ui.charts import (type_curve_chart, stream_type_curve_chart,
                               formation_well_count_chart)
        from engineering.type_curve import generate_type_curve_profile, export_type_curve_csv
        from datetime import date as _date

        all_data_formations = sorted(wells_df["formation"].dropna().unique().tolist())
        extra = [f for f in all_data_formations if f not in FORMATIONS]
        formation_options = FORMATIONS + extra

        valid_sw = section_wells.dropna(subset=["latitude", "longitude"])
        center_lat = valid_sw["latitude"].mean() if not valid_sw.empty else 31.5
        center_lon = valid_sw["longitude"].mean() if not valid_sw.empty else -104.0
        _section_apis_t = tuple(sorted(section_wells["api"].tolist()))
        _wells_json = wells_df.to_json(orient="split", date_format="iso")
        _prod_json  = prod_df.to_json(orient="split", date_format="iso")

        # ── Formation selector + comp set ────────────────────────────────────
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

        # ── Top row: map (left) + formation well-count chart (right) ─────────
        map_col, count_col = st.columns([3, 2])

        with map_col:
            _map_fnames = effective_fnames if effective_fnames else [selected_formation]
            map_offsets = _cached_map_offsets(
                _wells_json, tuple(sorted(_map_fnames)),
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
                well_counts = _cached_formation_well_counts(
                    _wells_json, center_lat, center_lon,
                    cfg["offset_radius_mi"], cfg["max_well_age_yr"], _section_apis_t,
                )
            st.plotly_chart(
                formation_well_count_chart(well_counts),
                use_container_width=True,
            )

        # ── Load type curve ──────────────────────────────────────────────────
        tc, offsets = None, None
        if effective_fnames:
            with st.spinner(f"Building {selected_formation} type curve…"):
                tc, offsets = _cached_type_curve(
                    _wells_json, _prod_json,
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
            # ── Initialize tc_params from suggested_params on first load ──────
            if selected_formation not in st.session_state.tc_params:
                sp = tc.get("suggested_params", {})
                st.session_state.tc_params[selected_formation] = {
                    "oil":   {**sp.get("oil",   {}), "ramp_months": 0},
                    "gas":   {**sp.get("gas",   {}), "ramp_months": 0},
                    "water": {**sp.get("water", {}), "ramp_months": 0},
                }
            params = st.session_state.tc_params[selected_formation]

            # ── Type curve parameter inputs (3 columns) ───────────────────────
            st.markdown("#### Type Curve Parameters")
            p_col_oil, p_col_gas, p_col_water = st.columns(3)
            changed = False

            def _stream_inputs(col, stream_key: str, label: str, qi_unit: str):
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

            new_oil   = _stream_inputs(p_col_oil,   "oil",   "Oil",   "BOPD/10kft")
            new_gas   = _stream_inputs(p_col_gas,   "gas",   "Gas",   "MCF/d/10kft")
            new_water = _stream_inputs(p_col_water, "water", "Water", "BWPD/10kft")

            # Detect changes and persist
            if (new_oil != params["oil"] or new_gas != params["gas"] or new_water != params["water"]):
                st.session_state.tc_params[selected_formation] = {
                    "oil": new_oil, "gas": new_gas, "water": new_water,
                }
                params = st.session_state.tc_params[selected_formation]

            # ── Generate active profiles ──────────────────────────────────────
            N_MONTHS = 600
            active_oil   = generate_type_curve_profile(params["oil"],   N_MONTHS)
            active_gas   = generate_type_curve_profile(params["gas"],   N_MONTHS)
            active_water = generate_type_curve_profile(params["water"], N_MONTHS)

            # ── Oil type curve chart + stats ──────────────────────────────────
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

            # ── Gas and water mini-charts ─────────────────────────────────────
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

        # ── Remaining locations ───────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### Remaining Locations")
        from engineering.spacing import remaining_locations
        rem_df = remaining_locations(
            section_wells, st.session_state.section_acreage, cfg["wells_per_section"],
        )
        rem_col, rem_metric_col = st.columns([3, 1])
        with rem_col:
            st.dataframe(rem_df, use_container_width=True, hide_index=True)
        with rem_metric_col:
            st.metric("Total Undrilled Locations", int(rem_df["Remaining"].sum()))

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
                from engineering.type_curve import generate_type_curve_profile

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

                    if tc["n_wells"] == 0:
                        econ_rows.append({
                            "Formation": formation, "Remaining Wells": n_remaining,
                            "NPV/Well ($MM)": None, "IRR/Well (%)": None,
                            "Payout/Well (mo)": None, "PV10/Well ($MM)": None,
                            "Total NPV ($MM)": None, "Type Curve Wells": 0,
                            "Note": "No qualifying offset wells found",
                        })
                        continue

                    # Use user-adjusted tc_params if available, else suggested_params from tc
                    if formation in st.session_state.tc_params:
                        tc_p = st.session_state.tc_params[formation]
                    else:
                        sp = tc.get("suggested_params", {})
                        tc_p = {
                            "oil":   {**sp.get("oil",   {}), "ramp_months": 0},
                            "gas":   {**sp.get("gas",   {}), "ramp_months": 0},
                            "water": {**sp.get("water", {}), "ramp_months": 0},
                        }

                    oil_profile   = generate_type_curve_profile(tc_p["oil"],   600)
                    gas_profile   = generate_type_curve_profile(tc_p["gas"],   600)
                    water_profile = generate_type_curve_profile(tc_p["water"], 600)

                    cf_one = build_undrilled_well_cashflow(
                        oil_profile, gas_profile, water_profile, cfg, formation
                    )
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

                    _tc_params_snap = dict(st.session_state.tc_params)

                    def _quick_npv(alt_cfg, _rem=rem_df, _clat=center_lat, _clon=center_lon,
                                   _fm=_fname_map, _tcp=_tc_params_snap):
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
                            if fm2 in _tcp:
                                tc_p2 = _tcp[fm2]
                            else:
                                sp2 = tc2.get("suggested_params", {})
                                tc_p2 = {
                                    "oil":   {**sp2.get("oil",   {}), "ramp_months": 0},
                                    "gas":   {**sp2.get("gas",   {}), "ramp_months": 0},
                                    "water": {**sp2.get("water", {}), "ramp_months": 0},
                                }
                            oil2   = generate_type_curve_profile(tc_p2["oil"],   600)
                            gas2   = generate_type_curve_profile(tc_p2["gas"],   600)
                            water2 = generate_type_curve_profile(tc_p2["water"], 600)
                            cf2 = build_undrilled_well_cashflow(oil2, gas2, water2, alt_cfg, fm2)
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
