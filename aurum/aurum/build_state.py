"""Build-state registry.

The assertion harness must distinguish "organ not built yet" (skip the gate) from
"organ built and violating its invariant" (fail the gate). This registry is the
single switch. As Opus implements an organ, flip it to True here; its AURUM_ERR
assertion goes from skipped to live and MUST pass before commit.

An organ counts as built when its methods no longer raise base.Unbuilt.
"""
from __future__ import annotations

# Flip to True as each organ is genuinely implemented.
BUILT: dict[str, bool] = {
    # Tier 0 spine
    "PK": False, "BB": False, "TS": False,
    # Tier 0.5 durability / infrastructure
    "EL": True, "RR": True, "MGC": False, "KVE": False,
    "GR": True, "PM": False, "TCM": False,
    # Tier 1 novel
    "AA": False, "LS": False, "HVP": False, "EG": False,
    "CS": True, "AG": False, "OI": False,
    # Tier 2 extensions
    "SDG": False, "SM": False,
    # Tier 3 support
    "TL": False, "CB": True, "SH": False, "CG": True, "RS": False, "SEN": False,
    # Tier 3.5 observability — DERIVED VIEWS over EL, not first-class organs
    "IDM": True, "MPD": True, "CC": False,
    # Tier 4 deferred (stays False — documented boundary, not built in v1)
    "AO": False,
    # Deployment substrate (not an organ) — the cage's host-mount containment
    # boundary. True once the mount jail exists; arms AURUM_ERR_021.
    "CAGE": True,
}


def is_built(*organs: str) -> bool:
    return all(BUILT.get(o, False) for o in organs)
