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
    MIN_LATERAL_FT,
)

DEFAULT_MAX_LATERAL_FT = 25_000  # upper cap for the offset-filter range slider


try:
    from utils.geo import read_shapefile_zip, geojson_to_gdf
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

try:
    import folium
    from folium.plugins import Draw
    from streamlit_folium import st_folium
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False

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
        # bumped whenever wells_df / prod_df / section_wells / section_prod / AOI change;
        # used as the cache key for _cached_* functions so they invalidate on data change
        "data_version": 0,
        # offset-area-of-interest (drawn polygon) — overrides radius filter when set
        "offset_aoi_geojson": None,
        "offset_aoi_gdf":     None,
        # directional surveys (optional) — path to the cached CSV on disk
        "dir_surveys_path": None,
        # extracted heel coords {api: (heel_lat, heel_lon)} — populated lazily
        "heels": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Auto-detect a previously cached directional surveys file
    if st.session_state.dir_surveys_path is None:
        from pathlib import Path
        existing = Path("data_cache") / "directional_surveys.csv"
        if existing.exists():
            st.session_state.dir_surveys_path = str(existing)

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

    # 2b. Directional surveys (optional) — for heel→BH lateral sticks on the map
    if st.session_state.wells_df is not None:
        with st.expander("📐 2b. Directional Surveys (optional)", expanded=False):
            st.caption(
                "Upload directional surveys to draw lateral sticks from **heel→BH** "
                "instead of surface→BH. Without surveys, the existing surface→BH "
                "fallback still works."
            )
            dir_file = st.file_uploader(
                "Directional surveys CSV",
                type=["csv"],
                help="Enverus / Drillinginfo sampled directional surveys export.",
                key="dir_surveys_uploader",
            )
            if dir_file is not None:
                if st.button("Save & use", type="primary", use_container_width=True):
                    from pathlib import Path
                    cache_dir = Path("data_cache")
                    cache_dir.mkdir(exist_ok=True)
                    dest = cache_dir / "directional_surveys.csv"
                    with open(dest, "wb") as f:
                        f.write(dir_file.getbuffer())
                    # New file → invalidate prior heels cache
                    (cache_dir / "heels.parquet").unlink(missing_ok=True)
                    st.session_state.dir_surveys_path = str(dest)
                    st.session_state.heels = {}
                    st.session_state.data_version += 1
                    st.success(f"Saved ({dest.stat().st_size / 1e6:.1f} MB). Heels will rebuild on demand.")
                    st.rerun()
            elif st.session_state.dir_surveys_path:
                st.caption(f"Currently using: `{st.session_state.dir_surveys_path}`")
                if st.button("Clear surveys", use_container_width=True):
                    st.session_state.dir_surveys_path = None
                    st.session_state.heels = {}
                    from pathlib import Path
                    (Path("data_cache") / "directional_surveys.csv").unlink(missing_ok=True)
                    (Path("data_cache") / "heels.parquet").unlink(missing_ok=True)
                    st.session_state.data_version += 1
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
            offset_mode = st.radio(
                "Filter mode",
                ["Radius", "Draw AOI"],
                horizontal=True,
                key="offset_mode",
                help="Radius: simple circle around the section. Draw AOI: sketch a custom area on the map.",
            )

            offset_radius = int(DEFAULT_OFFSET_RADIUS_MI)

            if offset_mode == "Radius":
                # Switching back to radius mode: drop any active AOI polygon
                if st.session_state.offset_aoi_gdf is not None:
                    st.session_state.offset_aoi_gdf = None
                    st.session_state.data_version += 1
                offset_radius = st.slider(
                    "Offset radius (miles)", 1, 25,
                    int(DEFAULT_OFFSET_RADIUS_MI), 1,
                )
            else:
                if not HAS_FOLIUM:
                    st.error(
                        "Drawing requires `streamlit-folium` and `folium`. "
                        "Run `pip install -r requirements.txt` and restart."
                    )
                    offset_radius = st.slider(
                        "Offset radius (miles)", 1, 25,
                        int(DEFAULT_OFFSET_RADIUS_MI), 1,
                    )
                else:
                    # Center on section centroid for the draw map
                    sec = st.session_state.section_wells
                    valid_sec = sec.dropna(subset=["latitude", "longitude"])
                    if not valid_sec.empty:
                        clat = float(valid_sec["latitude"].mean())
                        clon = float(valid_sec["longitude"].mean())
                    else:
                        clat, clon = 31.5, -104.0

                    fmap = folium.Map(location=[clat, clon], zoom_start=11, control_scale=True)
                    Draw(
                        export=False,
                        position="topleft",
                        draw_options={
                            "polyline":  False,
                            "circle":    False,
                            "circlemarker": False,
                            "marker":    False,
                            "rectangle": True,
                            "polygon":   True,
                        },
                        edit_options={"edit": True, "remove": True},
                    ).add_to(fmap)

                    # Plot section wells as markers for visual reference
                    for _, w in valid_sec.iterrows():
                        folium.CircleMarker(
                            location=[w["latitude"], w["longitude"]],
                            radius=4, color="#ff6600", fill=True, fill_opacity=0.8,
                        ).add_to(fmap)

                    # If a polygon was previously applied, render it
                    if st.session_state.offset_aoi_geojson:
                        folium.GeoJson(
                            st.session_state.offset_aoi_geojson,
                            style_function=lambda _f: {"color": "yellow", "weight": 2, "fillOpacity": 0.1},
                        ).add_to(fmap)

                    map_state = st_folium(
                        fmap, height=350, width=None,
                        returned_objects=["all_drawings"],
                        key="offset_aoi_map",
                    )

                    drawings = (map_state or {}).get("all_drawings") or []
                    btn_a, btn_b = st.columns(2)
                    with btn_a:
                        if st.button("Apply AOI", type="primary", use_container_width=True, disabled=not drawings):
                            features = []
                            for d in drawings:
                                if d.get("type") == "Feature":
                                    features.append(d)
                                else:
                                    features.append({"type": "Feature", "properties": {}, "geometry": d})
                            new_geojson = {"type": "FeatureCollection", "features": features}
                            try:
                                new_gdf = geojson_to_gdf(new_geojson)
                                st.session_state.offset_aoi_geojson = new_geojson
                                st.session_state.offset_aoi_gdf = new_gdf
                                st.session_state.data_version += 1
                                st.success("AOI applied.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Could not apply AOI: {e}")
                    with btn_b:
                        if st.button("Clear AOI", use_container_width=True,
                                     disabled=st.session_state.offset_aoi_gdf is None):
                            st.session_state.offset_aoi_geojson = None
                            st.session_state.offset_aoi_gdf = None
                            st.session_state.data_version += 1
                            st.rerun()

                    if st.session_state.offset_aoi_gdf is not None:
                        st.caption("✅ AOI active — offsets restricted to drawn polygon.")
                    else:
                        st.caption("Draw a polygon or rectangle, then click **Apply AOI**.")

            max_well_age = st.slider(
                "Max well age for type curve (years)", 1, 10,
                DEFAULT_MAX_WELL_AGE_YR, 1,
            )

            lateral_min, lateral_max = st.slider(
                "Lateral length range (ft)",
                min_value=1_000, max_value=DEFAULT_MAX_LATERAL_FT,
                value=(int(MIN_LATERAL_FT), DEFAULT_MAX_LATERAL_FT),
                step=500,
                help=(
                    "Offset wells outside this range are excluded from the type-curve "
                    "comp set. Short laterals (≪10k ft) and very long laterals (≫10k ft) "
                    "don't normalize linearly to the 10,000-ft basis. Map display is "
                    "unaffected — this only filters the comp set."
                ),
            )

    # 5. Price deck
    if st.session_state.section_wells is not None:
        with st.expander("💲 5. Price Deck"):
            oil_price  = st.number_input("Oil ($/BBL)",    value=DEFAULT_PRICE_DECK["oil_price"],  step=1.0)
            gas_price  = st.number_input("Gas ($/MCF)",    value=DEFAULT_PRICE_DECK["gas_price"],  step=0.10)
            ngl_yield  = st.number_input("NGL yield (BBL/MMCF)", value=DEFAULT_PRICE_DECK["ngl_yield"], step=1.0)
            ngl_price  = st.number_input("NGL ($/BBL)",    value=DEFAULT_PRICE_DECK["ngl_price"],  step=1.0)

    # 6. Revenue deductions
    if st.session_state.section_wells is not None:
        with st.expander("📉 6. Revenue Deductions"):
            wi             = st.slider(
                "Default WI", 0.10, 1.00, 1.00, 0.01,
                help="Working interest used when a well has no per-well WI set. "
                     "Scales LOE and D&C costs. Per-well overrides in Tab 1 take precedence.",
            )
            nri            = st.slider(
                "Default NRI", 0.50, 1.00, float(DEFAULT_DEDUCTIONS["nri"]), 0.01,
                help="Net revenue interest used when a well has no per-well NRI set. "
                     "Per-well overrides in Tab 1 take precedence.",
            )
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
                "wi":               wi,
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
                "min_lateral_ft":   lateral_min,
                "max_lateral_ft":   lateral_max,
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
