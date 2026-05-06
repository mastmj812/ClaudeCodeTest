"""
All Plotly figure factories.
Each function returns a plotly Figure that can be passed to st.plotly_chart.
"""

import math
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np
from config import FORMATIONS

# Formation color palette (consistent across all charts)
FORMATION_COLORS: dict[str, str] = {
    "Upper Avalon":           "#1f77b4",
    "Middle Avalon":          "#4a90d9",
    "Lower Avalon":           "#aec7e8",
    "First Bone Spring":      "#9467bd",
    "Second Bone Spring":     "#8c564b",
    "Third Bone Spring":      "#e377c2",
    "Third Bone Spring Sand": "#f7b6d2",
    "Wolfcamp XY":            "#bcbd22",
    "Wolfcamp A":             "#ff7f0e",
    "Wolfcamp B":             "#d62728",
    "Wolfcamp C":             "#2ca02c",
    "Wolfcamp D":             "#17becf",
    "Woodford":               "#7f7f7f",
    "Other":                  "#c7c7c7",
    "Unknown":                "#c7c7c7",
}


def _formation_color(formation: str) -> str:
    return FORMATION_COLORS.get(formation, FORMATION_COLORS["Unknown"])


# ── Tab 1: Section map ─────────────────────────────────────────────────────

def _radius_circle(
    center_lat: float, center_lon: float, radius_miles: float, n_points: int = 120
) -> tuple[list[float], list[float]]:
    """Generate lat/lon points tracing a circle of radius_miles around a center."""
    R = 3958.8  # Earth radius in miles
    d = radius_miles / R
    lats, lons = [], []
    for i in range(n_points + 1):
        bearing = math.radians(360 * i / n_points)
        lat0 = math.radians(center_lat)
        lon0 = math.radians(center_lon)
        lat1 = math.asin(math.sin(lat0) * math.cos(d) +
                         math.cos(lat0) * math.sin(d) * math.cos(bearing))
        lon1 = lon0 + math.atan2(
            math.sin(bearing) * math.sin(d) * math.cos(lat0),
            math.cos(d) - math.sin(lat0) * math.sin(lat1),
        )
        lats.append(math.degrees(lat1))
        lons.append(math.degrees(lon1))
    return lats, lons


def _lateral_line_coords(grp: pd.DataFrame) -> tuple[list, list]:
    """
    Build lat/lon arrays that draw one line segment per well, with None
    separators so segments don't connect across wells.

    Each well's segment runs from heel→bottom-hole when both heel coords
    (`latitude_heel`/`longitude_heel`) are present (more accurate — the
    stick spans only the horizontal lateral). Otherwise falls back to
    surface→BH. Wells without BH coords contribute nothing.
    """
    if "latitude_bh" not in grp.columns or "longitude_bh" not in grp.columns:
        return [], []
    has_heel = (
        "latitude_heel"  in grp.columns
        and "longitude_heel" in grp.columns
    )
    lats: list = []
    lons: list = []
    for _, w in grp.iterrows():
        if not (pd.notna(w.get("latitude_bh")) and pd.notna(w.get("longitude_bh"))):
            continue
        if has_heel and pd.notna(w.get("latitude_heel")) and pd.notna(w.get("longitude_heel")):
            start_lat, start_lon = w["latitude_heel"], w["longitude_heel"]
        else:
            start_lat, start_lon = w["latitude"], w["longitude"]
        lats.extend([start_lat, w["latitude_bh"], None])
        lons.extend([start_lon, w["longitude_bh"], None])
    return lats, lons


