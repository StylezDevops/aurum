"""Skill-CI / Regression Guard — validate-before-promote (self-ability-map §3 + §3a).

Before a self-authored or installed skill goes live, Skill-CI enforces two gates:

  1. **Static security scan** (``skills_guard.scan_skill``). A skill whose scan is
     not allowed is **never executed** — Skill-CI fails closed at this phase, so a
     flagged payload cannot reach the test runner.
  2. **Regression / detonation** — if the skill ships tests, they run in a hardened
     subprocess. A clean skill whose tests pass is promotable; failing tests block.

Isolation model (stated precisely — we do not claim isolation we cannot deliver):

  * The hard containment boundary is the **outer nanoclaw cage**: the agent already
    runs in an ephemeral ``--rm`` container that holds no long-lived secrets (OneCLI
    injects per request) and has controlled egress. Skill-CI runs *inside* that.
  * Skill-CI layers defense-in-depth on top: it (a) refuses to execute a skill the
    scanner flagged, (b) copies the skill into an ephemeral temp dir so nothing
    writes back to the real skills tree, (c) runs the test process with a **scrubbed
    environment** (secrets stripped via ``code_execution_tool._scrub_child_env``),
    (d) imposes a hard wall-clock timeout and an output cap, and (e) on Linux drops
    network with ``unshare -rn`` when the kernel permits it, plus blackhole proxy
    env. When hard network isolation is *required* but unavailable, it refuses to
    execute rather than run unconfined.

This module is pure-Python and side-effect free on import; nothing runs untrusted
code unless ``run_skill_ci(..., execute=True)`` is called on a skill that already
passed the static scan.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Wall-clock ceiling for a single skill's test run (seconds). The outer cage owns
# the absolute timeout; this is a tighter inner bound so a hung test cannot stall
# a skill write indefinitely.
DEFAULT_TIMEOUT = 60
# Cap captured test output so a noisy/adversarial run cannot blow up memory/logs.
_MAX_OUTPUT = 64 * 1024


@dataclass
class SkillCIResult:
    """Outcome of a Skill-CI run."""

    ok: bool
    phase: str  # "scan" | "test" | "skipped"
    detail: str
    verdict: Optional[str] = None  # static-scan verdict, when known
    tests_ran: bool = False
    network_isolated: bool = False


# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------

def _has_tests(skill_dir: Path) -> bool:
    """True if the skill ships any pytest-style test files."""
    if (skill_dir / "tests").is_dir():
        for f in (skill_dir / "tests").rglob("*.py"):
            if f.name.startswith("test_") or f.name.endswith("_test.py"):
                return True
    for f in skill_dir.glob("test_*.py"):
        if f.is_file():
            return True
    for f in skill_dir.glob("*_test.py"):
        if f.is_file():
            return True
    return False


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------

def _network_sandbox_prefix() -> Optional[List[str]]:
    """Return an argv prefix that drops network for the child, or None.

    Uses ``unshare -rn`` (new user + network namespace) when available and
    permitted — this gives the child an empty network stack with no egress.
    Returns None on platforms/kernels where it is unavailable (e.g. Windows, or
    a container without user-namespace permission), in which case the caller
    decides whether degraded isolation is acceptable.
    """
    if sys.platform.startswith("win"):
        return None
    unshare = shutil.which("unshare")
    if not unshare:
        return None
    try:
        # Probe: can we actually create the namespaces here? (CAP/userns gated.)
        probe = subprocess.run(
            [unshare, "-rn", "true"],
            capture_output=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return [unshare, "-rn", "--"]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _scrubbed_env() -> dict:
    """Build a secret-free child environment, reusing the vetted scrubber.

    Falls back to a conservative minimal env if the scrubber is unavailable.
    Adds blackhole proxy vars so any best-effort HTTP attempt fails fast even
    when kernel-level network isolation is not in force.
    """
    try:
        from tools.code_execution_tool import _scrub_child_env

        env = _scrub_child_env(dict(os.environ))
    except Exception:  # pragma: no cover - defensive fallback
        keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
        env = {k: v for k, v in os.environ.items() if k in keep}
    # Blackhole egress: point proxies at a closed local port; clear no_proxy.
    env.update({
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "http_proxy": "http://127.0.0.1:1",
        "https_proxy": "http://127.0.0.1:1",
        "NO_PROXY": "",
        "no_proxy": "",
        # Deterministic, quiet test runs.
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    })
    return env


def _build_runner_cmd(target: Path) -> List[str]:
    """Choose the in-cage test runner.

    Prefer pytest (full regression). If pytest is not importable, fall back to a
    collection-only import of each test module — which still detonates any
    import/collection-time payload (the §3a vector) inside the sandbox, without
    asserting test outcomes.
    """
    import importlib.util

    if importlib.util.find_spec("pytest") is not None:
        # -o addopts= clears the project's signal-based --timeout (Linux-cage we
        # supply our own wall timeout via subprocess); -p no:cacheprovider keeps
        # the ephemeral dir clean.
        return [
            sys.executable, "-m", "pytest", str(target),
            "-q", "-x", "-p", "no:cacheprovider", "-o", "addopts=",
        ]
    return [sys.executable, "-c", _IMPORT_DETONATE_SRC, str(target)]


# Stdlib-only fallback runner: import every test module under the target dir.
# Importing executes module-level (collection-time) code — the supply-chain
# vector — inside the already-scrubbed/namespaced subprocess.
_IMPORT_DETONATE_SRC = (
    "import sys, importlib.util, pathlib\n"
    "root = pathlib.Path(sys.argv[1])\n"
    "mods = list(root.rglob('test_*.py')) + list(root.rglob('*_test.py'))\n"
    "for m in mods:\n"
    "    spec = importlib.util.spec_from_file_location(m.stem, m)\n"
    "    mod = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(mod)\n"
    "print(f'imported {len(mods)} test module(s)')\n"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_skill_ci(
    skill_dir: Path,
    *,
    source: str = "agent-created",
    timeout: int = DEFAULT_TIMEOUT,
    execute: bool = True,
    require_network_isolation: bool = False,
) -> SkillCIResult:
    """Validate a skill before promotion. Never executes a skill that fails the scan.

    Args:
        skill_dir: Path to the skill directory.
        source: Trust source passed to the static scanner.
        timeout: Wall-clock ceiling for the test subprocess.
        execute: When False, run the static scan only (no test execution).
        require_network_isolation: When True, refuse to execute if kernel-level
            network isolation (``unshare -rn``) is unavailable — fail closed
            rather than run tests with only env-level egress denial.

    Returns:
        SkillCIResult.
    """
    skill_dir = Path(skill_dir)

    # ── Phase 1: static scan (fail closed) ────────────────────────────────
    try:
        from tools.skills_guard import scan_skill, should_allow_install

        result = scan_skill(skill_dir, source=source)
        allowed, reason = should_allow_install(result)
        if allowed is not True:
            return SkillCIResult(
                ok=False, phase="scan", verdict=result.verdict,
                detail=f"static scan blocked promotion: {reason}",
            )
    except Exception as e:  # scanner failure must not silently allow execution
        logger.warning("Skill-CI scan error for %s: %s", skill_dir, e, exc_info=True)
        return SkillCIResult(ok=False, phase="scan", detail=f"scan error: {e}")

    verdict = result.verdict

    # ── Phase 2: regression / detonation ──────────────────────────────────
    if not execute or not _has_tests(skill_dir):
        return SkillCIResult(
            ok=True, phase="skipped", verdict=verdict,
            detail="scan clean; no tests to run" if not _has_tests(skill_dir)
                   else "scan clean; execution disabled",
        )

    ran = run_sandboxed_tests(
        skill_dir, timeout=timeout, require_network_isolation=require_network_isolation,
    )
    return SkillCIResult(
        ok=ran.ok, phase="test", verdict=verdict, detail=ran.detail,
        tests_ran=ran.tests_ran, network_isolated=ran.network_isolated,
    )


@dataclass
class SandboxRun:
    """Outcome of running a directory's tests in the hardened subprocess."""

    ok: bool
    detail: str
    tests_ran: bool = False
    network_isolated: bool = False


