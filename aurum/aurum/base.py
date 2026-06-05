"""Base conventions for AURUM organ stubs.

Every organ method that is not yet implemented raises `Unbuilt`, a subclass of
NotImplementedError carrying the organ + method name. Tests can assert an organ
is still a stub, and the assertion harness can tell "not built yet" apart from
"built and genuinely failed".
"""
from __future__ import annotations


class Unbuilt(NotImplementedError):
    """Raised by an interface method that has not been implemented yet."""

    def __init__(self, organ: str, method: str) -> None:
        super().__init__(f"{organ}.{method} is not implemented yet (stub)")
        self.organ = organ
        self.method = method


def unbuilt(organ: str, method: str) -> "Unbuilt":
    return Unbuilt(organ, method)
