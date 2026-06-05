# =============================================================================
# PRIOR ART / DONOR -- NOT CANONICAL. Canonical organ: aurum/aurum/spine/bb.py (BB).
# Still RUNS in the container today, but this is a DONOR: migrate its logic INTO the
# scaffold BB organ; do NOT extend it here as if it were the organ. A future session
# builds BB in the scaffold, not in this file. build_state.BUILT['BB'] stays False
# until that scaffold organ is spec-complete. (Spine reconciliation: Option B.)
# =============================================================================
"""Black Box — failure recorder that turns mistakes into structured postmortems.

Closes gap #1 of the self-ability map: the background review fires on *learning*
signals ("save a skill") but does not specifically mine *failures*. Black Box adds
that missing trigger. When something the agent did fails — a Skill-CI block, a
non-retryable provider misconfiguration, a tool error — it records a redacted,
structured postmortem under ``HERMES_HOME/blackbox/{timestamp}/``. The skill-review
fork then consumes recent postmortems (via :func:`build_failure_review_addendum`)
so a fix can become a durable skill instead of being relearned next time.

Design choices (principal-grade, honest):
  * **Best-effort, never fatal.** Recording a postmortem must never raise into the
    caller — a failure to log a failure should not break the turn.
  * **Redacted by default.** Free-text context is scrubbed of secret-shaped tokens
    before it ever hits disk. Redaction is pattern-based and therefore best-effort;
    it is paired with truncation and is *in addition to* the cage's guarantee that
    long-lived secrets are never present in the container.
  * **Signal, not noise.** Transient infra errors (rate-limit, timeout, server
    blips) are NOT learnable — :func:`is_learnable_api_error` filters them out so
    the Black Box records misconfigurations and real task failures, not weather.
  * **Out of band.** Writing/reading postmortems is plain file I/O; it runs in the
    already-forked review thread or at a failure site, never on the hot path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_FIELD = 4000  # truncate any single redacted field to bound record size


# ---------------------------------------------------------------------------
# Redaction (best-effort, defense-in-depth on top of the cage)
# ---------------------------------------------------------------------------

# Known credential shapes + key=value secret assignments. Conservative by design:
# we would rather over-redact a postmortem than persist a token.
_REDACTION_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),                      # OpenAI/OpenRouter
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                       # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"),                # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),                           # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                    # Google API key
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}"),        # Bearer <token>
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|auth[_-]?token|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]?[^\s'\"]{6,}"
    ),
]


def redact(text: Any) -> str:
    """Scrub secret-shaped tokens from free text and bound its length."""
    if text is None:
        return ""
    s = str(text)
    for pat in _REDACTION_PATTERNS:
        s = pat.sub("[REDACTED]", s)
    if len(s) > _MAX_FIELD:
        s = s[:_MAX_FIELD] + "…[truncated]"
    return s


# ---------------------------------------------------------------------------
# Postmortem record + store
# ---------------------------------------------------------------------------

@dataclass
class Postmortem:
    """A single redacted failure record."""

    trigger: str           # "skill_ci" | "api_error" | "tool_error" | ...
    title: str
    summary: str
    severity: str = "medium"
    lesson_hint: str = ""
    context: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _blackbox_root(hermes_home: Optional[Path] = None) -> Path:
    if hermes_home is None:
        try:
            from hermes_constants import get_hermes_home
            hermes_home = get_hermes_home()
        except Exception:  # pragma: no cover - defensive
            hermes_home = Path.home() / ".hermes"
    return Path(hermes_home) / "blackbox"


def record_postmortem(
    trigger: str,
    title: str,
    summary: str,
    *,
    severity: str = "medium",
    lesson_hint: str = "",
    context: str = "",
    hermes_home: Optional[Path] = None,
) -> Optional[Path]:
    """Write a redacted postmortem under HERMES_HOME/blackbox/{timestamp}/.

    Returns the record path, or None on any failure (best-effort — a failure to
    record a failure must never propagate into the caller).
    """
    try:
        pm = Postmortem(
            trigger=str(trigger),
            title=redact(title),
            summary=redact(summary),
            severity=str(severity),
            lesson_hint=redact(lesson_hint),
            context=redact(context),
        )
        root = _blackbox_root(hermes_home)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = root / stamp
        suffix = 1
        while run_dir.exists():
            suffix += 1
            run_dir = root / f"{stamp}-{suffix}"
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "postmortem.json").write_text(
            json.dumps(asdict(pm), indent=2), encoding="utf-8"
        )
        (run_dir / "POSTMORTEM.md").write_text(
            f"# {pm.title}\n\n"
            f"- **trigger:** {pm.trigger}\n"
            f"- **severity:** {pm.severity}\n"
            f"- **recorded:** {pm.recorded_at}\n\n"
            f"## What happened\n{pm.summary}\n\n"
            + (f"## Lesson\n{pm.lesson_hint}\n\n" if pm.lesson_hint else "")
            + (f"## Context\n{pm.context}\n" if pm.context else ""),
            encoding="utf-8",
        )
        return run_dir
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Black Box: failed to record postmortem (%s): %s", trigger, e)
        return None


def recent_postmortems(limit: int = 5, hermes_home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the newest ``limit`` postmortem records (newest first)."""
    root = _blackbox_root(hermes_home)
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)
    except OSError:
        return []
    for d in dirs[:limit]:
        rec = d / "postmortem.json"
        try:
            out.append(json.loads(rec.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Failure classification: which failures are worth a postmortem
# ---------------------------------------------------------------------------

# Provider failures that are transient by nature — retrying handles them, so they
# are weather, not a lesson, even when a given attempt is marked non-retryable.
_TRANSIENT_REASONS = {"timeout", "server_error", "overloaded", "rate_limit", "rate_limited"}


def is_learnable_api_error(classified: Any) -> bool:
    """True if a ClassifiedError reflects a misconfiguration worth recording.

    Transient infra (rate-limit/timeout/server) is filtered out; non-retryable
    auth/config/permanent failures are kept — those usually mean a setting needs
    fixing, which is exactly what a skill/memory note should capture.
    """
    if classified is None:
        return False
    if getattr(classified, "retryable", True):
        return False
    reason = getattr(getattr(classified, "reason", None), "name", "") or ""
    return reason.lower() not in _TRANSIENT_REASONS


# ---------------------------------------------------------------------------
# Review consumption
# ---------------------------------------------------------------------------

def build_failure_review_addendum(
    hermes_home: Optional[Path] = None, limit: int = 5
) -> str:
    """Build a skill-review prompt addendum from recent postmortems, or "".

    Appended to the skill-review fork's prompt so recorded failures become a
    first-class learning signal: "these things failed — should a skill capture
    the fix?" Returns "" when there is nothing to report (no prompt cost).
    """
    records = recent_postmortems(limit=limit, hermes_home=hermes_home)
    if not records:
        return ""
    lines = [
        "\n\nRecent failures were recorded by the Black Box. Treat each as a "
        "first-class skill signal: if a durable fix or guardrail would prevent a "
        "repeat, capture it in the relevant skill (prefer patching an existing "
        "umbrella). If a failure is purely transient/environmental, skip it.\n",
    ]
    for r in records:
        title = r.get("title", "(untitled)")
        trigger = r.get("trigger", "?")
        hint = r.get("lesson_hint") or r.get("summary", "")
        lines.append(f"  • [{trigger}] {title} — {hint}".rstrip())
    return "\n".join(lines)


__all__ = [
    "Postmortem",
    "redact",
    "record_postmortem",
    "recent_postmortems",
    "is_learnable_api_error",
    "build_failure_review_addendum",
]