def section_map(
    section_wells: pd.DataFrame,
    offset_wells: pd.DataFrame | None = None,
    polygon_geojson: dict | None = None,
    radius_miles: float | None = None,
    center_lat: float | None = None,
    center_lon: float | None = None,
) -> go.Figure:
    """
    Scatter mapbox showing in-section wells colored by formation,
    offset wells colored by formation (smaller/lower opacity), and
    optional shapefile boundary. When bottom-hole coords are present,
    each well is also drawn as a line from surface to BH.
    """
    fig = go.Figure()

    # Offset wells (background layer) — one trace per formation so colors
    # match the formation_well_count_chart palette
    if offset_wells is not None and not offset_wells.empty:
        off = offset_wells.dropna(subset=["latitude", "longitude"])
        for formation in off["formation"].fillna("Unknown").unique():
            grp = off[off["formation"].fillna("Unknown") == formation]
            color = _formation_color(formation)
            legend_group = f"offset_{formation}"

            line_lats, line_lons = _lateral_line_coords(grp)
            if line_lats:
                fig.add_trace(go.Scattermapbox(
                    lat=line_lats, lon=line_lons,
                    mode="lines",
                    line=dict(color=color, width=1.5),
                    opacity=0.5,
                    name=f"Offset · {formation}",
                    legendgroup=legend_group,
                    showlegend=False,
                    hoverinfo="skip",
                ))

            fig.add_trace(go.Scattermapbox(
                lat=grp["latitude"],
                lon=grp["longitude"],
                mode="markers",
                marker=dict(size=6, color=color, opacity=0.7),
                name=f"Offset · {formation}",
                legendgroup=legend_group,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Formation: " + formation + "<br>"
                    "Operator: %{customdata[1]}<extra></extra>"
                ),
                customdata=grp[["well_name", "operator"]].fillna("—").values,
            ))

    # In-section wells colored by formation
    if not section_wells.empty:
        sw = section_wells.dropna(subset=["latitude", "longitude"])
        for formation in sw["formation"].fillna("Unknown").unique():
            grp = sw[sw["formation"].fillna("Unknown") == formation]
            color = _formation_color(formation)
            legend_group = f"section_{formation}"

            line_lats, line_lons = _lateral_line_coords(grp)
            if line_lats:
                fig.add_trace(go.Scattermapbox(
                    lat=line_lats, lon=line_lons,
                    mode="lines",
                    line=dict(color=color, width=3),
                    name=formation,
                    legendgroup=legend_group,
                    showlegend=False,
                    hoverinfo="skip",
                ))

            fig.add_trace(go.Scattermapbox(
                lat=grp["latitude"],
                lon=grp["longitude"],
                mode="markers",
                marker=dict(size=9, color=color),
                name=formation,
                legendgroup=legend_group,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Formation: " + formation + "<br>"
                    "Lateral: %{customdata[1]:,.0f} ft<br>"
                    "First Prod: %{customdata[2]}<extra></extra>"
                ),
                customdata=grp[["well_name", "lateral_length", "first_prod_date"]].fillna("—").values,
            ))

    # Shapefile boundary
    if polygon_geojson:
        fig.add_trace(go.Scattermapbox(
            mode="lines",
            lon=[coord[0] for feature in polygon_geojson["features"]
                 for coord in feature["geometry"]["coordinates"][0]],
            lat=[coord[1] for feature in polygon_geojson["features"]
                 for coord in feature["geometry"]["coordinates"][0]],
            line=dict(width=2, color="yellow"),
            name="Section boundary",
        ))

    # Map center (use provided coords if given, otherwise derive from section wells)
    if center_lat is None or center_lon is None:
        if not section_wells.empty and section_wells[["latitude", "longitude"]].notna().all(axis=1).any():
            valid = section_wells.dropna(subset=["latitude", "longitude"])
            center_lat = valid["latitude"].mean()
            center_lon = valid["longitude"].mean()
        else:
            center_lat, center_lon = 31.5, -104.0  # TX Delaware default

    # Offset radius circle
    if radius_miles is not None and radius_miles > 0:
        circ_lats, circ_lons = _radius_circle(center_lat, center_lon, radius_miles)
        fig.add_trace(go.Scattermapbox(
            lat=circ_lats,
            lon=circ_lons,
            mode="lines",
            line=dict(color="rgba(255,200,0,0.7)", width=2),
            name=f"{radius_miles:.0f} mi radius",
            hoverinfo="skip",
        ))

    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=10,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=500,
        legend=dict(
            bgcolor="rgba(0,0,0,0.5)",
            font=dict(color="white"),
            x=0.01,
            y=0.99,
        ),
    )
    return fig


