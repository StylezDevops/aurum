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
        from aurum.action_map import CONSEQUENTIAL, risk_tier, to_action
    except ImportError:
        from aurum.aurum.kernel import GovernanceKernel  # type: ignore[no-redef]
        from aurum.aurum.action_map import (  # type: ignore[no-redef]
            CONSEQUENTIAL, risk_tier, to_action,
        )
    return GovernanceKernel, to_action, risk_tier, CONSEQUENTIAL


def _enabled() -> bool:
    if os.environ.get("AURUM_GOVERNANCE_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
        return False
    return os.environ.get("AURUM_GOVERNANCE", "") == "1"


def _home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


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
    """Govern a tool call. Return None to allow, a block dict to refuse."""
    if not _enabled():
        return None
    try:
        _, to_action, _, _ = _import_governance()
        action = to_action(tool_name, args if isinstance(args, dict) else {})
        decision = _get_kernel().govern(action)
    except Exception as exc:  # last-resort guard: block loudly (fail-safe posture)
        logger.error("aurum-governance kernel error on %s: %s", tool_name, exc)
        return {
            "action": "block",
            "message": (
                f"aurum-governance fault while evaluating '{tool_name}' — blocked "
                f"(fail-closed). Set AURUM_GOVERNANCE_DISABLE=1 to bypass. ({exc})"
            ),
        }
    if decision.allow:
        return None
    return {"action": "block", "message": f"aurum-governance: {decision.reason}"}


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    status: str = "",
    **_: Any,
) -> None:
    """On a consequential tool error, record a redacted postmortem (failure → fix loop).
    Best-effort; never raises."""
    if not _enabled():
        return
    if status not in {"error", "failed"} and not _looks_like_error(result):
        return
    try:
        _, to_action, risk_tier, CONSEQUENTIAL = _import_governance()
        if risk_tier(tool_name) != CONSEQUENTIAL:
            return
        action = to_action(tool_name, args if isinstance(args, dict) else {})
        _get_kernel().record_failure(action, _result_text(result))
    except Exception as exc:
        logger.debug("aurum-governance post_tool_call note skipped: %s", exc)


def _looks_like_error(result: Any) -> bool:
    return isinstance(result, str) and result.lstrip().startswith('{"error"')


def _result_text(result: Any) -> str:
    return result if isinstance(result, str) else repr(result)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
