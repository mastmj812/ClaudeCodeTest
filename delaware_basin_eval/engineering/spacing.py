"""
Calculate remaining drillable locations per formation in a section.
"""

import pandas as pd
import numpy as np
from config import FORMATIONS, DEFAULT_WELLS_PER_SECTION, PLSS_SECTION_ACRES


def remaining_locations(
    section_wells: pd.DataFrame,
    section_acreage: float,
    wells_per_section: dict[str, int],
) -> pd.DataFrame:
    """
    For each formation, compute total drillable slots scaled to actual acreage,
    subtract existing wells, and return remaining undrilled locations.

    wells_per_section is keyed by formation and represents wells per 640-acre section.
    For partial sections, slots are scaled proportionally.
    """
    rows = []
    formation_counts = section_wells["formation"].value_counts().to_dict()

    for formation in FORMATIONS:
        n_per_section = wells_per_section.get(formation, DEFAULT_WELLS_PER_SECTION.get(formation, 4))
        n_per_section = max(0, int(n_per_section))
        total_slots   = max(0, int(section_acreage / PLSS_SECTION_ACRES * n_per_section))
        existing      = formation_counts.get(formation, 0)
        remaining     = max(0, total_slots - existing)

        rows.append({
            "Formation":        formation,
            "Wells/Section":    n_per_section,
            "Total Slots":      total_slots,
            "Existing Wells":   existing,
            "Remaining":        remaining,
        })

    return pd.DataFrame(rows)
