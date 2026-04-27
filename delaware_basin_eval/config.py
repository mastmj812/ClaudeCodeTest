"""
Central configuration: formation aliases, defaults, and constants.
All downstream modules import from here — never hardcode formation names elsewhere.
"""

# ── Formation canonical names ──────────────────────────────────────────────
FORMATIONS = [
    "Wolfcamp A",
    "Wolfcamp B",
    "Wolfcamp C",
    "Wolfcamp D",
    "3rd Bone Spring",
    "2nd Bone Spring",
    "1st Bone Spring",
    "Delaware Sand",
    "Cherry Canyon",
]

# Enverus/DI formation name variants → canonical name
FORMATION_ALIASES: dict[str, str] = {
    # Wolfcamp A
    "wolfcamp a": "Wolfcamp A",
    "wc-a": "Wolfcamp A",
    "wfmp a": "Wolfcamp A",
    "wolfcamp a1": "Wolfcamp A",
    "wolfcamp a2": "Wolfcamp A",
    "wolfcamp a upper": "Wolfcamp A",
    "wolfcamp a lower": "Wolfcamp A",
    # Wolfcamp B
    "wolfcamp b": "Wolfcamp B",
    "wc-b": "Wolfcamp B",
    "wfmp b": "Wolfcamp B",
    "wolfcamp b1": "Wolfcamp B",
    "wolfcamp b2": "Wolfcamp B",
    "wolfcamp b upper": "Wolfcamp B",
    "wolfcamp b lower": "Wolfcamp B",
    # Wolfcamp C
    "wolfcamp c": "Wolfcamp C",
    "wc-c": "Wolfcamp C",
    "wfmp c": "Wolfcamp C",
    # Wolfcamp D
    "wolfcamp d": "Wolfcamp D",
    "wc-d": "Wolfcamp D",
    "wfmp d": "Wolfcamp D",
    # Bone Spring
    "3rd bone spring": "3rd Bone Spring",
    "3rd bs": "3rd Bone Spring",
    "bone spring 3": "3rd Bone Spring",
    "bone spring 3rd": "3rd Bone Spring",
    "3 bone spring": "3rd Bone Spring",
    "2nd bone spring": "2nd Bone Spring",
    "2nd bs": "2nd Bone Spring",
    "bone spring 2": "2nd Bone Spring",
    "bone spring 2nd": "2nd Bone Spring",
    "2 bone spring": "2nd Bone Spring",
    "1st bone spring": "1st Bone Spring",
    "1st bs": "1st Bone Spring",
    "bone spring 1": "1st Bone Spring",
    "bone spring 1st": "1st Bone Spring",
    "1 bone spring": "1st Bone Spring",
    # Delaware Sand / Cherry Canyon
    "delaware sand": "Delaware Sand",
    "delaware": "Delaware Sand",
    "del sand": "Delaware Sand",
    "cherry canyon": "Cherry Canyon",
    "cherry cyn": "Cherry Canyon",
}

# ── Default price deck ─────────────────────────────────────────────────────
DEFAULT_PRICE_DECK = {
    "oil_price": 70.00,       # $/BBL
    "gas_price": 2.50,        # $/MMBTU
    "ngl_yield": 25.0,        # BBL/MMCF
    "ngl_price": 22.00,       # $/BBL
}

# ── Default revenue deductions ─────────────────────────────────────────────
DEFAULT_DEDUCTIONS = {
    "nri": 0.75,              # net revenue interest (working interest × NRI factor)
    "oil_severance": 0.046,   # TX statutory 4.6%
    "gas_severance": 0.075,   # TX statutory 7.5%
    "ad_valorem": 0.010,      # 1% ad valorem estimate
}

# ── Default well costs by formation (D&C, $MM) ────────────────────────────
DEFAULT_DC_COSTS: dict[str, float] = {
    "Wolfcamp A":     10.0,
    "Wolfcamp B":     11.0,
    "Wolfcamp C":     12.0,
    "Wolfcamp D":     14.0,
    "3rd Bone Spring": 9.0,
    "2nd Bone Spring": 8.5,
    "1st Bone Spring": 8.0,
    "Delaware Sand":  10.5,
    "Cherry Canyon":   9.5,
}

# ── Default LOE and discount ───────────────────────────────────────────────
DEFAULT_LOE_PER_BOE = 6.00     # $/BOE/month
DEFAULT_DISCOUNT_RATE = 0.10   # 10% annual

# ── Default well spacing by formation (acres/well) ────────────────────────
DEFAULT_SPACING: dict[str, float] = {
    "Wolfcamp A":      80.0,
    "Wolfcamp B":      80.0,
    "Wolfcamp C":     100.0,
    "Wolfcamp D":     100.0,
    "3rd Bone Spring": 80.0,
    "2nd Bone Spring": 80.0,
    "1st Bone Spring": 80.0,
    "Delaware Sand":  100.0,
    "Cherry Canyon":  100.0,
}

# ── Default offset filter ──────────────────────────────────────────────────
DEFAULT_OFFSET_RADIUS_MI = 10.0
DEFAULT_MAX_WELL_AGE_YR  = 10

# ── Decline curve constants ────────────────────────────────────────────────
MIN_MONTHS_FOR_FIT    = 6      # wells with fewer months are excluded from fitting
B_FACTOR_CAP          = 1.9    # hard clamp; warn above 1.5
TERMINAL_DI_ANNUAL    = 0.06   # switch to exponential below this annual decline
ECONOMIC_LIMIT_BOPD   = 1.0    # monthly rate < 30 BOE → economic limit
MAX_PROJECTION_MONTHS = 600

# ── Normalization ──────────────────────────────────────────────────────────
NORM_LATERAL_FT       = 10_000
MIN_LATERAL_FT        = 2_000  # exclude verticals / stubs

# ── Texas county FIPS (for spatial reference) ─────────────────────────────
TX_DELAWARE_COUNTIES = ["Reeves", "Loving", "Ward", "Culberson", "Winkler"]

# ── Standard PLSS section acreage ─────────────────────────────────────────
PLSS_SECTION_ACRES = 640.0