# ── Tab 2: Decline curve subplot ───────────────────────────────────────────

def decline_curve_grid(wells_data: list[dict]) -> go.Figure:
    """
    wells_data: list of dicts with keys:
      well_name, actual_months, actual_rates,
      fit_months, fit_rates, proj_months, proj_rates
    Returns a subplot grid, 3 columns.
    """
    from plotly.subplots import make_subplots

    n = len(wells_data)
    ncols = min(3, n)
    nrows = (n + ncols - 1) // ncols

    fig = make_subplots(
        rows=nrows, cols=ncols,
        subplot_titles=[w["well_name"] for w in wells_data],
        shared_xaxes=False, shared_yaxes=False,
        vertical_spacing=0.12,
        horizontal_spacing=0.07,
    )

    for i, w in enumerate(wells_data):
        row = i // ncols + 1
        col = i % ncols + 1

        # Actual production
        fig.add_trace(go.Scatter(
            x=w["actual_months"], y=w["actual_rates"],
            mode="markers", marker=dict(size=4, color="steelblue"),
            name="Actual" if i == 0 else None,
            showlegend=(i == 0),
        ), row=row, col=col)

        # Fitted curve
        if w.get("fit_months") is not None:
            fig.add_trace(go.Scatter(
                x=w["fit_months"], y=w["fit_rates"],
                mode="lines", line=dict(color="orange", width=2),
                name="Fit" if i == 0 else None,
                showlegend=(i == 0),
            ), row=row, col=col)

        # Projection
        if w.get("proj_months") is not None:
            fig.add_trace(go.Scatter(
                x=w["proj_months"], y=w["proj_rates"],
                mode="lines", line=dict(color="orange", width=1.5, dash="dash"),
                name="Projection" if i == 0 else None,
                showlegend=(i == 0),
            ), row=row, col=col)

    fig.update_yaxes(type="log")
    fig.update_layout(
        height=max(300, nrows * 280),
        title_text="Decline Curves (log scale)",
        showlegend=True,
        margin=dict(t=60),
    )
    return fig


# ── Tab 3: Type curve ──────────────────────────────────────────────────────

def type_curve_chart(
    offset_traces: list[dict],
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
    formation: str = "",
    n_wells: int = 0,
    active_curve: np.ndarray | None = None,
    active_label: str = "Active Type Curve",
    phase_label: str = "Oil",
    y_title: str = "Oil Rate (BOPD / 10,000 ft lateral)",
    days_per_month: float = 30.44,
    height: int = 420,
) -> go.Figure:
    """
    Normalized offset well traces + P10/P50/P90 band.
    Optional active_curve overlay (user-adjusted type curve) shown in red.
    active_curve is monthly volumes — converted to daily rates for display.
    Pass offset_traces=[] to skip per-well faint lines (used for gas/water
    where per-well traces aren't stored).
    """
    fig = go.Figure()
    months = np.arange(len(p50))

    # Individual traces
    for i, t in enumerate(offset_traces):
        fig.add_trace(go.Scatter(
            x=t["months"], y=t["rates"],
            mode="lines",
            line=dict(color="lightsteelblue", width=1),
            opacity=0.25,
            name="Offset wells" if i == 0 else None,
            showlegend=(i == 0),
            hoverinfo="skip",
        ))

    # P90–P10 shaded band
    fig.add_trace(go.Scatter(
        x=np.concatenate([months, months[::-1]]),
        y=np.concatenate([p10, p90[::-1]]),
        fill="toself",
        fillcolor="rgba(31,119,180,0.10)",
        line=dict(color="rgba(255,255,255,0)"),
        name="P10–P90",
        hoverinfo="skip",
    ))

    fig.add_trace(go.Scatter(
        x=months, y=p10,
        mode="lines", line=dict(color="rgba(31,119,180,0.5)", width=1.5, dash="dot"),
        name="P10",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=p90,
        mode="lines", line=dict(color="rgba(31,119,180,0.5)", width=1.5, dash="dot"),
        name="P90",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=p50,
        mode="lines", line=dict(color="steelblue", width=3),
        name="P50",
    ))

    # Active type curve overlay
    if active_curve is not None:
        active_rates = np.asarray(active_curve, dtype=float) / days_per_month
        active_months = np.arange(len(active_rates))
        fig.add_trace(go.Scatter(
            x=active_months, y=active_rates,
            mode="lines", line=dict(color="crimson", width=2.5),
            name=active_label,
        ))

    fig.update_layout(
        title=f"{phase_label} Type Curve — {formation} ({n_wells} offset wells, normalized to 10,000 ft)",
        xaxis_title="Months on Production",
        yaxis_title=y_title,
        yaxis_type="log",
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=80),
    )
    return fig