def run_sandboxed_tests(
    test_dir: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    require_network_isolation: bool = False,
) -> SandboxRun:
    """Run a directory's pytest suite in the hardened subprocess.

    Shared by Skill-CI and the Toolsmith. The directory is copied to an ephemeral
    workdir (no writeback); the child runs with a secret-scrubbed environment, a
    wall-clock timeout, an output cap, and best-effort network isolation via
    ``unshare -rn`` (degrades gracefully). Callers must perform their own static
    scan FIRST — this function executes code and must never be the only gate.
    """
    test_dir = Path(test_dir)
    net_prefix = _network_sandbox_prefix()
    if require_network_isolation and net_prefix is None:
        return SandboxRun(
            ok=False,
            detail="network isolation required but unavailable (no usable unshare); "
                   "refusing to execute tests unconfined",
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="sandboxci_"))
    try:
        work = tmp_root / test_dir.name
        shutil.copytree(test_dir, work)
        cmd = (net_prefix or []) + _build_runner_cmd(work)
        env = _scrubbed_env()
        try:
            proc = subprocess.run(
                cmd, cwd=str(work), env=env, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxRun(
                ok=False,
                detail=f"tests exceeded {timeout}s wall-clock limit (possible hang)",
                tests_ran=True, network_isolated=net_prefix is not None,
            )

        out = ((proc.stdout or "") + (proc.stderr or ""))[:_MAX_OUTPUT]
        # pytest: 0 = passed, 5 = no tests collected (treat as pass). Anything
        # else (incl. the import-detonate runner's non-zero) is a failure.
        ok = proc.returncode in (0, 5)
        return SandboxRun(
            ok=ok,
            detail=("tests passed" if ok else f"tests failed (exit {proc.returncode})\n{out[-2000:]}"),
            tests_ran=True, network_isolated=net_prefix is not None,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def skill_ci_gate(skill_dir: Path) -> Optional[str]:
    """Gate for the skill_manage write path: return an error string if blocked.

    Mirrors ``skill_manager_tool._security_scan_skill``'s contract (error-or-None)
    so it can be wired in alongside it. Active only when ``AURUM_SKILL_CI=1`` (set
    by the Aurum container entrypoint); a no-op otherwise so stock Hermes and the
    host are unaffected.
    """
    if os.environ.get("AURUM_SKILL_CI") != "1":
        return None
    try:
        res = run_skill_ci(Path(skill_dir), source="agent-created")
    except Exception as e:  # never block a write on a Skill-CI infrastructure bug
        logger.warning("Skill-CI gate error for %s: %s", skill_dir, e, exc_info=True)
        return None
    if res.ok:
        return None
    # Record the block as a Black Box postmortem so the skill-review fork can turn
    # the failure into a durable fix. Best-effort: never let logging break the gate.
    try:
        from agent.black_box import record_postmortem

        record_postmortem(
            "skill_ci",
            title=f"Skill-CI blocked {Path(skill_dir).name} at {res.phase}",
            summary=res.detail,
            severity="high" if res.phase == "scan" else "medium",
            lesson_hint=(
                "A self-authored skill failed validation before promotion. "
                "Capture the corrected approach so the next attempt passes."
            ),
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("Black Box record failed for Skill-CI block", exc_info=True)
    return f"Skill-CI blocked this skill ({res.phase}): {res.detail}"
