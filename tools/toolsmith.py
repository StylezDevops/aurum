# =============================================================================
# PRIOR ART / DONOR -- NOT CANONICAL. Canonical organ: aurum/aurum/spine/toolsmith.py (TS).
# Still RUNS in the container today, but this is a DONOR: migrate its logic INTO the
# scaffold TS organ; do NOT extend it here as if it were the organ. A future session
# builds TS in the scaffold, not in this file. build_state.BUILT['TS'] stays False
# until that scaffold organ is spec-complete. (Spine reconciliation: Option B.)
# =============================================================================
"""Toolsmith — governed authoring lifecycle for agent-proposed tools.

The agent already authors *skills* (sandboxed Markdown+assets, gated by the Policy
Kernel and Skill-CI). It does not author *tools*. A tool is different in kind: it
is Python that runs with the agent's **full privileges**, not inside the skill
sandbox. So the Toolsmith's governing invariant is strict:

    A proposed tool is scanned and tested, but NEVER auto-registered into the
    running agent and NEVER written into the image. It lands in a staging area
    with a recorded verdict; activating it is a separate, human-reviewed deploy
    step deliberately outside the agent's reach.

This is fail-closed by construction: the worst an agent can do via the Toolsmith
is leave a validated proposal on disk for a human to review. Lifecycle is modeled
on the curator (staged → tested → [human] approved/archived); scanning reuses the
Policy Kernel (``skills_guard``); test execution reuses Skill-CI's hardened
sandbox (``skill_ci.run_sandboxed_tests``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tool name: a conservative Python-identifier-ish slug.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# Status values a proposal can carry. Note there is no agent-reachable "active":
# promotion to a live tool is a separate reviewed deploy step by design.
STATUS_BLOCKED = "blocked"        # static scan flagged it dangerous; not executed
STATUS_TESTED_FAIL = "tested-fail"  # scan clean, tests ran and failed
STATUS_TESTED_PASS = "tested-pass"  # scan clean, tests passed — awaiting review
STATUS_NO_TESTS = "untested"      # scan clean but no tests supplied


@dataclass
class ToolProposal:
    name: str
    status: str
    detail: str
    verdict: Optional[str] = None
    findings: List[str] = field(default_factory=list)
    proposed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _staging_root(hermes_home: Optional[Path] = None) -> Path:
    if hermes_home is None:
        try:
            from hermes_constants import get_hermes_home
            hermes_home = get_hermes_home()
        except Exception:  # pragma: no cover - defensive
            hermes_home = Path.home() / ".hermes"
    return Path(hermes_home) / "tools_staging"


def _scan_dir(staging: Path) -> tuple[str, List[str]]:
    """Static-scan every .py under the staging dir; return (verdict, finding strs).

    Reuses the Policy Kernel scanner — including the import-time side-effect
    detector, which correctly flags dangerous *module-level* code without
    penalising normal tool bodies (functions that call subprocess/network are
    fine; running them at import time is not).
    """
    from tools.skills_guard import scan_file, _determine_verdict

    findings = []
    for f in sorted(staging.rglob("*.py")):
        if f.is_file():
            findings.extend(scan_file(f, str(f.relative_to(staging))))
    verdict = _determine_verdict(findings)
    summaries = [
        f"{fi.severity}:{fi.category} {fi.file}:{fi.line} {fi.match[:60]}"
        for fi in findings
    ]
    return verdict, summaries


def propose_tool(
    name: str,
    code: str,
    *,
    test_code: Optional[str] = None,
    hermes_home: Optional[Path] = None,
    timeout: int = 60,
) -> ToolProposal:
    """Stage, scan, and (if clean) test a proposed tool. Never activates it.

    Pipeline (fail-closed at every step):
      1. Validate the name.
      2. Write ``{name}.py`` (+ optional ``tests/test_{name}.py``) into
         ``HERMES_HOME/tools_staging/{name}/``.
      3. Static scan all Python. A *dangerous* verdict → STATUS_BLOCKED, and the
         tests are NOT executed.
      4. If clean and tests were supplied, run them in the hardened sandbox.
      5. Persist ``meta.json`` with the verdict/status. Activation is out of scope.
    """
    if not _NAME_RE.match(name or ""):
        return ToolProposal(name=name, status=STATUS_BLOCKED,
                            detail="invalid tool name (use a lowercase slug: ^[a-z][a-z0-9_]{2,40}$)")

    staging = _staging_root(hermes_home) / name
    try:
        (staging / "tests").mkdir(parents=True, exist_ok=True)
        (staging / f"{name}.py").write_text(code, encoding="utf-8")
        if test_code is not None:
            (staging / "tests" / f"test_{name}.py").write_text(test_code, encoding="utf-8")
    except OSError as e:
        return ToolProposal(name=name, status=STATUS_BLOCKED, detail=f"could not stage tool: {e}")

    verdict, findings = _scan_dir(staging)
    if verdict == "dangerous":
        result = ToolProposal(
            name=name, status=STATUS_BLOCKED, verdict=verdict, findings=findings,
            detail="static scan flagged the tool as dangerous; not executed. "
                   "Review the findings and resubmit without the flagged code.",
        )
        _write_meta(staging, result)
        return result

    has_tests = test_code is not None and test_code.strip() != ""
    if not has_tests:
        result = ToolProposal(
            name=name, status=STATUS_NO_TESTS, verdict=verdict, findings=findings,
            detail="scan clean, but no tests were supplied — a tool needs tests "
                   "before it can be reviewed for promotion.",
        )
        _write_meta(staging, result)
        return result

    from tools.skill_ci import run_sandboxed_tests

    ran = run_sandboxed_tests(staging, timeout=timeout)
    status = STATUS_TESTED_PASS if ran.ok else STATUS_TESTED_FAIL
    result = ToolProposal(
        name=name, status=status, verdict=verdict, findings=findings,
        detail=(
            "scan clean and tests passed — staged for human review. NOT active: "
            "promotion to a live tool is a separate reviewed deploy."
            if ran.ok else f"scan clean but tests failed: {ran.detail}"
        ),
    )
    _write_meta(staging, result)
    return result


def _write_meta(staging: Path, result: ToolProposal) -> None:
    try:
        (staging / "meta.json").write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover - defensive
        logger.debug("Toolsmith: could not write meta.json for %s", staging, exc_info=True)


def list_proposals(hermes_home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all staged tool proposals (their persisted meta), newest first."""
    root = _staging_root(hermes_home)
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        meta = d / "meta.json"
        try:
            out.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda m: m.get("proposed_at", ""), reverse=True)
    return out


