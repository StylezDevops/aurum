"""MCP self-expansion substrate — register-not-install, mount-resident, remote-first.

The bridge that lets AA's synthesized tools reach the live MCP layer WITHOUT baking anything into
the ephemeral --rm container: tools are recorded in a durable, mount-resident registry and
re-registered each turn. See aurum.mcp.registry.
"""
from .registry import (
    DEPRECATED, ENABLED, MCP_REGISTRY_FILENAME, QUARANTINED, REGISTERED,
    McpRegistry, McpRegistryError, load_enabled_servers,
)

__all__ = [
    "McpRegistry", "McpRegistryError", "load_enabled_servers",
    "MCP_REGISTRY_FILENAME",
    "REGISTERED", "ENABLED", "QUARANTINED", "DEPRECATED",
]
