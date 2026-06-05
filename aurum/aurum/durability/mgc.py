"""MGC — Memory Garbage Collector. Without it the system self-poisons.

Lease-aware (skips CS-leased artifacts). ARCHIVE != DELETE (all recoverable).
EL compression is lossless structural only.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..base import unbuilt


class MemoryGarbageCollector:
    ORGAN = "MGC"

    def scan(self) -> Dict[str, Any]:
        """-> {archivable:[id], mergeable:[[id]], retirable:[id]}. Skips leased."""
        raise unbuilt(self.ORGAN, "scan")

    def archive(self, id: str) -> None:
        raise unbuilt(self.ORGAN, "archive")

    def merge(self, ids: List[str]) -> None:
        raise unbuilt(self.ORGAN, "merge")

    def restore(self, id: str) -> Any:
        raise unbuilt(self.ORGAN, "restore")

    def compress(self, el_region: Any) -> None:
        """Lossless structural only (snapshot + raw deltas), never semantic summary."""
        raise unbuilt(self.ORGAN, "compress")
