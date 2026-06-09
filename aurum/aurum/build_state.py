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
    "PK": True, "BB": True, "TS": True,
    # Tier 0.5 durability / infrastructure
    "EL": True, "RR": True, "MGC": True, "KVE": True,
    "GR": True, "PM": True, "TCM": True,
    # Tier 1 novel
    "AA": True, "LS": True, "HVP": True, "EG": True,
    "CS": True, "AG": True, "OI": True,
    # Tier 2 extensions
    "SDG": False, "SM": False,
    # Tier 3 support
    "TL": True, "CB": True, "SH": True, "CG": True, "RS": False, "SEN": True,
    # Tier 3.5 observability — DERIVED VIEWS over EL, not first-class organs
    "IDM": True, "MPD": True, "CC": True,
    # Tier 4 deferred (stays False — documented boundary, not built in v1)
    "AO": False,
    # Arbitration layer (MECHANISMS, not organs) — CA synchronous, DD asynchronous.
    # Arms AURUM_ERR_013–020.
    "CA": True, "DD": True,
    # CS-EQ constitutional-stability mechanism (legs 1 & 3: economic differential +
    # equilibrium taxonomy + integrity probes + signed external manifest). Arms 022–026.
    # Leg 2 (the sustained equilibrium PROPERTY) is INSTRUMENTED but deferred to evidence.
    "CSEQ": True,
    # FC forced-contestability mechanism: inverted scrutiny for the irreversible class +
    # periodic re-justification of elite pathways (observe on; evaluate evidence-gated;
    # demote-only, never widens authority). Arms 070–072.
    "FC": True,
    # Deployment substrate (not an organ) — the cage's host-mount containment
    # boundary. True once the mount jail exists; arms AURUM_ERR_021.
    "CAGE": True,
}


def is_built(*organs: str) -> bool:
    return all(BUILT.get(o, False) for o in organs)
