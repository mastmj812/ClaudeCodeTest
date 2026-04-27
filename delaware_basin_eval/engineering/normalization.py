"""
Normalize well production to a 10,000 ft lateral equivalent.
"""

import numpy as np
import pandas as pd
from config import NORM_LATERAL_FT, MIN_LATERAL_FT


def normalize_production(
    monthly_rates: np.ndarray,
    lateral_length_ft: float,
) -> np.ndarray | None:
    """
    Scale daily oil rates to NORM_LATERAL_FT (10,000 ft) equivalent.
    Returns None if lateral_length is invalid (< MIN_LATERAL_FT).
    """
    if lateral_length_ft is None or np.isnan(lateral_length_ft):
        return None
    if lateral_length_ft < MIN_LATERAL_FT:
        return None
    factor = NORM_LATERAL_FT / lateral_length_ft
    return np.asarray(monthly_rates, dtype=float) * factor
