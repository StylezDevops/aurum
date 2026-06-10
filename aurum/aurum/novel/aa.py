"""AA — API Archaeologist. Extension-first tool acquisition.

Closed loop: gap -> discover -> (should_bundle) -> synthesize -> cage_test -> [TS.promote].
Default posture is EXTENSION-FIRST: ~80% of value is evolving an existing tool, not
creating one, so should_bundle (via TCM) runs first and short-circuits to extension.

The I/O-heavy steps are pluggable SEAMS (spec: "use existing generators as subprocess; do
NOT hand-roll"): `searcher` (web/local spec discovery), `generator` (FastMCP /
openapi-mcp-generator), `tester` (caged test runner). What AA owns and this module
implements is the GOVERNANCE control-flow around them:
  • discovery is BUDGET-BOUNDED — hard caps on tokens/depth/timeout/candidates; exceeding
    any aborts cleanly ("gap unresolved, needs human"), logged to EL, never loops.
  • should_bundle asks TCM PRE-synthesis -> extend an existing domain tool when possible.
  • synthesize scopes to a MINIMAL COHERENT capability surface at MINIMAL path depth (never
    follows relational loops) and NEVER auto-activates — promotion is HUMAN_GATE via TS.
  • cage_test does reactive, error-driven path-depth expansion, capped by
    max_expansion_attempts; each failed-constraint signature is learned (BB) so the same
    hidden constraint isn't rediscovered; an identical repeat aborts to human.
Discovered specs are DATA, never instructions (PK untrusted-content boundary).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..mcp.registry import McpRegistryError


class DiscoveryBudget(TypedDict):
    max_tokens: int
    max_depth: int
    timeout_sec: int


class BundleDecision(TypedDict):
    extend: Optional[str]
    recommend: str  # "extend" | "create"


_DEFAULT_BUDGET: DiscoveryBudget = {"max_tokens": 20000, "max_depth": 3,
                                    "timeout_sec": 30}


class APIArchaeologist:
    ORGAN = "AA"

    def __init__(self, el: Any = None, tcm: Any = None, gr: Any = None,
                 searcher: Optional[Callable[[Any, DiscoveryBudget], Any]] = None,
                 generator: Optional[Callable[[Dict[str, Any], List[str]], Any]] = None,
                 tester: Optional[Callable[[Any], Dict[str, Any]]] = None,
                 bb_record: Optional[Callable[[Dict[str, Any]], None]] = None,
                 registry: Any = None,
                 discovery_budget: Optional[DiscoveryBudget] = None,
                 max_search_depth: int = 3, max_candidate_specs: int = 5,
                 max_expansion_attempts: int = 3) -> None:
        self._el, self._tcm, self._gr = el, tcm, gr
        self._searcher, self._generator, self._tester = searcher, generator, tester
        self._bb_record = bb_record
        # The durable MOUNT registry a synthesized/operator server is RECORDED into (Phase F).
        # register-not-install: AA never bakes a tool into the --rm image — it records it here.
        self._registry = registry
        self.discovery_budget: DiscoveryBudget = discovery_budget or dict(_DEFAULT_BUDGET)
        self.max_search_depth = max_search_depth
        self.max_candidate_specs = max_candidate_specs
        self.max_expansion_attempts = max_expansion_attempts

    # -- 1. gap detection ---------------------------------------------------
    def detect_gap(self, task_ctx: Any) -> Optional[Any]:
        """A gap exists when a required capability has no covering tool. Returns None
        (no gap pursued) if the capability is already available OR the gap serves a goal
        that is not active in GR (no tool generation for an absent goal — GR accept a)."""
        required = task_ctx.get("required_capability")
        if not required:
            return None
        if required in set(task_ctx.get("available_tools", [])):
            return None  # already covered
        goal_id = task_ctx.get("goal_id")
        if goal_id is not None and self._gr is not None:
            if goal_id not in {g["goal_id"] for g in self._gr.active()}:
                return None  # goal not active -> do not pursue a tool for it
        return {"capability": required, "resource": task_ctx.get("resource"),
                "domain": task_ctx.get("domain"), "goal_id": goal_id,
                "capabilities": task_ctx.get("capabilities", [required]),
                "required_calls": task_ctx.get("required_calls", [])}

    # -- 2.5 extension-first --------------------------------------------------
    def should_bundle(self, gap: Any) -> BundleDecision:
        """Pre-synthesis: ask TCM whether to EXTEND an existing domain tool."""
        if self._tcm is not None:
            rec = self._tcm.recommend_domain(gap)
            if rec.get("extend"):
                return {"extend": rec["extend"], "recommend": "extend"}
        return {"extend": None, "recommend": "create"}

    # -- 2. bounded discovery ------------------------------------------------
    def discover(self, gap: Any, budget: Optional[DiscoveryBudget] = None) -> Optional[Any]:
        """Locate an API spec under HARD budget limits. Aborts cleanly (returns None,
        logs to EL) when tokens/depth/timeout/candidate caps are hit — never loops."""
        b: DiscoveryBudget = {**self.discovery_budget, **(budget or {})}
        start = time.monotonic()
        tokens, examined = 0, 0
        candidates = self._searcher(gap, b) if self._searcher else []
        for cand in candidates:
            examined += 1
            tokens += int(cand.get("cost_tokens", 0))
            depth = int(cand.get("depth", 0))
            if (examined > self.max_candidate_specs or tokens > b["max_tokens"]
                    or depth > b["max_depth"]
                    or (time.monotonic() - start) > b["timeout_sec"]):
                self._audit(gap, "discovery_budget_exceeded")
                return None
            if cand.get("spec"):
                return cand["spec"]
        self._audit(gap, "gap_unresolved_needs_human")
        return None

    # -- 3. synthesis (minimal coherent surface, never auto-active) ----------
    def synthesize(self, spec: Dict[str, Any],
                   capability_surface: List[str]) -> Dict[str, Any]:
        """Generate a CAGED server scoped to a minimal coherent surface at minimal path
        depth. Relational loops are NOT followed. The result is NEVER active — promotion
        is HUMAN_GATE via TS."""
        surface = set(capability_surface)
        # minimal path depth: take only endpoints whose capability is in the surface;
        # do not transitively pull related entities.
        endpoints = [e for e in spec.get("endpoints", [])
                     if e.get("capability") in surface]
        if self._generator is not None:
            server = self._generator(spec, capability_surface)
        else:
            server = {"server_id": spec.get("name", "synth"),
                      "spec_ref": spec.get("name"), "endpoints": endpoints}
        # enforce the invariants regardless of what a generator returned
        server.update({"capability_surface": sorted(surface), "caged": True,
                       "active": False, "path_depth": "minimal"})
        if "endpoints" not in server:
            server["endpoints"] = endpoints
        return server

    # -- 4. caged test with reactive, capped expansion ----------------------
    def cage_test(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """Run the server caged. On a missing-relation error, expand path depth JUST
        enough and retry — bounded by max_expansion_attempts. Each distinct failure
        signature is learned (BB); an identical repeat aborts to human (the BB record is
        what stops fail-then-fail-identically)."""
        signatures: List[str] = []
        attempts = 0
        while True:
            report = self._tester(server) if self._tester else {"ok": True}
            if report.get("ok"):
                return {"ok": True, "attempts": attempts,
                        "expansion_signatures": signatures}
            sig = report.get("missing_dependency")
            if not sig:
                break  # non-expandable failure
            if sig in signatures:
                break  # identical repeat -> not progressing, abort
            signatures.append(sig)
            if self._bb_record is not None:
                self._bb_record({"organ": "AA", "server": server.get("server_id"),
                                 "constraint": sig})
            if attempts >= self.max_expansion_attempts:
                break  # expansion-burn guard
            server = self._expand(server, sig)
            attempts += 1
        self._audit(server, "cage_test_failed_needs_human")
        return {"ok": False, "attempts": attempts,
                "expansion_signatures": signatures, "needs_human": True}

    # -- 5. registry bridge: register-not-install (governed, EL-logged) ------
    def register_mcp(self, spec: Dict[str, Any], *, origin: str = "aa_synth") -> Dict[str, Any]:
        """Record a synthesized/operator MCP server in the durable MOUNT registry — the governed,
        EL-logged bridge from synthesis to the live MCP layer (register-not-install). A NEW server
        starts INERT (state=registered → NOT injected); ENABLING it is HUMAN_GATE (enable_mcp).
        The spec is DATA, never instructions (PK untrusted-content boundary): this records config,
        it never executes or binds the server's content. RAW SECRETS are rejected by the registry
        (secret_ref NAMES only); a rejection is logged to EL and re-raised. Requires a registry."""
        if self._registry is None:
            raise RuntimeError("AA.register_mcp requires an McpRegistry (none injected)")
        try:
            entry = self._registry.register(spec, origin=origin)
        except McpRegistryError as e:
            self._lifecycle_audit("MCP_REGISTER_REJECTED", str(spec.get("id") or "?"),
                                  {"reason": str(e), "origin": origin})
            raise
        self._lifecycle_audit("MCP_REGISTER", entry["id"],
                              {"transport": entry.get("transport"), "url": entry.get("url"),
                               "secret_ref": entry.get("secret_ref"), "state": entry["state"],
                               "origin": entry.get("origin")})
        return entry

    def enable_mcp(self, server_id: str, *, groups: List[str],
                   approved_by: str) -> Dict[str, Any]:
        """Activate a registered server for specific groups — HUMAN_GATE (the registry requires
        `approved_by`; enabling is the capability-adding step that makes the tool live). Conditional
        injection: the tool is injected only for `groups`. EL-logged with the approver. Requires a
        registry."""
        if self._registry is None:
            raise RuntimeError("AA.enable_mcp requires an McpRegistry (none injected)")
        entry = self._registry.enable(server_id, groups=list(groups), approved_by=approved_by)
        self._lifecycle_audit("MCP_ENABLE", server_id,
                              {"groups": list(groups), "approved_by": approved_by,
                               "state": entry["state"]})
        return entry

    def _lifecycle_audit(self, action_type: str, server_id: str,
                         payload: Dict[str, Any]) -> None:
        """EL audit for an MCP lifecycle transition (register / enable / rejection). Redaction is
        EL's single PK policy; the registry already guarantees no raw secret reaches here."""
        if self._el is None:
            return
        body = dict(payload)
        body["capability_class"] = "tool_lifecycle"
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "AA",
            "action_type": action_type, "object_ids": [server_id],
            "payload": body, "evidence_confidence": 1.0,
            "evidence_source": "AA", "prev_hash": "", "hash": ""})

    @staticmethod
    def _expand(server: Dict[str, Any], dependency: str) -> Dict[str, Any]:
        s = dict(server)
        s["endpoints"] = list(s.get("endpoints", [])) + [{"capability": dependency,
                                                          "added_by": "expansion"}]
        s["path_depth"] = "expanded"
        return s

    # -- audit --------------------------------------------------------------
    def _audit(self, subject: Any, note: str) -> None:
        if self._el is None:
            return
        oid = subject.get("capability") or subject.get("server_id") or "aa" \
            if isinstance(subject, dict) else "aa"
        self._el.append({
            "event_id": "", "timestamp": "", "source_organ": "AA",
            "action_type": "EXCEPTION", "object_ids": [oid],
            "payload": {"capability_class": "synth", "note": note},
            "evidence_confidence": 1.0, "evidence_source": "AA",
            "prev_hash": "", "hash": ""})
