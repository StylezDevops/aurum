"""BB — Black Box. Tier 0 spine (stubbed mock).

Failed task auto-writes a structured postmortem to a searchable on-disk corpus
the loop mines. Redacted via PK.redact (single policy); on-disk; never hot-path.

SIGNED STATE: the corpus is host-mounted and re-ingested across restarts, so it is
a self-poisoning vector if writable out-of-band. Per the deployment-hardening
invariant, entries are signed on write and verified on load; entries that fail
verification are quarantined and treated as untrusted external content, never
ingested as trusted history.

Reconciliation (scaffold canonical): `agent/black_box.py` in the Hermes tree is working
prior art (redacted postmortems + skill-review addendum) and the live implementation
today. Migrate its logic INTO this organ as BB is built to spec, and keep it running
until then. `build_state.BUILT['BB']` stays False until this stub is spec-complete —
don't flip-to-True-and-wire the existing file.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt


class BlackBox:
    ORGAN = "BB"

    def write(self, postmortem: Dict[str, Any]) -> None:  # signs on write
        raise unbuilt(self.ORGAN, "write")

    def search(self, query: str) -> List[Dict[str, Any]]:
        raise unbuilt(self.ORGAN, "search")

    def verify_on_load(self) -> Dict[str, List[str]]:
        """-> {trusted:[id], quarantined:[id]}. Signature check on reloaded state."""
        raise unbuilt(self.ORGAN, "verify_on_load")
