"""aurum-governance plugin — the live governance seam.

Registers a ``pre_tool_call`` hook that routes every tool call through the
:class:`aurum.kernel.GovernanceKernel` (PK → AG → CA → EL) BEFORE the tool runs.
A denied / gated / degraded decision returns ``{"action": "block", "message": ...}``;
an allowed decision returns ``None`` and the tool proceeds.

Activation: opt-in via ``AURUM_GOVERNANCE=1`` (set by ``container/aurum/entrypoint.py``),
matching the ``AURUM_GUARD_SKILLS`` / ``AURUM_SKILL_CI`` / ``AURUM_TOOLSMITH`` pattern.
``AURUM_GOVERNANCE_DISABLE=1`` is a kill-switch (recover from a governance bug without a
redeploy). Stock Hermes leaves this off.

Fail-safe: the kernel applies the tiered posture (PK/EL fault → full fail-closed;
AG/CA fault → degrade to read-only). The plugin's own outer guard is the last resort —
if even constructing/calling the kernel raises, it BLOCKS with a loud message (the env
flag makes this safe to disable).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_kernel = None
_kernel_lock = threading.Lock()


def _import_governance():
    """Import the organs from whichever root the brain is running under.

    The organs package is nested at ``<repo>/aurum/aurum`` and ``<repo>/aurum`` has no
    ``__init__`` (namespace dir). So it imports as ``aurum.*`` when CWD is the organs dir
    (tests) but ``aurum.aurum.*`` from the brain's repo-root context. Try both. (The pending
    Hermes→aurum rename will collapse this nesting and remove the fallback.)
    """
    try:
        from aurum.kernel import GovernanceKernel
        from aurum.action_map import SAFE_READ, risk_tier, to_action
    except ImportError:
        from aurum.aurum.kernel import GovernanceKernel  # type: ignore[no-redef]
        from aurum.aurum.action_map import (  # type: ignore[no-redef]
            SAFE_READ, risk_tier, to_action,
        )
    return GovernanceKernel, to_action, risk_tier, SAFE_READ


def _enabled() -> bool:
    if os.environ.get("AURUM_GOVERNANCE_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
        return False
    return os.environ.get("AURUM_GOVERNANCE", "") == "1"


def _home() -> str:
    """Durable state root for governance (EL / authority / BB / OI). MUST be a mount that
    outlives the --rm cage — a host bind dir, Azure Files/EFS share, or a k8s PVC; the
    container sees only this path (mount-jailed) and writes the ledger here. One knob,
    deployment picks the backing store: AURUM_STATE_ROOT overrides; else HERMES_HOME; else
    ~/.hermes."""
    return (os.environ.get("AURUM_STATE_ROOT")
            or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def _get_kernel():
    """Lazily build a process-singleton kernel rooted at HERMES_HOME."""
    global _kernel
    if _kernel is not None:
        return _kernel
    with _kernel_lock:
        if _kernel is None:
            GovernanceKernel, _, _, _ = _import_governance()
            _kernel = GovernanceKernel(home=_home())
    return _kernel


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Optional[Dict[str, str]]:
    """Govern a tool call. Return None to allow, a block dict to refuse.

    This function MUST NOT raise. PluginManager.invoke_hook swallows a throwing
    callback (logs + skips it), which would silently fall OPEN — bypassing
    governance. So the ENTIRE body (including the enabled-check and imports) is
    wrapped: any unexpected error fails CLOSED while governance is engaged.
    """
    try:
        if not _enabled():
            return None
        _, to_action, _, _ = _import_governance()
        action = to_action(tool_name, args if isinstance(args, dict) else {})
        decision = _get_kernel().govern(action)
        if decision.allow:
            return None
        return {"action": "block", "message": f"aurum-governance: {decision.reason}"}
    except Exception as exc:  # last-resort guard — never let the hook fall open
        logger.error("aurum-governance fault on %s: %s", tool_name, exc)
        # If governance isn't actually engaged, don't block (avoid bricking a
        # non-governed session on an unrelated error).
        if os.environ.get("AURUM_GOVERNANCE") != "1" or \
                os.environ.get("AURUM_GOVERNANCE_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
            return None
        return {
            "action": "block",
            "message": (
                f"aurum-governance fault while evaluating '{tool_name}' — blocked "
                f"(fail-closed). Set AURUM_GOVERNANCE_DISABLE=1 to bypass. ({exc})"
            ),
        }


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    status: str = "",
    **_: Any,
) -> None:
    """Feed the tool's OUTCOME into the OI→BB→AG loop (proxy path). A failed/errored
    consequential outcome reflexively demotes the class + banks a BB lesson; a successful
    one is HELD (proxy success never promotes). Skips pure reads. Best-effort; never raises.
    """
    if not _enabled():
        return
    try:
        _, to_action, risk_tier, SAFE_READ = _import_governance()
        if risk_tier(tool_name) == SAFE_READ:
            return  # side-effect-free reads don't move authority
        action = to_action(tool_name, args if isinstance(args, dict) else {})
        errored = status in {"error", "failed"} or _looks_like_error(result)
        task_result = {
            "completed": not errored,
            "proxy_satisfied": not errored,
            "quality": 0.3 if errored else 1.0,
            "error": _result_text(result) if errored else None,
        }
        _get_kernel().observe_outcome(action, task_result)
    except Exception as exc:
        logger.debug("aurum-governance post_tool_call note skipped: %s", exc)


def _looks_like_error(result: Any) -> bool:
    return isinstance(result, str) and result.lstrip().startswith('{"error"')


def _result_text(result: Any) -> str:
    return result if isinstance(result, str) else repr(result)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