def cumulative_type_curve_chart(
    offset_traces: list[dict],
    cum_p10: np.ndarray,
    cum_p50: np.ndarray,
    cum_p90: np.ndarray,
    formation: str = "",
    active_curve: np.ndarray | None = None,
    active_label: str = "Active Type Curve",
    phase_label: str = "Oil",
    cum_unit_short: str = "MBO",
    cum_unit_scale: float = 1_000.0,
    days_per_month: float = 30.44,
    height: int = 380,
) -> go.Figure:
    """
    Cumulative chart for type curve calibration.

    offset_traces: same format as type_curve_chart — daily rates per 10k ft.
                   Pass [] to skip per-well faint cumulative lines (used
                   for gas/water where per-well traces aren't stored).
    cum_p10/p50/p90: cumulative volume per 10k ft from build_type_curve
                     (BBL for oil/water, MCF for gas).
    active_curve:  monthly volumes from generate_type_curve_profile.
    cum_unit_short: display unit ("MBO", "MMCF", "MBW").
    cum_unit_scale: divisor to convert raw volume → display unit
                    (1000 for MBO/MBW, 1000 for MMCF since input is MCF).
    """
    fig = go.Figure()

    # Per-well faint cumulative traces (skipped if offset_traces is empty)
    for i, t in enumerate(offset_traces):
        rates = np.array(t["rates"], dtype=float)
        cum = np.cumsum(rates * days_per_month) / cum_unit_scale
        fig.add_trace(go.Scatter(
            x=t["months"], y=cum,
            mode="lines", line=dict(color="lightsteelblue", width=1),
            opacity=0.25,
            name="Offset wells" if i == 0 else None,
            showlegend=(i == 0), hoverinfo="skip",
        ))

    months = np.arange(len(cum_p50))
    p10_disp = np.nan_to_num(cum_p10, nan=0.0) / cum_unit_scale
    p50_disp = np.nan_to_num(cum_p50, nan=0.0) / cum_unit_scale
    p90_disp = np.nan_to_num(cum_p90, nan=0.0) / cum_unit_scale

    fig.add_trace(go.Scatter(
        x=np.concatenate([months, months[::-1]]),
        y=np.concatenate([p10_disp, p90_disp[::-1]]),
        fill="toself", fillcolor="rgba(31,119,180,0.10)",
        line=dict(color="rgba(255,255,255,0)"),
        name="P10–P90", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=p10_disp,
        mode="lines", line=dict(color="rgba(31,119,180,0.5)", width=1.5, dash="dot"),
        name="P10",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=p90_disp,
        mode="lines", line=dict(color="rgba(31,119,180,0.5)", width=1.5, dash="dot"),
        name="P90",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=p50_disp,
        mode="lines", line=dict(color="steelblue", width=3),
        name="P50",
    ))

    if active_curve is not None:
        active_cum_disp = np.cumsum(np.asarray(active_curve, dtype=float)) / cum_unit_scale
        fig.add_trace(go.Scatter(
            x=np.arange(len(active_cum_disp)), y=active_cum_disp,
            mode="lines", line=dict(color="crimson", width=2.5),
            name=active_label,
        ))

    fig.update_layout(
        title=f"Cumulative {phase_label} — {formation} (normalized to 10,000 ft)",
        xaxis_title="Months on Production",
        yaxis_title=f"Cumulative {phase_label} ({cum_unit_short} / 10,000 ft lateral)",
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=80),
    )
    return fig


