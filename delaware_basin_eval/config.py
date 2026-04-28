"""
Central configuration: formation aliases, defaults, and constants.
All downstream modules import from here — never hardcode formation names elsewhere.
"""

# ── Formation canonical names (user-defined order) ────────────────────────
FORMATIONS = [
    "Upper Avalon",
    "Middle Avalon",
    "Lower Avalon",
    "First Bone Spring",
    "Second Bone Spring",
    "Third Bone Spring",
    "Third Bone Spring Sand",
    "Wolfcamp XY",
    "Wolfcamp A",
    "Wolfcamp B",
    "Wolfcamp C",
    "Wolfcamp D",
    "Woodford",
]

# Enverus/DI formation name variants → canonical name (used for auto-match in mapping UI)
FORMATION_ALIASES: dict[str, str] = {
    # Avalon
    "upper avalon":            "Upper Avalon",
    "avalon upper":            "Upper Avalon",
    "avalon u":                "Upper Avalon",
    "middle avalon":           "Middle Avalon",
    "avalon middle":           "Middle Avalon",
    "avalon m":                "Middle Avalon",
    "lower avalon":            "Lower Avalon",
    "avalon lower":            "Lower Avalon",
    "avalon l":                "Lower Avalon",
    "avalon":                  "Upper Avalon",
    # Bone Spring
    "1st bone spring":         "First Bone Spring",
    "first bone spring":       "First Bone Spring",
    "bone spring 1":           "First Bone Spring",
    "bone spring 1st":         "First Bone Spring",
    "1 bone spring":           "First Bone Spring",
    "1st bs":                  "First Bone Spring",
    "bs1":                     "First Bone Spring",
    "2nd bone spring":         "Second Bone Spring",
    "second bone spring":      "Second Bone Spring",
    "bone spring 2":           "Second Bone Spring",
    "bone spring 2nd":         "Second Bone Spring",
    "2 bone spring":           "Second Bone Spring",
    "2nd bs":                  "Second Bone Spring",
    "bs2":                     "Second Bone Spring",
    "3rd bone spring":         "Third Bone Spring",
    "third bone spring":       "Third Bone Spring",
    "bone spring 3":           "Third Bone Spring",
    "bone spring 3rd":         "Third Bone Spring",
    "3 bone spring":           "Third Bone Spring",
    "3rd bs":                  "Third Bone Spring",
    "bs3":                     "Third Bone Spring",
    "3rd bone spring sand":    "Third Bone Spring Sand",
    "third bone spring sand":  "Third Bone Spring Sand",
    "bs sand":                 "Third Bone Spring Sand",
    "bone spring sand":        "Third Bone Spring Sand",
    # Wolfcamp
    "wolfcamp xy":             "Wolfcamp XY",
    "wc-xy":                   "Wolfcamp XY",
    "wfmp xy":                 "Wolfcamp XY",
    "wolfcamp a":              "Wolfcamp A",
    "wc-a":                    "Wolfcamp A",
    "wfmp a":                  "Wolfcamp A",
    "wolfcamp a1":             "Wolfcamp A",
    "wolfcamp a2":             "Wolfcamp A",
    "wolfcamp a upper":        "Wolfcamp A",
    "wolfcamp a lower":        "Wolfcamp A",
    "wolfcamp b":              "Wolfcamp B",
    "wc-b":                    "Wolfcamp B",
    "wfmp b":                  "Wolfcamp B",
    "wolfcamp b1":             "Wolfcamp B",
    "wolfcamp b2":             "Wolfcamp B",
    "wolfcamp b upper":        "Wolfcamp B",
    "wolfcamp b lower":        "Wolfcamp B",
    "wolfcamp c":              "Wolfcamp C",
    "wc-c":                    "Wolfcamp C",
    "wfmp c":                  "Wolfcamp C",
    "wolfcamp d":              "Wolfcamp D",
    "wc-d":                    "Wolfcamp D",
    "wfmp d":                  "Wolfcamp D",
    # Woodford
    "woodford":                "Woodford",
    "woodford shale":          "Woodford",
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
    "nri": 0.75,              # net revenue interest
    "oil_severance": 0.046,   # TX statutory 4.6%
    "gas_severance": 0.075,   # TX statutory 7.5%
    "ad_valorem": 0.010,      # 1% ad valorem estimate
}

# ── Default well costs by formation (D&C, $MM) ────────────────────────────
DEFAULT_DC_COSTS: dict[str, float] = {
    "Upper Avalon":           9.0,
    "Middle Avalon":          9.0,
    "Lower Avalon":           9.5,
    "First Bone Spring":      8.0,
    "Second Bone Spring":     8.5,
    "Third Bone Spring":      9.0,
    "Third Bone Spring Sand": 9.0,
    "Wolfcamp XY":           10.0,
    "Wolfcamp A":            10.0,
    "Wolfcamp B":            11.0,
    "Wolfcamp C":            12.0,
    "Wolfcamp D":            14.0,
    "Woodford":              12.0,
}

# ── Default LOE by cost component ─────────────────────────────────────────
DEFAULT_LOE_OIL_PER_BBL   = 2.50    # $/BBL oil produced (fluid handling, treating)
DEFAULT_LOE_GAS_PER_MCF   = 0.30    # $/MCF gas produced (gathering, compression)
DEFAULT_LOE_WATER_PER_BBL = 1.25    # $/BBL water produced (disposal)
DEFAULT_LOE_FIXED_PER_MO  = 2000.0  # $/month fixed (pump lease, lift, labor allocation)

# ── Default water-oil ratio for undrilled well LOE estimation ─────────────
DEFAULT_WOR = 1.5  # BBL water / BBL oil

# ── Default discount rate ──────────────────────────────────────────────────
DEFAULT_DISCOUNT_RATE = 0.10   # 10% annual

# ── Default wells per 640-acre section by formation ───────────────────────
DEFAULT_WELLS_PER_SECTION: dict[str, int] = {
    "Upper Avalon":            4,
    "Middle Avalon":           4,
    "Lower Avalon":            4,
    "First Bone Spring":       4,
    "Second Bone Spring":      4,
    "Third Bone Spring":       4,
    "Third Bone Spring Sand":  4,
    "Wolfcamp XY":             4,
    "Wolfcamp A":              4,
    "Wolfcamp B":              4,
    "Wolfcamp C":              4,
    "Wolfcamp D":              4,
    "Woodford":                4,
}

# ── Default offset filter ──────────────────────────────────────────────────
DEFAULT_OFFSET_RADIUS_MI = 10.0
DEFAULT_MAX_WELL_AGE_YR  = 10

# ── Decline curve constants ────────────────────────────────────────────────
MIN_MONTHS_FOR_FIT    = 6
B_FACTOR_CAP          = 1.9
TERMINAL_DI_ANNUAL    = 0.06
ECONOMIC_LIMIT_BOPD   = 1.0
MAX_PROJECTION_MONTHS = 600

# ── Normalization ──────────────────────────────────────────────────────────
NORM_LATERAL_FT       = 10_000
MIN_LATERAL_FT        = 2_000

# ── Texas county FIPS (for spatial reference) ─────────────────────────────
TX_DELAWARE_COUNTIES = ["Reeves", "Loving", "Ward", "Culberson", "Winkler"]

# ── Standard PLSS section acreage ─────────────────────────────────────────
PLSS_SECTION_ACRES = 640.0
