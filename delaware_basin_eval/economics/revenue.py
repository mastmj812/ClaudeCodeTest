"""
Price deck application and revenue calculations.
"""

import numpy as np


def calc_monthly_revenue(
    oil_bbl: np.ndarray,
    gas_mcf: np.ndarray,
    cfg: dict,
) -> dict[str, np.ndarray]:
    """
    Convert monthly production volumes to net revenue arrays.

    Parameters
    ----------
    oil_bbl  : monthly oil volumes (BBL)
    gas_mcf  : monthly gas volumes (MCF)
    cfg      : config dict with price deck and deduction keys

    Returns dict with: gross_oil_rev, gross_gas_rev, gross_ngl_rev,
                       severance, ad_valorem, net_revenue
    """
    oil_bbl = np.asarray(oil_bbl, dtype=float)
    gas_mcf = np.asarray(gas_mcf, dtype=float)

    ngl_bbl = gas_mcf * cfg["ngl_yield"] / 1000.0  # MCF → MMCF × yield

    gross_oil = oil_bbl * cfg["oil_price"]
    gross_gas = gas_mcf * cfg["gas_price"] / 1000.0 * 1e3  # MCF × $/MMBTU × (1 MMBTU/MCF approx)
    # More precisely: gas_mcf × (1 MMBTU/MCF) × gas_price; 1 MCF ≈ 1 MMBTU for dry gas
    gross_gas = gas_mcf * cfg["gas_price"]           # MCF × $/MCF (treating $/MMBTU ≈ $/MCF)
    gross_ngl = ngl_bbl * cfg["ngl_price"]

    gross_total = (gross_oil + gross_gas + gross_ngl) * cfg["nri"]

    oil_sev = gross_oil * cfg["oil_severance"]
    gas_sev = gross_gas * cfg["gas_severance"]
    severance = (oil_sev + gas_sev) * cfg["nri"]

    ad_valorem = gross_total * cfg["ad_valorem"]

    net_revenue = gross_total - severance - ad_valorem

    return {
        "gross_oil_rev": gross_oil,
        "gross_gas_rev": gross_gas,
        "gross_ngl_rev": gross_ngl,
        "severance":     severance,
        "ad_valorem":    ad_valorem,
        "net_revenue":   net_revenue,
        "ngl_bbl":       ngl_bbl,
    }
