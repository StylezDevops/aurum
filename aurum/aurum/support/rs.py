"""RS — Resource Scheduler. Coordinates background-organ competition (priority).

Sits above CG (cost) deciding what runs now vs later. Foreground preempts
background; starvation guard escalates perpetually-deferred jobs. Background
organs must register here rather than spinning raw unmanaged threads.
"""
from __future__ import annotations

from typing import Any, List

from ..base import unbuilt


class ResourceScheduler:
    ORGAN = "RS"

    def submit(self, job: Any, weight: Any) -> None:
        raise unbuilt(self.ORGAN, "submit")

    def next(self) -> Any:
        raise unbuilt(self.ORGAN, "next")

    def preempt(self, reason: str) -> None:
        raise unbuilt(self.ORGAN, "preempt")

    def backlog(self) -> List[Any]:
        raise unbuilt(self.ORGAN, "backlog")
