"""
Directional surveys heel-extraction cache. Regression: APIs queried but never
found (no surveys, or never crossed 80° inclination) must be recorded as
"scanned" so the next call short-circuits instead of re-reading the CSV.
"""

import io
from pathlib import Path

import pandas as pd
import pytest

from data.directional import (
    _normalize_api,
    _read_cache,
    _write_cache,
    extract_heels,
    load_or_compute_heels,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _surveys_csv(rows: list[dict]) -> io.StringIO:
    """Build an in-memory directional-surveys CSV with the columns the loader expects."""
    cols = ["API_UWI", "MeasuredDepth_FT", "CoordinateSource",
            "Inclination_DEG", "Latitude", "Longitude"]
    df = pd.DataFrame(rows, columns=cols)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


# ── _normalize_api ─────────────────────────────────────────────────────────

def test_normalize_api_strips_dashes_and_pads_to_14():
    s = pd.Series(["42-345-12345", "42345123456789", "1234567890"])
    out = _normalize_api(s).tolist()
    assert out[0] == "00004234512345"
    assert out[1] == "42345123456789"
    assert out[2] == "00001234567890"


def test_normalize_api_truncates_overlong():
    s = pd.Series(["12345678901234567890"])
    assert _normalize_api(s).iloc[0] == "12345678901234"


# ── Cache roundtrip ────────────────────────────────────────────────────────

def test_cache_roundtrip_preserves_heels_and_scanned(tmp_path: Path):
    cache = tmp_path / "heels.parquet"
    heels = {"00000000000001": (31.5, -104.0), "00000000000002": (31.6, -104.1)}
    # Include a third API that was scanned but had no heel.
    scanned = {"00000000000001", "00000000000002", "00000000000003"}
    _write_cache(cache, heels, scanned)

    heels_back, scanned_back = _read_cache(cache)
    assert heels_back == heels
    assert scanned_back == scanned


def test_read_cache_missing_file_returns_empty(tmp_path: Path):
    heels, scanned = _read_cache(tmp_path / "nonexistent.parquet")
    assert heels == {}
    assert scanned == set()


# ── extract_heels ──────────────────────────────────────────────────────────

def test_extract_heels_finds_first_station_crossing_threshold():
    csv = _surveys_csv([
        # Well A: vertical section then crosses 80° at MD=8500
        {"API_UWI": "00000000000001", "MeasuredDepth_FT": 7000, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 5.0, "Latitude": 31.40, "Longitude": -104.00},
        {"API_UWI": "00000000000001", "MeasuredDepth_FT": 8500, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 82.0, "Latitude": 31.50, "Longitude": -104.10},
        {"API_UWI": "00000000000001", "MeasuredDepth_FT": 9500, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 89.0, "Latitude": 31.51, "Longitude": -104.11},
    ])
    heels, scanned = extract_heels(csv, {"00000000000001"})
    assert scanned == {"00000000000001"}
    assert heels == {"00000000000001": (31.50, -104.10)}


def test_extract_heels_returns_scanned_even_when_no_heel_found():
    # Well B is in the surveys but never crosses 80° — must be in scanned but not heels.
    csv = _surveys_csv([
        {"API_UWI": "00000000000002", "MeasuredDepth_FT": 7000, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 5.0, "Latitude": 31.40, "Longitude": -104.00},
        {"API_UWI": "00000000000002", "MeasuredDepth_FT": 8000, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 60.0, "Latitude": 31.45, "Longitude": -104.05},
    ])
    heels, scanned = extract_heels(csv, {"00000000000002", "00000000000003"})
    assert scanned == {"00000000000002", "00000000000003"}
    assert heels == {}


def test_extract_heels_skips_non_actual_coordinate_source():
    # Even though inclination > 80°, the CoordinateSource isn't ACTUAL, so skip it.
    csv = _surveys_csv([
        {"API_UWI": "00000000000004", "MeasuredDepth_FT": 8500, "CoordinateSource": "PLANNED",
         "Inclination_DEG": 85.0, "Latitude": 31.50, "Longitude": -104.10},
    ])
    heels, _ = extract_heels(csv, {"00000000000004"})
    assert heels == {}


# ── load_or_compute_heels: the regression ──────────────────────────────────

def test_load_or_compute_records_no_heel_apis_as_scanned(tmp_path: Path):
    """
    Regression for the lag bug: an API queried but without a heel must be
    cached as 'scanned' so subsequent calls short-circuit. Pre-fix, the
    parquet only held found heels, so every rerun re-scanned the CSV.
    """
    cache = tmp_path / "heels.parquet"
    # Well A has a heel; Well B is in the CSV but never crosses 80°; Well C is absent entirely.
    csv = _surveys_csv([
        {"API_UWI": "A", "MeasuredDepth_FT": 8500, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 82.0, "Latitude": 31.5, "Longitude": -104.1},
        {"API_UWI": "B", "MeasuredDepth_FT": 8000, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 60.0, "Latitude": 31.4, "Longitude": -104.0},
    ])

    targets = {"A".zfill(14), "B".zfill(14), "C".zfill(14)}
    result = load_or_compute_heels(csv, targets, cache_path=cache)
    assert result == {"A".zfill(14): (31.5, -104.1)}

    # Parquet must now contain all 3 APIs (A as heel, B and C as scanned-no-heel).
    heels_cached, scanned_cached = _read_cache(cache)
    assert scanned_cached == targets
    assert set(heels_cached.keys()) == {"A".zfill(14)}


def test_subsequent_call_short_circuits_without_csv_when_all_scanned(tmp_path: Path):
    """After one scan, asking for the same APIs with csv_source=None still resolves."""
    cache = tmp_path / "heels.parquet"
    csv = _surveys_csv([
        {"API_UWI": "A", "MeasuredDepth_FT": 8500, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 82.0, "Latitude": 31.5, "Longitude": -104.1},
    ])
    targets = {"A".zfill(14), "B".zfill(14)}
    # Initial scan populates cache for both A (heel) and B (no heel).
    load_or_compute_heels(csv, targets, cache_path=cache)

    # Now pass csv_source=None; result must still be correct (no re-scan needed).
    result = load_or_compute_heels(None, targets, cache_path=cache)
    assert result == {"A".zfill(14): (31.5, -104.1)}


def test_new_api_triggers_scan_others_short_circuit(tmp_path: Path):
    """Only truly new APIs should ever trigger a CSV read."""
    cache = tmp_path / "heels.parquet"

    # First call: pre-populate cache with A (heel) and B (scanned, no heel).
    csv1 = _surveys_csv([
        {"API_UWI": "A", "MeasuredDepth_FT": 8500, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 82.0, "Latitude": 31.5, "Longitude": -104.1},
    ])
    load_or_compute_heels(csv1, {"A".zfill(14), "B".zfill(14)}, cache_path=cache)

    # Second call: ask for A, B, C with a brand-new CSV that only contains C.
    # The cache should have already-scanned A and B, so only C triggers a read.
    csv2 = _surveys_csv([
        {"API_UWI": "C", "MeasuredDepth_FT": 9000, "CoordinateSource": "ACTUAL",
         "Inclination_DEG": 85.0, "Latitude": 32.0, "Longitude": -103.5},
    ])
    result = load_or_compute_heels(
        csv2,
        {"A".zfill(14), "B".zfill(14), "C".zfill(14)},
        cache_path=cache,
    )
    assert result == {
        "A".zfill(14): (31.5, -104.1),   # from cache
        "C".zfill(14): (32.0, -103.5),   # newly scanned
    }
