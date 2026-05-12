# Delaware Basin Property Evaluator

A Streamlit web app for evaluating oil & gas acreage in the Delaware Basin. Loads
Enverus / Drillinginfo well-header and production CSVs, filters to a section or
drawn AOI, fits Arps decline curves, builds P10/P50/P90 type curves by formation,
and runs cashflow economics on existing and undrilled wells.

## Setup

Requires **Python 3.11+** and the dependencies in `delaware_basin_eval/requirements.txt`.

```bash
cd delaware_basin_eval
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

The optional `geopandas`, `streamlit-folium`, and `streamlit-plotly-events`
packages enable the AOI draw, shapefile import, and click-to-select formation
features respectively — the app falls back gracefully if any are missing.

## Running

```bash
streamlit run delaware_basin_eval/app.py
```

Opens at `http://localhost:8501`.

**Windows shortcut:** double-click `run.bat` in the repo root. It activates the
venv at `.venv\` and launches the app in one step.

## Workflow

1. **Upload CSVs** (sidebar §1) — well header + production. Schema is loose: the
   loader maps common column aliases automatically.
2. **Map formations** (§2) — confirm or override the canonical formation name
   for each raw Enverus value.
3. *(Optional)* **Upload directional surveys** (§2b) — heel coords get extracted
   lazily and cached to `data_cache/heels.parquet` so subsequent sessions skip
   the heavy CSV scan.
4. **Select your section / AOI** (§3) — either type a PLSS / abstract ID, upload
   a shapefile zip, or pick the *Draw AOI* radio in §4 and sketch a polygon on
   the map.
5. **Configure economics** (§§5–8) — price deck, NRI / severance / ad valorem,
   D&C costs, LOE, discount rate, wells-per-section assumption.
6. **Read the four tabs** — Overview (map + well editor), Existing wells
   (decline fits + NPV), Type Curve & Locations (build P10/P50/P90 from offsets;
   tune ramp/qi/Di/b/Dt per phase), Undrilled (NPV/IRR/payout by formation +
   ±20% sensitivity tornado).

## Tests

```bash
cd delaware_basin_eval
pytest
```

Tests cover the modeling math (decline ramp + projection, gas revenue units,
cashflow assembly with D&C fallback warnings). The UI tabs are not unit-tested
— smoke-test them by running the app.

## Layout

```
delaware_basin_eval/
├── app.py              # Streamlit entry — sidebar + tab dispatch
├── config.py           # Canonical formations, aliases, default economics
├── data/               # CSV loaders, validators, section/AOI filtering, surveys → heels
├── engineering/        # Arps decline, normalization, spacing, type-curve construction
├── economics/          # Revenue, cashflow, NPV/IRR/payout
├── ui/                 # Streamlit tabs + chart helpers + cache wrappers
├── utils/              # Geo helpers, number formatting
└── tests/              # pytest goldens for the modeling core
data_cache/             # Gitignored — directional surveys CSV + heels.parquet
```

For deeper architectural notes (session state contract, caching strategy,
optional-import flags), see [CLAUDE.md](CLAUDE.md).
