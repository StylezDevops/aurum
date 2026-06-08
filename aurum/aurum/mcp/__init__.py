"""MCP self-expansion substrate — register-not-install, mount-resident, remote-first.

The bridge that lets AA's synthesized tools reach the live MCP layer WITHOUT baking anything into
the ephemeral --rm container: tools are recorded in a durable, mount-resident registry and
re-registered each turn. See aurum.mcp.registry.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from .registry import (
    DEPRECATED, ENABLED, QUARANTINED, REGISTERED, McpRegistry, McpRegistryError,
)

__all__ = [
    "McpRegistry", "McpRegistryError",
    "REGISTERED", "ENABLED", "QUARANTINED", "DEPRECATED",
]
