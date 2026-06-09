"""Action mapping — translate a live tool call into a governance `action` dict.

This is the **policy surface** of the governance seam: it decides, for each Hermes
tool, (a) its RISK TIER (does it have side effects? does it ingest untrusted
content?), (b) its AG capability_class + action_class (what authority band it needs),
and (c) its PK taint role (source/sink) so `check_chain` can detect exfiltration across
a real tool sequence.

Three risk tiers (see `aurum_organs_spec.md` degraded-mode posture):
  SAFE_READ     — side-effect-free retrieval from already-trusted local state.
                  Allowed even when the governance layer is degraded.
  INGEST        — pulls UNTRUSTED external content (web, browser). Treated as
                  consequential; blocked while degraded; results are `trust:untrusted`.
  CONSEQUENTIAL — writes / exec / network egress / tool lifecycle. Blocked while degraded.

Honest v1 limits (documented, not hidden):
  - justification_sources defaults to ["operator"]: the operator's direct message drove
    the turn. Full AA/SEN provenance tagging needs SEN (not built). PK still denies any
    explicitly-untrusted source, and INGEST actions carry an untrusted marker.
  - the tool tables below are a STARTER mapping; unknown tools default to SAFE_READ-safe
    only if they are demonstrably read-like, else CONSEQUENTIAL (fail-safe: unknown ⇒ treat
    as having side effects).
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

# -- risk tiers --------------------------------------------------------------
SAFE_READ = "safe_read"
INGEST = "ingest"
CONSEQUENTIAL = "consequential"

# -- tool -> (capability_class, action_class, risk_tier) ---------------------
# action_class maps to an AG required band: advise->advisory, propose->readonly,
# code_edit->code, commit_outward->full (see AuthorityGovernor._ACTION_BAND).
_TOOL_TABLE: Dict[str, Tuple[str, str, str]] = {
    # side-effect-free local reads
    "read_file": ("read", "advise", SAFE_READ),
    "read": ("read", "advise", SAFE_READ),
    "grep": ("read", "advise", SAFE_READ),
    "glob": ("read", "advise", SAFE_READ),
    "ls": ("read", "advise", SAFE_READ),
    "list_files": ("read", "advise", SAFE_READ),
    # untrusted external ingestion
    "web_search": ("ingest", "propose", INGEST),
    "web_fetch": ("ingest", "propose", INGEST),
    "fetch": ("ingest", "propose", INGEST),
    "browser": ("ingest", "propose", INGEST),
    "analyze_image": ("ingest", "propose", INGEST),
    # consequential — local writes / exec
    "write_file": ("file_write", "code_edit", CONSEQUENTIAL),
    "patch": ("file_write", "code_edit", CONSEQUENTIAL),
    "edit": ("file_write", "code_edit", CONSEQUENTIAL),
    "terminal": ("exec", "code_edit", CONSEQUENTIAL),
    "execute_code": ("exec", "code_edit", CONSEQUENTIAL),
    "bash": ("exec", "code_edit", CONSEQUENTIAL),
    # consequential — tool/skill lifecycle (HUMAN_GATE via rule table)
    "skill_manage": ("tool_lifecycle", "code_edit", CONSEQUENTIAL),
    "propose_tool": ("tool_lifecycle", "code_edit", CONSEQUENTIAL),
    # consequential — outward / network egress (requires the FULL band → gated by default)
    "send_email": ("network", "commit_outward", CONSEQUENTIAL),
    "send_message": ("network", "commit_outward", CONSEQUENTIAL),
    "http_post": ("network", "commit_outward", CONSEQUENTIAL),
    "git_push": ("network", "commit_outward", CONSEQUENTIAL),
    # Dataverse / D365 workflow surface. Reads are safe; an update is a REVERSIBLE mutation
    # (code band); a DELETE is IRREVERSIBLE → commit_outward (FULL band) so it is gated until
    # the agent holds top authority — the "correctly-gated irreversible step" the build brief
    # requires of a real governed workflow.
    "dataverse_query": ("read", "advise", SAFE_READ),
    "dataverse_get": ("read", "advise", SAFE_READ),
    "dataverse_create": ("dataverse", "code_edit", CONSEQUENTIAL),
    "dataverse_update": ("dataverse", "code_edit", CONSEQUENTIAL),
    "dataverse_delete": ("dataverse", "commit_outward", CONSEQUENTIAL),
    # NOTE: the 24KR pipeline tools are NOT seeded here. As a dynamically-registered (remote)
    # MCP, the pipeline DECLARES its tools' governance classification in the mount F1 registry
    # (see PipelineClient._TOOL_CLASS) and passes it via `to_action(classification=...)` — so a
    # registered/AA-synthesized tool never requires an edit to this baked file. Only the
    # `pipeline` capability CLASS keeps a baseline below (class baselines are operator config).
}

# Tools whose effect is IRREVERSIBLE — flagged on the action so the gate (and FC inverted
# scrutiny / telemetry) can treat them as the highest-consequence class. The flag is metadata;
# the gating itself is the FULL-band requirement carried by their commit_outward action_class.
_IRREVERSIBLE_TOOLS = frozenset({
    "dataverse_delete", "delete_record", "deactivate_record", "git_push",
})  # dynamic tools (e.g. pipeline_submit) carry `irreversible` in their registry classification

# Tools whose action_type is a PK taint SOURCE (sensitive read) or SINK (egress).
# Setting action_type to the taint token lets PK.check_chain detect a
# source -> sink exfiltration path across a real tool sequence (AURUM_ERR_007/009).
_TAINT_SOURCE_TOOLS = frozenset({"read_secret", "read_credential", "get_secret"})
_TAINT_SINK_TOOLS = frozenset({"send_email", "send_message", "http_post", "git_push"})

# capability_class -> privilege weight (feeds PK aggregate-cap rules).
_PRIVILEGE: Dict[str, float] = {
    "read": 0.0, "ingest": 0.1, "file_write": 0.2,
    "exec": 0.3, "tool_lifecycle": 0.3, "network": 0.4, "dataverse": 0.3, "pipeline": 0.4,
}

# Default per-class AG baseline authority an operator install grants. Raising these
# is an operator config decision (HUMAN_GATE per spec: "raising a band's mapping is
# config"). network/outward is left BELOW the full band on purpose, so outward commits
# are gated until the operator explicitly raises authority.
DEFAULT_AG_BASELINE: Dict[str, float] = {
    "read": 0.10,          # advisory floor — always clears `advise`
    "ingest": 0.65,        # readonly band — clears `propose`
    "file_write": 0.85,    # code band — clears `code_edit`
    "exec": 0.85,          # code band
    "tool_lifecycle": 0.85,  # code band (promotion itself is HUMAN_GATE via rules)
    "network": 0.65,       # readonly band — does NOT clear `commit_outward` (full) → gated
    "dataverse": 0.85,     # code band — clears `code_edit` (read/update) but NOT the
    #                        irreversible delete's `commit_outward` (full) → delete gated
    "pipeline": 0.85,      # code band — health/releases/validate are safe reads; the irreversible
    #                        submit needs the full band → gated until authority is earned/granted
}


def risk_tier(tool_name: str, classification: Optional[Dict[str, Any]] = None) -> str:
    """Risk tier for a tool. Resolution order: an explicit `classification` (declared by a
    dynamically-registered / AA-synthesized tool, carried in the mount F1 registry) → the baked
    `_TOOL_TABLE` seed (BUILT-IN Hermes tools only) → fail-safe CONSEQUENTIAL (unknown ⇒ treat as
    having side effects). Dynamic tools must NOT require an edit to this baked file."""
    if classification:
        return str(classification.get("risk_tier", CONSEQUENTIAL))
    entry = _TOOL_TABLE.get(tool_name)
    return entry[2] if entry is not None else CONSEQUENTIAL


def _taint_action_type(tool_name: str, default: str) -> str:
    if tool_name in _TAINT_SOURCE_TOOLS:
        return "secret_read"      # a PK taint SOURCE
    if tool_name in _TAINT_SINK_TOOLS:
        return "network_send"     # a PK taint SINK
    return default


def to_action(tool_name: str, args: Optional[Dict[str, Any]] = None,
              justification_sources: Optional[List[str]] = None,
              domain: Optional[str] = None,
              classification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a PK/AG `action` dict from a live tool call.

    `classification` is how a DYNAMICALLY-REGISTERED / AA-synthesized tool declares its own
    governance shape — `{capability_class, action_class, risk_tier, irreversible}` — carried in
    the mount-resident F1 registry and passed in by the caller. This is the maintainable path:
    a new tool needs a REGISTRY ENTRY on the durable mount, never an edit to this baked file.
    Resolution: `classification` → the baked `_TOOL_TABLE` seed (BUILT-IN tools only) → fail-safe
    (unknown ⇒ CONSEQUENTIAL/exec, treat as having side effects). The resolved risk tier is stored
    on the action as `_risk_tier`, so the kernel governs dynamic tools without consulting this file.

    `domain` (optional) is the SUBJECT area (e.g. 'd365', 'azure', 'k8s') — orthogonal to
    capability_class — engaging the AG familiarity factor when present.
    """
    args = args if isinstance(args, dict) else {}
    if classification:
        capability_class = classification.get("capability_class", "exec")
        action_class = classification.get("action_class", "code_edit")
        tier = classification.get("risk_tier", CONSEQUENTIAL)
        irreversible = bool(classification.get("irreversible"))
    elif tool_name in _TOOL_TABLE:
        capability_class, action_class, tier = _TOOL_TABLE[tool_name]
        irreversible = tool_name in _IRREVERSIBLE_TOOLS
    else:
        capability_class, action_class, tier = "exec", "code_edit", CONSEQUENTIAL  # fail safe
        irreversible = tool_name in _IRREVERSIBLE_TOOLS
    resource = args.get("path") or args.get("url") or args.get("file_path") or ""
    action = {
        "action_id": uuid.uuid4().hex,
        "action_type": _taint_action_type(tool_name, capability_class),
        "tool_name": tool_name,
        "capability_class": capability_class,
        "action_class": action_class,
        "_risk_tier": tier,             # the kernel reads this → no _TOOL_TABLE lookup for dynamic tools
        "privilege": _PRIVILEGE.get(capability_class, 0.3),
        "resource": resource,
        "justification_sources": list(justification_sources or ["operator"]),
    }
    if domain:
        action["domain"] = domain
    if irreversible:
        action["irreversible"] = True   # highest-consequence: gated to the FULL band
    return action


def action_from_event(event: Dict[str, Any], tool_name: str,
                      args: Optional[Dict[str, Any]] = None, *,
                      domain: Optional[str] = None,
                      classification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build an action DRIVEN BY ingested content (a SEN-tagged event). Its `justification_sources`
    are the event's UNTRUSTED provenance (the watcher id — never 'operator'), so the action is
    denied binding by PK's injection boundary (AURUM_ERR_008): ingested content cannot trigger
    actions. This is the bridge that flows SEN provenance into the live `govern()` path, instead of
    the `justification_sources=['operator']` default that assumes the operator's direct channel.
    Fail-safe: an event with no provenance is still treated as untrusted, never operator."""
    sources: List[str] = []
    if isinstance(event, dict):
        sources = [str(s) for s in (event.get("justification_sources") or [])]
        if not sources and event.get("source"):
            sources = [str(event["source"])]
    # never let ingested content claim (or default to) operator trust
    sources = [s for s in sources if s != "operator"] or ["sen:untrusted"]
    action = to_action(tool_name, args, justification_sources=sources, domain=domain,
                       classification=classification)
    action["origin"] = "ingested"
    return action