def formation_well_count_chart(counts: dict[str, int]) -> go.Figure:
    """
    Horizontal bar chart showing qualifying offset well counts per formation.
    counts: {formation_name: well_count}
    """
    if not counts:
        fig = go.Figure()
        fig.update_layout(title="No qualifying offset wells found", height=300)
        return fig

    # Sort by count descending, filter to non-zero
    sorted_items = sorted(
        [(f, c) for f, c in counts.items() if c > 0],
        key=lambda x: x[1],
    )
    formations = [item[0] for item in sorted_items]
    well_counts = [item[1] for item in sorted_items]
    colors = [_formation_color(f) for f in formations]

    fig = go.Figure(go.Bar(
        x=well_counts,
        y=formations,
        orientation="h",
        marker_color=colors,
        text=well_counts,
        textposition="outside",
        hovertemplate="%{y}: %{x} wells<extra></extra>",
    ))

    fig.update_layout(
        title="Qualifying Offset Wells by Formation",
        xaxis_title="Well Count",
        height=max(250, len(formations) * 35 + 80),
        margin=dict(l=10, r=40, t=50, b=40),
        yaxis=dict(tickfont=dict(size=11)),
    )
    return fig


# ── Tab 4: Waterfall chart ─────────────────────────────────────────────────

def npv_waterfall(formation_npvs: dict[str, float], total_existing_npv: float) -> go.Figure:
    """Stacked waterfall: existing production NPV + undrilled NPV by formation."""
    labels = ["Existing Production"] + list(formation_npvs.keys()) + ["Total"]
    values = [total_existing_npv] + list(formation_npvs.values())
    total  = sum(values)

    measure = ["absolute"] + ["relative"] * len(formation_npvs) + ["total"]
    text    = [f"${v/1e6:.1f}MM" for v in values] + [f"${total/1e6:.1f}MM"]
    y_vals  = values + [total]

    fig = go.Figure(go.Waterfall(
        name="NPV",
        orientation="v",
        measure=measure,
        x=labels,
        y=y_vals,
        text=text,
        textposition="outside",
        connector=dict(line=dict(color="rgb(63,63,63)")),
        increasing=dict(marker=dict(color="#2ca02c")),
        decreasing=dict(marker=dict(color="#d62728")),
        totals=dict(marker=dict(color="steelblue")),
    ))
    fig.update_layout(
        title="NPV Contribution by Category",
        yaxis_title="NPV ($)",
        height=420,
        margin=dict(t=60),
    )
    return fig


# ── Tab 4: Tornado chart ───────────────────────────────────────────────────

def tornado_chart(sensitivities: list[dict]) -> go.Figure:
    """
    sensitivities: list of {label, low_npv, base_npv, high_npv}
    Sorted by impact magnitude.
    """
    df = pd.DataFrame(sensitivities)
    df["range"] = (df["high_npv"] - df["low_npv"]).abs()
    df = df.sort_values("range", ascending=True)

    base = df["base_npv"].iloc[0]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=df["label"],
        x=df["low_npv"] - base,
        orientation="h",
        name="Low (-20%)",
        marker_color="#d62728",
        base=base,
    ))
    fig.add_trace(go.Bar(
        y=df["label"],
        x=df["high_npv"] - base,
        orientation="h",
        name="High (+20%)",
        marker_color="#2ca02c",
        base=base,
    ))
    fig.add_vline(x=base, line_dash="dash", line_color="black", line_width=1)

    fig.update_layout(
        title="NPV Sensitivity (±20% on key inputs)",
        xaxis_title="NPV ($)",
        barmode="overlay",
        height=350,
        margin=dict(t=60),
    )
    return fig
