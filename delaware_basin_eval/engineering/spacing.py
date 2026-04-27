"""
Calculate remaining drillable locations per formation in a section.
"""

import pandas as pd
import numpy as np
from config import FORMATIONS, DEFAULT_SPACING, PLSS_SECTION_ACRES


def remaining_locations(
    section_wells: pd.DataFrame,
    section_acreage: float,
    spacing: dict[str, float],
) -> pd.DataFrame:
    """
    For each formation, compute:
      - existing_wells: count of wells already drilled
      - total_slots: floor(section_acreage / acres_per_well)
      - remaining: max(0, total_slots - existing_wells)

    Returns a DataFrame with one row per formation.
    """
    rows = []
    formation_counts = section_wells["formation"].value_counts().to_dict()

    for formation in FORMATIONS:
        acres_per_well = spacing.get(formation, DEFAULT_SPACING.get(formation, 80.0))
        if acres_per_well <= 0:
            acres_per_well = 80.0
        total_slots  = max(0, int(section_acreage / acres_per_well))
        existing     = formation_counts.get(formation, 0)
        remaining    = max(0, total_slots - existing)

        rows.append({
            "Formation":        formation,
            "Acres/Well":       acres_per_well,
            "Total Slots":      total_slots,
            "Existing Wells":   existing,
            "Remaining":        remaining,
        })

    return pd.DataFrame(rows)
