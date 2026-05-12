"""
Lateral-length normalization to a 10,000 ft equivalent.
"""

import numpy as np
import pytest

from engineering.normalization import normalize_production
from config import NORM_LATERAL_FT, MIN_LATERAL_FT


def test_at_norm_lateral_no_scaling():
    rates = np.array([100.0, 90.0, 80.0])
    out = normalize_production(rates, NORM_LATERAL_FT)
    assert np.allclose(out, rates)


def test_half_lateral_doubles_rates():
    rates = np.array([100.0, 90.0, 80.0])
    out = normalize_production(rates, NORM_LATERAL_FT / 2.0)
    assert np.allclose(out, rates * 2.0)


def test_below_min_lateral_returns_none():
    rates = np.array([100.0, 90.0])
    assert normalize_production(rates, MIN_LATERAL_FT - 1) is None


def test_at_min_lateral_returns_scaled():
    rates = np.array([100.0])
    out = normalize_production(rates, MIN_LATERAL_FT)
    expected = rates * (NORM_LATERAL_FT / MIN_LATERAL_FT)
    assert np.allclose(out, expected)


def test_nan_lateral_returns_none():
    assert normalize_production(np.array([100.0]), float("nan")) is None


def test_none_lateral_returns_none():
    assert normalize_production(np.array([100.0]), None) is None
