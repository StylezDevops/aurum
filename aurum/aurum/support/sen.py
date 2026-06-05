"""SEN — Sensorium. Pluggable watchers (folder/inbox/repo/webhook/RSS) wake the agent.

Content arriving here is tagged untrusted by PK (injection guard).
"""
from __future__ import annotations

from typing import Any, Callable

from ..base import unbuilt


class Sensorium:
    ORGAN = "SEN"

    def watch(self, source: Any, handler: Callable[[Any], None]) -> None:
        raise unbuilt(self.ORGAN, "watch")

    def on_event(self, event: Any) -> None:
        raise unbuilt(self.ORGAN, "on_event")
