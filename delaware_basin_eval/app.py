"""
Delaware Basin Property Evaluator
Main Streamlit entry point.
"""

import streamlit as st
import pandas as pd

from data.loader import load_well_header, load_production
from data.validators import validate_wells, validate_production, fix_quarterly_gas
from data.section_filter import get_section_wells
from ui import cache
from config import (
    DEFAULT_PRICE_DECK, DEFAULT_DEDUCTIONS, DEFAULT_DC_COSTS,
    DEFAULT_LOE_OIL_PER_BBL, DEFAULT_LOE_GAS_PER_MCF,
    DEFAULT_LOE_WATER_PER_BBL, DEFAULT_LOE_FIXED_PER_MO,
    DEFAULT_WOR, DEFAULT_DISCOUNT_RATE, DEFAULT_WELLS_PER_SECTION,
    DEFAULT_OFFSET_RADIUS_MI, DEFAULT_MAX_WELL_AGE_YR, FORMATIONS,
)


try:
    from utils.geo import read_shapefile_zip
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

st.set_page_config(
    page_title="Delaware Basin Evaluator",
    page_icon="🛢️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
section[data-testid="stSidebar"] > div:first-child {
    overflow-y: auto;
    max-height: 100vh;
    padding-bottom: 3rem;
}
</style>
""", unsafe_allow_html=True)


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
        # per-formation type curve params {formation → {oil, gas, water: {qi, di_annual, b, dt_annual, ramp_months, q_ramp}}}
        "tc_params": {},
        # bumped whenever wells_df / prod_df / section_wells / section_prod change;
        # used as the cache key for _cached_* functions so they invalidate on data change
        "data_version": 0,
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
                st.session_state.data_version += 1
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
                st.session_state.data_version += 1
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
                    st.session_state.data_version  += 1
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
    from ui import tab_overview
    tab_overview.render()

with tab2:
    from ui import tab_existing
    tab_existing.render()

with tab3:
    from ui import tab_typecurve
    tab_typecurve.render()

with tab4:
    from ui import tab_undrilled
    tab_undrilled.render()

# ── Raw data preview ───────────────────────────────────────────────────────
with st.expander("🔍 Raw data preview", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Well Header** — {len(wells_df):,} wells")
        st.dataframe(wells_df.head(200), use_container_width=True)
    with c2:
        st.markdown(f"**Production** — {len(prod_df):,} rows")
        st.dataframe(prod_df.head(200), use_container_width=True)
