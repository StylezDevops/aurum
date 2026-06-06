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
}

# Tools whose action_type is a PK taint SOURCE (sensitive read) or SINK (egress).
# Setting action_type to the taint token lets PK.check_chain detect a
# source -> sink exfiltration path across a real tool sequence (AURUM_ERR_007/009).
_TAINT_SOURCE_TOOLS = frozenset({"read_secret", "read_credential", "get_secret"})
_TAINT_SINK_TOOLS = frozenset({"send_email", "send_message", "http_post", "git_push"})

# capability_class -> privilege weight (feeds PK aggregate-cap rules).
_PRIVILEGE: Dict[str, float] = {
    "read": 0.0, "ingest": 0.1, "file_write": 0.2,
    "exec": 0.3, "tool_lifecycle": 0.3, "network": 0.4,
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
}


def risk_tier(tool_name: str) -> str:
    """Return the risk tier for a tool. Unknown tools fail safe to CONSEQUENTIAL."""
    entry = _TOOL_TABLE.get(tool_name)
    return entry[2] if entry is not None else CONSEQUENTIAL


def _taint_action_type(tool_name: str, default: str) -> str:
    if tool_name in _TAINT_SOURCE_TOOLS:
        return "secret_read"      # a PK taint SOURCE
    if tool_name in _TAINT_SINK_TOOLS:
        return "network_send"     # a PK taint SINK
    return default


def to_action(tool_name: str, args: Optional[Dict[str, Any]] = None,
              justification_sources: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build a PK/AG `action` dict from a live tool call.

    Unknown tools fail safe: treated as CONSEQUENTIAL with the `exec` class.
    """
    args = args if isinstance(args, dict) else {}
    entry = _TOOL_TABLE.get(tool_name)
    if entry is not None:
        capability_class, action_class, _tier = entry
    else:
        capability_class, action_class = "exec", "code_edit"  # fail safe
    resource = args.get("path") or args.get("url") or args.get("file_path") or ""
    return {
        "action_id": uuid.uuid4().hex,
        "action_type": _taint_action_type(tool_name, capability_class),
        "tool_name": tool_name,
        "capability_class": capability_class,
        "action_class": action_class,
        "privilege": _PRIVILEGE.get(capability_class, 0.3),
        "resource": resource,
        "justification_sources": list(justification_sources or ["operator"]),
    }
