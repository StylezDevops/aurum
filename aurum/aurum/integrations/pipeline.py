"""Governed client for the 24KR release pipeline API (Phase F).

Aurum drives the pipeline THROUGH the governance kernel: every call is `kernel.govern()`-cleared
before it leaves. `health`/`releases`/`validate` have no side effect (safe reads, allowed); the
irreversible **`submit`** (a real Label Engine submission) is `commit_outward` → it is GATED to
the FULL authority band, so at baseline it is refused (shadow-contained) and only fires once that
authority is earned/granted — the live instance of the Phase-D pattern.

Secret-by-reference: the API key is resolved from a `secret_ref` (env → gitignored `secrets/` →
later OneCLI vault) at construction and sent only in the `X-API-Key` header; never logged, never
embedded. The pipeline is registered in the F1 MCP registry as a remote/HTTP MCP (nothing baked).
HTTP is stdlib urllib; `transport` is injectable so the governed flow is testable offline.
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


def resolve_secret(ref: str, *, secrets_file: str = "secrets/pipeline.env") -> Optional[str]:
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


class PipelineClient:
    """Governed pipeline caller. `transport(method, url, body, headers) -> dict` is injectable
    for tests; the default uses urllib. A gated action is NEVER sent (returns the gated decision).

    The pipeline's tools DECLARE their governance classification here (not in the baked
    action_map seed) and register it on the mount — so this dynamic/remote tool is governed from
    durable registry data: health/releases/validate are safe reads; submit is the IRREVERSIBLE,
    full-band step. An AA-synthesized tool follows the same shape (declare → register → govern)."""

    # tool -> {capability_class, action_class, risk_tier, irreversible}. Registered on the mount.
    _TOOL_CLASS: Dict[str, Dict[str, Any]] = {
        "pipeline_health":   {"capability_class": "read", "action_class": "advise", "risk_tier": "safe_read"},
        "pipeline_releases": {"capability_class": "read", "action_class": "advise", "risk_tier": "safe_read"},
        "pipeline_validate": {"capability_class": "read", "action_class": "advise", "risk_tier": "safe_read"},
        "pipeline_submit":   {"capability_class": "pipeline", "action_class": "commit_outward",
                              "risk_tier": "consequential", "irreversible": True},
    }

    def __init__(self, kernel: Any, *, base_url: Optional[str] = None,
                 secret_ref: str = "PIPELINE_API_KEY", api_key: Optional[str] = None,
                 registry: Any = None, transport: Optional[Callable[..., Dict[str, Any]]] = None,
                 timeout: float = 900.0) -> None:
        self.kernel = kernel
        port = resolve_secret("PIPELINE_PORT") or "9111"
        self.base_url = (base_url or f"http://127.0.0.1:{port}").rstrip("/")
        self._secret_ref = secret_ref
        self._api_key = api_key if api_key is not None else resolve_secret(secret_ref)
        self._transport = transport or self._urllib_transport
        self._timeout = timeout
        if registry is not None:
            # Remote-first: register the pipeline as an HTTP MCP — URL + secret_ref + the per-tool
            # governance classification, all on the durable mount. Nothing baked into the image.
            registry.register({"id": "24kr-pipeline", "transport": "http", "url": self.base_url,
                               "secret_ref": secret_ref, "auth_header": "X-API-Key",
                               "capability": "submit 24KR release", "tools": self._TOOL_CLASS},
                              origin="operator")

    # -- governed operations ----------------------------------------------
    def health(self) -> Dict[str, Any]:
        return self._call("pipeline_health", "GET", "/health", auth=False)

    def releases(self) -> Dict[str, Any]:
        return self._call("pipeline_releases", "GET", "/releases")

    def validate(self, folder_name: str) -> Dict[str, Any]:
        return self._call("pipeline_validate", "POST", "/validate",
                          body={"folder_name": folder_name})

    def submit(self, folder_name: str, *, headless: Optional[bool] = None) -> Dict[str, Any]:
        """The IRREVERSIBLE step. Gated to the full band: at baseline this returns the gate
        decision and does NOT call the pipeline (shadow containment)."""
        body: Dict[str, Any] = {"folder_name": folder_name}
        if headless is not None:
            body["headless"] = headless
        return self._call("pipeline_submit", "POST", "/submit", body=body)

    # -- the governed seam -------------------------------------------------
    def _call(self, tool: str, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
              auth: bool = True) -> Dict[str, Any]:
        # Classify from the tool's DECLARED classification (registry-backed), not a baked table.
        decision = self.kernel.govern(to_action(tool, body or {},
                                                classification=self._TOOL_CLASS.get(tool)))
        if not decision.allow:
            return {"ok": False, "gated": True, "allowed": False,
                    "rule_id": decision.rule_id, "reason": decision.reason}
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self._api_key:
                return {"ok": False, "gated": False, "error": (
                    f"no API key resolved for {self._secret_ref!r} — inject it (env or "
                    "secrets/pipeline.env) before a real call")}
            headers["X-API-Key"] = self._api_key
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
