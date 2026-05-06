# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Delaware Basin Property Evaluator** — Streamlit web app for evaluating oil & gas acreage in the Delaware Basin (Texas). Loads Enverus/Drillinginfo well-header and production CSVs, filters to a section/polygon of interest, fits decline curves, builds type curves by formation, and runs cashflow economics on existing and undrilled wells.

The actual app lives in the nested `delaware_basin_eval/` subdirectory.

## Running

```
cd delaware_basin_eval
pip install -r requirements.txt   # first time only, or after dep changes
streamlit run app.py
```

Opens at `http://localhost:8501`. On Windows, double-clicking `run.bat` in the project root activates the venv and launches the app in one step.

## Git Workflow

After completing any meaningful unit of work, commit and push:

```
git add <files>
git commit -m "short, imperative summary of what changed"
git push
```

- Commit after each logical change (feature added, bug fixed, refactor done) — not per file save
- Keep commit messages concise and imperative
- Never let significant work sit uncommitted

## Architecture

```
delaware_basin_eval/
├── app.py              # Streamlit entry — sidebar (upload, formation mapping, AOI/draw, surveys, econ config) + tab dispatch
├── config.py           # Canonical formation list, formation aliases, default economic constants
├── data/               # CSV loaders, schema validators, section/polygon filtering, directional surveys → heels
├── engineering/        # Arps decline fits, normalization, spacing, type-curve construction
├── economics/          # Revenue, cashflow, NPV/IRR/payout metrics
├── ui/                 # Streamlit tabs (overview, existing, type curve, undrilled), charts, caching
└── utils/              # Number formatting, shapefile/geo helpers
data_cache/             # Gitignored — saved directional surveys CSV + heels.parquet
```

**Formation handling:** all formation names flow through `config.FORMATIONS` (canonical list) and `config.FORMATION_ALIASES`. Never hardcode formation strings elsewhere — the user maps raw Enverus values to canonical names via the sidebar, and downstream modules read the mapped values from session state.

**Session state contract** (`st.session_state` keys set by `app._init_state`):
- `wells_df`, `prod_df` — full loaded datasets (wells_df includes optional `latitude_bh`/`longitude_bh` columns)
- `section_wells`, `section_prod` — filtered to the area of interest; `section_wells` can be further trimmed by the per-well include/exclude editor on Tab 1
- `formation_mapping` — `{raw_name: canonical_name}` from the mapping UI
- `tc_params` — `{formation: {oil|gas|water: {qi, di_annual, b, dt_annual, ramp_months, q_ramp}}}`. First-time defaults: `ramp_months=1`, `q_ramp=P50[0]`, `b=1.0`
- `well_params_override` — per-API decline overrides `{api: {qi, di_annual, b}}`
- `cfg` — economics config object built by the sidebar
- `data_version` — bumped on data change, AOI change, or directional surveys upload; used as cache key for `_cached_*` functions in `ui/cache.py`
- `offset_aoi_geojson`, `offset_aoi_gdf` — drawn polygon for the offset comp set; when `offset_aoi_gdf` is non-None, all offset filtering uses spatial containment instead of the radius slider
- `dir_surveys_path` — path to the cached directional surveys CSV under `data_cache/`
- `heels` — `{api: (heel_lat, heel_lon)}` populated lazily from the directional surveys; persisted to `data_cache/heels.parquet`

**Caching:** any expensive computation that depends on loaded data should be wrapped in `ui/cache.py` and keyed on `data_version` so it invalidates when the user reloads CSVs, applies an AOI, or uploads directional surveys.

**Optional geo:** `utils/geo.read_shapefile_zip` and `geojson_to_gdf` require `geopandas`. `streamlit-folium` + `folium` power the AOI draw tool. `streamlit-plotly-events` enables click-to-select on the formation bar chart. `app.py` guards each import with a `HAS_*` flag and falls back gracefully when a package is missing.

**Offset filter modes** (sidebar section 4):
- *Radius* (default): `wells_within_radius` haversine filter using `cfg["offset_radius_mi"]`
- *Draw AOI*: `st_folium` map with the Leaflet `Draw` plugin; user draws a polygon/rectangle and clicks **Apply AOI**, which captures the GeoJSON, builds a GeoDataFrame, stores it in session state, and bumps `data_version`. All downstream offset filtering routes through `utils/geo.filter_offsets`, which prefers `aoi_gdf` over the radius.

**Lateral sticks on the map** (`ui/charts.section_map` → `_lateral_line_coords`):
- Each well is drawn as a line segment from start→bottom-hole.
- Start point preference: `heel` if the well has `latitude_heel`/`longitude_heel` (extracted from directional surveys), else `surface`.
- Wells without BH coords render only as a marker.

**Directional surveys / heel extraction** (`data/directional.py`):
- User uploads the surveys CSV in sidebar section 2b; it's saved to `data_cache/directional_surveys.csv` so subsequent sessions auto-detect it.
- `ensure_heels_for(apis)` is called from `tab_overview.py` and `tab_typecurve.py` immediately before the section_map is rendered. It checks `data_cache/heels.parquet` for cached heels and only reads the surveys CSV (chunked, with a column subset) for APIs not yet cached.
- Heel definition: first survey station (sorted by `MeasuredDepth_FT`) with `Inclination_DEG >= 80.0` AND `CoordinateSource == "ACTUAL"`. API_UWI hyphens are stripped and the result zero-padded to 14 to match `data.loader._standardize_api`.

**Type curve tab (Tab 3) layout:**
- Rate-vs-month and cumulative-vs-month charts render side-by-side per phase (oil, gas, water stacked vertically).
- The formation bar chart on the right is wired through `streamlit-plotly-events` — clicking a bar updates `selected_formation_tab3` in session state and reruns. Falls back to a static chart if the package is missing.
