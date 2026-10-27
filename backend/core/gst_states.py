"""GST state / union-territory codes (the list is a constant, mirrored in ``core.State``).

``core.State`` is seeded from this tuple by ``core.migrations.0008_seed_states`` and
re-ensured by ``seed_defaults``. GSTIN validation checks the two-digit prefix against
:data:`STATE_CODES` without touching the database.

Each row: ``(code, name, abbr, is_union_territory, is_active)``. Codes 25 (old Daman
& Diu) and 28 (old Andhra Pradesh) are retired but still appear inside GSTINs issued
before the reorganisation, so they stay valid for GSTIN purposes.
"""

STATES = (
    ("01", "Jammu & Kashmir", "JK", True, True),
    ("02", "Himachal Pradesh", "HP", False, True),
    ("03", "Punjab", "PB", False, True),
    ("04", "Chandigarh", "CH", True, True),
    ("05", "Uttarakhand", "UK", False, True),
    ("06", "Haryana", "HR", False, True),
    ("07", "Delhi", "DL", True, True),
    ("08", "Rajasthan", "RJ", False, True),
    ("09", "Uttar Pradesh", "UP", False, True),
    ("10", "Bihar", "BR", False, True),
    ("11", "Sikkim", "SK", False, True),
    ("12", "Arunachal Pradesh", "AR", False, True),
    ("13", "Nagaland", "NL", False, True),
    ("14", "Manipur", "MN", False, True),
    ("15", "Mizoram", "MZ", False, True),
    ("16", "Tripura", "TR", False, True),
    ("17", "Meghalaya", "ML", False, True),
    ("18", "Assam", "AS", False, True),
    ("19", "West Bengal", "WB", False, True),
    ("20", "Jharkhand", "JH", False, True),
    ("21", "Odisha", "OD", False, True),
    ("22", "Chhattisgarh", "CG", False, True),
    ("23", "Madhya Pradesh", "MP", False, True),
    ("24", "Gujarat", "GJ", False, True),
    ("25", "Daman & Diu", "DD", True, False),
    ("26", "Dadra & Nagar Haveli and Daman & Diu", "DN", True, True),
    ("27", "Maharashtra", "MH", False, True),
    ("28", "Andhra Pradesh (old)", "AP", False, False),
    ("29", "Karnataka", "KA", False, True),
    ("30", "Goa", "GA", False, True),
    ("31", "Lakshadweep", "LD", True, True),
    ("32", "Kerala", "KL", False, True),
    ("33", "Tamil Nadu", "TN", False, True),
    ("34", "Puducherry", "PY", True, True),
    ("35", "Andaman & Nicobar Islands", "AN", True, True),
    ("36", "Telangana", "TS", False, True),
    ("37", "Andhra Pradesh", "AP", False, True),
    ("38", "Ladakh", "LA", True, True),
    ("96", "Other Countries", "", False, True),
    ("97", "Other Territory", "", False, True),
)

STATE_CODES = frozenset(row[0] for row in STATES)
STATE_NAMES = {row[0]: row[1] for row in STATES}
