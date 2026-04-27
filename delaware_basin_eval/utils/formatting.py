"""
Display formatting helpers.
"""


def fmt_mm(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"${value/1e6:,.{decimals}f}MM"


def fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value*100:.{decimals}f}%"


def fmt_months(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{value} mo"


def fmt_mboe(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value/1000:,.1f} MBOE"
