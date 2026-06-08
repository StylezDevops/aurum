"""Mount-resident MCP server registry — register-not-install, remote-first, secret-by-reference.

The substrate for AA self-expansion (Phase F). A synthesized (or operator-added) MCP server is
RECORDED here, in `mcp-servers.json` on the DURABLE MOUNT (AURUM_STATE_ROOT) — NEVER baked into
the `--rm` container image. The container reads this registry at startup and registers the
ENABLED servers, so a tool synthesized one turn is available the next turn and survives `--rm`
by construction.

Enforced properties (the a/b/c guarantees, registry side):
  • MOUNT-RESIDENT + ATOMIC + durable: one JSON file on the state mount; writes are atomic
    (temp + os.replace) and lock-guarded; the file IS the source of truth — a fresh registry
    re-reads it (persistence-as-projection, like the EL/authority pattern).
  • REGISTER ≠ ENABLE (least privilege + the idle-token-tax guard): a newly registered server is
    INERT (state=registered) and its tools are NOT injected; ENABLING it is HUMAN_GATE, and even
    then its tools are injected only for the groups it was enabled for (conditional injection).
  • NO RAW SECRETS: an entry may carry a `secret_ref` (a NAME) only — never a secret value. A
    spec carrying a raw-secret-shaped field is REJECTED. OneCLI resolves ref→value at request
    time, so the mount file (and `docker inspect`) stay clean.

REMOTE-FIRST: an HTTP MCP (url + secret_ref) has nothing to install, so it sidesteps `--rm`
entirely — the pipeline API is exactly this case. Local stdio servers must be baked into the
image, then merely registered here.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lifecycle states (TS-aligned). A registered server is inert until enabled.
REGISTERED = "registered"
ENABLED = "enabled"
QUARANTINED = "quarantined"
DEPRECATED = "deprecated"
_STATES = frozenset({REGISTERED, ENABLED, QUARANTINED, DEPRECATED})

_TRANSPORTS = frozenset({"http", "stdio"})

# Field names that would mean a RAW SECRET VALUE is being smuggled into the registry. Rejected
# on register — secrets live only as a `secret_ref` (a name), resolved by OneCLI at request time.
_FORBIDDEN_SECRET_FIELDS = frozenset({
    "api_key", "apikey", "token", "password", "passwd", "secret", "authorization",
    "bearer", "credential", "client_secret", "access_key", "private_key", "secret_value",
})

# The only fields an entry may carry (allowlist — anything else is rejected, which also catches
# a raw secret under an unexpected key name).
_ALLOWED_FIELDS = frozenset({
    "id", "transport", "url", "secret_ref", "auth_header", "enabled_groups", "origin",
    "state", "created_at", "updated_at", "description", "capability", "tool_schema",
    # `tools`: per-tool governance classification {tool: {capability_class, action_class,
    # risk_tier, irreversible}} — a registered/AA-synthesized tool's gating lives HERE on the
    # mount, never baked into action_map.py.
    "tools",
})


class McpRegistryError(ValueError):
    """A registry spec is invalid (missing required field, bad transport, or — most importantly —
    a raw secret where only a secret_ref is allowed)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class McpRegistry:
    """Durable, mount-resident registry of MCP servers. Thread-safe; the JSON file is the source
    of truth (every op reads it fresh, so a fresh registry — or a fresh container turn — sees all
    prior writes). Pure persistence + validation; governance LOGGING (to EL) is the caller's job
    (AA.register_mcp / the kernel), keeping this module a clean projection."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- persistence (atomic, mount-resident) ------------------------------
    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            # A corrupt registry must FAIL CLOSED to "no servers" (nothing injected), never
            # crash a turn or silently trust garbage.
            return {}

    def _atomic_write(self, data: Dict[str, Dict[str, Any]]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + f".tmp.{uuid.uuid4().hex}")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._path)   # atomic on the same filesystem (incl. Windows)

    # -- validation (no raw secrets) ---------------------------------------
    @staticmethod
    def _validate(spec: Dict[str, Any]) -> None:
        if not isinstance(spec, dict):
            raise McpRegistryError("MCP spec must be a dict")
        for key in spec:
            lk = str(key).lower()
            if lk in _FORBIDDEN_SECRET_FIELDS:
                raise McpRegistryError(
                    f"raw secret field {key!r} is forbidden in the registry — use 'secret_ref' "
                    "(a name); OneCLI resolves the value at request time")
            if lk not in _ALLOWED_FIELDS:
                raise McpRegistryError(f"unknown registry field {key!r} (allowlist-validated)")
        if not spec.get("id"):
            raise McpRegistryError("MCP spec requires a non-empty 'id'")
        transport = spec.get("transport")
        if transport not in _TRANSPORTS:
            raise McpRegistryError(f"transport must be one of {sorted(_TRANSPORTS)}, got {transport!r}")
        if transport == "http" and not spec.get("url"):
            raise McpRegistryError("an http (remote) MCP requires a 'url'")
        sr = spec.get("secret_ref")
        if sr is not None and (not isinstance(sr, str) or not sr):
            raise McpRegistryError("secret_ref must be a non-empty string NAME (never a value)")

    # -- lifecycle ---------------------------------------------------------
    def register(self, spec: Dict[str, Any], *, origin: str = "aa_synth") -> Dict[str, Any]:
        """Record a server. Validates (rejects raw secrets). A NEW id starts INERT
        (state=registered → not injected); re-registering an existing id updates its fields but
        preserves its lifecycle state. Returns the stored entry."""
        self._validate(spec)
        with self._lock:
            data = self._load()
            sid = spec["id"]
            now = _now_iso()
            existing = data.get(sid)
            entry: Dict[str, Any] = dict(existing) if existing else {}
            entry.update({k: v for k, v in spec.items() if k != "state"})
            entry["id"] = sid
            entry.setdefault("enabled_groups", [])
            entry["origin"] = entry.get("origin", origin)
            entry["state"] = existing["state"] if existing else REGISTERED
            entry["created_at"] = existing["created_at"] if existing else now
            entry["updated_at"] = now
            data[sid] = entry
            self._atomic_write(data)
            return dict(entry)

    def enable(self, server_id: str, *, groups: List[str],
               approved_by: Optional[str] = None) -> Dict[str, Any]:
        """Activate a server for specific groups. HUMAN_GATE — enabling is a capability-adding
        path (it makes the tool live), so `approved_by` is required (TS.promote convention).
        Only enabled servers are injected, and only for `groups` (conditional injection)."""
        if approved_by is None:
            raise PermissionError("McpRegistry.enable is HUMAN_GATE (approved_by required)")
        return self._set_state(server_id, ENABLED, enabled_groups=list(groups),
                               approved_by=approved_by)

    def quarantine(self, server_id: str, reason: str) -> Dict[str, Any]:
        """Suspend a server (e.g. reliability drop) — no longer injected, not deleted."""
        return self._set_state(server_id, QUARANTINED, quarantine_reason=reason)

    def deprecate(self, server_id: str) -> Dict[str, Any]:
        return self._set_state(server_id, DEPRECATED)

    def _set_state(self, server_id: str, state: str, **extra: Any) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            entry = data.get(server_id)
            if entry is None:
                raise KeyError(f"no MCP server {server_id!r}")
            entry["state"] = state
            entry["updated_at"] = _now_iso()
            if "enabled_groups" in extra:
                entry["enabled_groups"] = extra["enabled_groups"]
            # approved_by / quarantine_reason are recorded by the caller in EL, not stored as
            # registry fields (allowlist-validated); state + enabled_groups are the registry's.
            data[server_id] = entry
            self._atomic_write(data)
            return dict(entry)

    # -- reads -------------------------------------------------------------
    def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        return self._load().get(server_id)

    def list(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        entries = list(self._load().values())
        if state is not None:
            entries = [e for e in entries if e.get("state") == state]
        return sorted(entries, key=lambda e: e.get("id", ""))

    def classify(self, server_id: str, tool: str) -> Optional[Dict[str, Any]]:
        """The governance classification a registered server declared for one of its tools, or
        None. This is what feeds `to_action(classification=...)` so a dynamic tool is governed
        from the mount registry, not a baked table."""
        entry = self.get(server_id)
        if entry is None:
            return None
        return (entry.get("tools") or {}).get(tool)

    def tools_for_group(self, group: str) -> List[Dict[str, Any]]:
        """CONDITIONAL INJECTION: the ENABLED servers whose tools should be injected for `group`
        this turn. A registered-but-not-enabled server returns nothing — so its schema is never
        sent on an LLM call (the idle-token-tax guard). This is what the container MCP layer
        consults at startup/turn to decide which servers to register + inject."""
        return [e for e in self.list(state=ENABLED) if group in e.get("enabled_groups", [])]
