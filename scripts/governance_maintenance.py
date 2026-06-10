#!/usr/bin/env python3
"""Governance maintenance runner — the LONG-LIVED HOST driver for the trigger layer.

The ephemeral `--rm` cage has no natural between-turn moment in-process, so scheduled governance
maintenance (MPD owner-review scan, CC concentration, OI calibration, + any deployer-registered
organ tasks) runs in one of two places:

  • HOST / long-lived deployment  → THIS runner: a loop that calls kernel.run_maintenance(now) every
    interval (tick → feed RS → drain), with the GOOD defaults from default_governance_scheduler.
  • Cage / per-message            → set AURUM_MAINTENANCE_ON_TURN=1 so the governance plugin runs
    due maintenance opportunistically once per message (see plugins/aurum-governance).

Run:  python scripts/governance_maintenance.py [--interval SECONDS] [--once]
State root: AURUM_STATE_ROOT → HERMES_HOME → ~/.hermes (same resolution as the plugin).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import argparse
import os
import sys

# Resolve the organs package whether run from the repo root or elsewhere.
_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "aurum"))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from aurum.kernel import GovernanceKernel  # noqa: E402
from aurum.observability.triggers import run_maintenance_loop  # noqa: E402


def _state_root() -> str:
    return (os.environ.get("AURUM_STATE_ROOT")
            or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aurum governance maintenance runner (host loop).")
    ap.add_argument("--interval", type=float, default=float(os.environ.get("AURUM_MAINTENANCE_INTERVAL", 3600)),
                    help="seconds between maintenance passes (default 3600 / env AURUM_MAINTENANCE_INTERVAL)")
    ap.add_argument("--once", action="store_true", help="run a single pass and exit")
    ap.add_argument("--home", default=_state_root(), help="governance state root (durable mount)")
    args = ap.parse_args(argv)

    kernel = GovernanceKernel(home=args.home)
    if args.once:
        results = kernel.run_maintenance()
        print(f"maintenance pass: {len(results)} task(s) run")
        return 0

    print(f"governance maintenance runner: home={args.home} interval={args.interval}s (Ctrl-C to stop)")
    try:
        run_maintenance_loop(kernel.run_maintenance, interval_seconds=args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
