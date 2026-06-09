"""GovernedHttpClient — drive ANY external HTTP API *through* the governance kernel.

A public framework primitive (no domain knowledge): every call is `kernel.govern()`-cleared
before it leaves, so safe reads pass while irreversible/outward calls are gated to the full
authority band (shadow-contained until that authority is earned). The server is registered in the
mount MCP registry (remote-first), and each tool DECLARES its governance classification — so a
dynamically-registered / AA-synthesized API is governed from durable mount data, never from a
baked table.

Secret-by-reference: the API key is resolved from a `secret_ref` (env → gitignored `secrets/*.env`
→ later OneCLI vault) and sent only in the configured auth header; never logged, never embedded.
HTTP is stdlib urllib; `transport` is injectable so the governed flow is fully testable offline.

A concrete instance (e.g. a label's release pipeline) subclasses this with its `server_id`,
`base_url`, and tool classification + convenience methods — that subclass is *instance* code and
lives in the private layer, not here.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..action_map import to_action


def resolve_secret(ref: str, *, secrets_file: str = "secrets/secrets.env") -> Optional[str]:
    """Resolve a secret by REFERENCE name: env var first, then a gitignored `secrets/*.env`
    (KEY=VALUE) file, else None. (OneCLI vault slots in here later.) The VALUE is never logged."""
    val = os.environ.get(ref)
    if val:
        return val
    p = Path(secrets_file)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{ref}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return None


class GovernedHttpClient:
    """Governs HTTP calls to one external API. `transport(method, url, body, headers) -> dict` is
    injectable for tests (default: urllib). A GATED call is NEVER sent — it returns the gate
    decision (shadow containment). `tool_classes` maps each tool to its governance classification
    `{capability_class, action_class, risk_tier, irreversible}`; that is what gets registered on
    the mount and passed to `to_action(classification=...)`."""

    def __init__(self, kernel: Any, *, server_id: str, base_url: str,
                 tool_classes: Optional[Dict[str, Dict[str, Any]]] = None,
                 secret_ref: Optional[str] = None, api_key: Optional[str] = None,
                 auth_header: str = "X-API-Key", capability: Optional[str] = None,
                 registry: Any = None, transport: Optional[Callable[..., Dict[str, Any]]] = None,
                 secrets_file: str = "secrets/secrets.env", timeout: float = 60.0) -> None:
        self.kernel = kernel
        self.server_id = server_id
        self.base_url = base_url.rstrip("/")
        self._tool_classes = dict(tool_classes or {})
        self._secret_ref = secret_ref
        self._auth_header = auth_header
        self._api_key = api_key if api_key is not None else (
            resolve_secret(secret_ref, secrets_file=secrets_file) if secret_ref else None)
        self._transport = transport or self._urllib_transport
        self._timeout = timeout
        if registry is not None:
            spec: Dict[str, Any] = {"id": server_id, "transport": "http", "url": self.base_url,
                                    "auth_header": auth_header, "tools": self._tool_classes}
            if secret_ref:
                spec["secret_ref"] = secret_ref
            if capability:
                spec["capability"] = capability
            registry.register(spec, origin="operator")

    def call(self, tool: str, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
             auth: bool = True) -> Dict[str, Any]:
        """Govern, then (if allowed) send. Classification comes from the tool's DECLARED entry, so
        gating is mount-driven. Returns `{ok, gated, allowed, result|error|rule_id|reason}`."""
        decision = self.kernel.govern(
            to_action(tool, body or {}, classification=self._tool_classes.get(tool)))
        if not decision.allow:
            return {"ok": False, "gated": True, "allowed": False,
                    "rule_id": decision.rule_id, "reason": decision.reason}
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self._api_key:
                return {"ok": False, "gated": False, "error": (
                    f"no API key resolved for {self._secret_ref!r} — inject it (env or "
                    "secrets/*.env) before a real call")}
            headers[self._auth_header] = self._api_key
        try:
            result = self._transport(method, self.base_url + path, body, headers)
            return {"ok": True, "gated": False, "allowed": True, "result": result}
        except Exception as e:  # noqa: BLE001 — surface, never crash the turn
            return {"ok": False, "gated": False, "error": f"{type(e).__name__}: {e}"}

    def _urllib_transport(self, method: str, url: str, body: Optional[Dict[str, Any]],
                          headers: Dict[str, str]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}
