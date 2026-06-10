"""Shared path resolution for Aurum's durable governance state.

Single source of truth for the state root — the durable mount that outlives the `--rm` cage and
holds the EL ledger, authority projection, BB, OI, the MCP registry, etc. Both the in-cage plugin
and the host maintenance runner resolve it HERE so they can never diverge (a split would write/read
governance state to two different roots).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import os


def state_root() -> str:
    """The durable governance state root: AURUM_STATE_ROOT → HERMES_HOME → ~/.hermes. One knob;
    deployment picks the backing store (a host bind dir, an Azure Files / EFS share, a k8s PVC)."""
    return (os.environ.get("AURUM_STATE_ROOT")
            or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