def get_proposal(name: str, hermes_home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    meta = _staging_root(hermes_home) / name / "meta.json"
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = [
    "ToolProposal",
    "propose_tool",
    "list_proposals",
    "get_proposal",
    "STATUS_BLOCKED",
    "STATUS_TESTED_FAIL",
    "STATUS_TESTED_PASS",
    "STATUS_NO_TESTS",
]


# ---------------------------------------------------------------------------
# Tool registration — gated to the Aurum cage, fail-closed
# ---------------------------------------------------------------------------

def _toolsmith_check() -> bool:
    """Gate: the Toolsmith tool is only offered inside the Aurum cage.

    The container entrypoint sets AURUM_TOOLSMITH=1. Stock Hermes and the host
    never see it. Even when present, it cannot activate a tool — at most it
    leaves a scanned+tested proposal on disk for human review.
    """
    return os.environ.get("AURUM_TOOLSMITH") == "1"


_PROPOSE_TOOL_SCHEMA = {
    "name": "propose_tool",
    "description": (
        "Propose a new Python tool. The proposal is statically scanned and its "
        "tests are run in a hardened sandbox, then staged for human review. It is "
        "NEVER auto-activated — a separate reviewed deploy promotes it to a live "
        "tool. Always include tests."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "lowercase slug, e.g. 'parse_invoice'"},
            "code": {"type": "string", "description": "the tool's Python source"},
            "test_code": {"type": "string", "description": "pytest tests for the tool"},
        },
        "required": ["name", "code", "test_code"],
    },
}


def _propose_tool_handler(args: Dict[str, Any], **_kw) -> Dict[str, Any]:
    return asdict(propose_tool(
        args.get("name", ""),
        args.get("code", ""),
        test_code=args.get("test_code"),
    ))


# Register defensively: a registration hiccup must never break module import or
# agent startup. The tool stays absent (check_fn) unless the cage enables it.
try:  # pragma: no cover - exercised via registry integration, not unit tests
    from tools.registry import registry

    registry.register(
        name="propose_tool",
        toolset="skills",
        schema=_PROPOSE_TOOL_SCHEMA,
        handler=_propose_tool_handler,
        check_fn=_toolsmith_check,
        description="Propose a scanned, sandbox-tested tool for human review (never auto-activated).",
        emoji="🛠️",
    )
except Exception:  # pragma: no cover - defensive
    logger.debug("Toolsmith tool registration skipped", exc_info=True)
