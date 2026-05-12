"""
Lateral-line coordinate builder for the section map. Vectorized version must
match the old per-row semantics: one segment per well from start→BH, with
None separators so plotly doesn't connect them. Heel coords are preferred
when present; otherwise the segment starts at the surface.
"""

import math
import numpy as np
import pandas as pd
import pytest

from ui.charts import _lateral_line_coords


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_empty_df_returns_empty_lists():
    lats, lons = _lateral_line_coords(_df([]))
    assert lats == []
    assert lons == []


def test_missing_bh_columns_returns_empty_lists():
    df = _df([{"latitude": 31.5, "longitude": -104.0}])
    lats, lons = _lateral_line_coords(df)
    assert lats == []
    assert lons == []


def test_uses_heel_when_present():
    df = _df([{
        "latitude": 31.50, "longitude": -104.00,
        "latitude_bh": 31.55, "longitude_bh": -104.05,
        "latitude_heel": 31.51, "longitude_heel": -104.01,
    }])
    lats, lons = _lateral_line_coords(df)
    assert lats == [31.51, 31.55, None]
    assert lons == [-104.01, -104.05, None]


def test_falls_back_to_surface_when_heel_missing():
    df = _df([{
        "latitude": 31.50, "longitude": -104.00,
        "latitude_bh": 31.55, "longitude_bh": -104.05,
        "latitude_heel": float("nan"), "longitude_heel": float("nan"),
    }])
    lats, lons = _lateral_line_coords(df)
    assert lats == [31.50, 31.55, None]
    assert lons == [-104.00, -104.05, None]


def test_skips_wells_without_bh():
    df = _df([
        {"latitude": 31.50, "longitude": -104.00,
         "latitude_bh": 31.55, "longitude_bh": -104.05},
        {"latitude": 31.60, "longitude": -104.10,
         "latitude_bh": float("nan"), "longitude_bh": float("nan")},
        {"latitude": 31.70, "longitude": -104.20,
         "latitude_bh": 31.75, "longitude_bh": -104.25},
    ])
    lats, lons = _lateral_line_coords(df)
    # Two wells contribute: indices 0 and 2. None separators after each.
    assert lats == [31.50, 31.55, None, 31.70, 31.75, None]
    assert lons == [-104.00, -104.05, None, -104.20, -104.25, None]


def test_mixed_heel_and_surface_fallback():
    df = _df([
        {"latitude": 31.50, "longitude": -104.00,
         "latitude_bh": 31.55, "longitude_bh": -104.05,
         "latitude_heel": 31.51, "longitude_heel": -104.01},  # heel
        {"latitude": 31.60, "longitude": -104.10,
         "latitude_bh": 31.65, "longitude_bh": -104.15,
         "latitude_heel": float("nan"), "longitude_heel": float("nan")},  # surface
    ])
    lats, lons = _lateral_line_coords(df)
    assert lats == [31.51, 31.55, None, 31.60, 31.65, None]
    assert lons == [-104.01, -104.05, None, -104.10, -104.15, None]


def test_no_heel_columns_at_all_uses_surface():
    df = _df([
        {"latitude": 31.50, "longitude": -104.00,
         "latitude_bh": 31.55, "longitude_bh": -104.05},
    ])
    lats, lons = _lateral_line_coords(df)
    assert lats == [31.50, 31.55, None]
    assert lons == [-104.00, -104.05, None]


def test_large_input_completes_quickly():
    # 5000 wells used to be the kind of input that triggered map-render lag.
    # Vectorized version should breeze through it.
    n = 5000
    rng = np.random.default_rng(seed=0)
    df = pd.DataFrame({
        "latitude":       31.5 + rng.uniform(-0.5, 0.5, size=n),
        "longitude":     -104.0 + rng.uniform(-0.5, 0.5, size=n),
        "latitude_bh":    31.5 + rng.uniform(-0.5, 0.5, size=n),
        "longitude_bh":  -104.0 + rng.uniform(-0.5, 0.5, size=n),
        "latitude_heel":  31.5 + rng.uniform(-0.5, 0.5, size=n),
        "longitude_heel":-104.0 + rng.uniform(-0.5, 0.5, size=n),
    })
    lats, lons = _lateral_line_coords(df)
    # 3 entries per well (start, end, None separator)
    assert len(lats) == 3 * n
    assert len(lons) == 3 * n
    assert all(math.isnan(v) if isinstance(v, float) and v != v else True for v in lats)
