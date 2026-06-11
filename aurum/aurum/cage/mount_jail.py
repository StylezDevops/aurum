"""Mount jail — the cage's host-filesystem containment boundary.

RE-DERIVED from security requirements (not transcribed from any prior launcher).
This decides which host directories may be bind-mounted into an ephemeral agent
container. It is the boundary that keeps a caged agent from reading or writing the
host outside an explicitly-granted set, so it is held to the same bar as the kernel:
deny-by-default, canonical-path checks, fail-closed, and covered by AURUM_ERR_021.

Requirements it must satisfy (each is tested):
  R1 DENY BY DEFAULT — with no allowlist, no host dir is mountable beyond the two
     fixed, structural mounts (project RO, group RW). An empty/missing allowlist
     grants zero extra access.
  R2 EXPLICIT GRANT — every *extra* host dir mounted in must be inside an allowlisted
     directory. Anything else is refused.
  R3 CANONICALISE FIRST — resolve symlinks and `.` / `..` with realpath BEFORE the
     containment check, so a symlink (or `../` traversal) that escapes an allowlisted
     dir is caught (the resolved target, not the surface path, is what's checked).
  R4 TRUE ANCESTRY, NOT STRING PREFIX — `/data` must NOT authorise `/data-evil`.
     Containment is a path-component ancestry test (commonpath), never `startswith`.
  R5 FAIL CLOSED — a requested-but-disallowed mount raises `MountDenied` and aborts
     the whole run. We never silently drop a bad mount and proceed with the rest.
  R6 ABSOLUTE ONLY — relative sources are refused (their meaning depends on cwd).
  R7 CONTAINMENT INVARIANT — no mount (structural OR extra) may expose a GOVERNANCE ROOT to the
     container: Aurum runtime code, governance state, policy definitions, authority records,
     identity keys, or ledger storage. Overlap in EITHER direction (mounting the governance root,
     a parent that contains it, or a child inside it) is a CONTAINMENT FAILURE — an absolute deny
     that OVERRIDES the allowlist. A governed agent must not be able to read or tamper the substrate
     that governs it. (Dormant until `governance_roots` is supplied: v1 runs the kernel IN the cage
     with state under the group mount — the known violation the host-side migration resolves.)

Tamper note: the allowlist file is TRUSTED CONFIG and must live outside any
agent-writable mount (e.g. a host-only config dir), so the caged agent cannot widen
its own access by editing it. `load_allowlist` only reads it; it never writes.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping

# Default location for the host-owned allowlist. Deliberately outside the repo and
# outside any per-group (agent-writable) mount — see tamper note above.
DEFAULT_ALLOWLIST_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "aurum", "mount-allowlist.json"
)

# Fixed container mount points.
PROJECT_MOUNT = "/workspace/project"   # read-only
GROUP_MOUNT = "/workspace/group"       # read-write (per-group persistence)
EXTRA_MOUNT_BASE = "/workspace/extra"  # allowlisted host dirs, jailed under here


class MountDenied(Exception):
    """A requested mount is not permitted. Raised to FAIL CLOSED (abort the run)."""


class ContainmentError(MountDenied):
    """CONTAINMENT INVARIANT violation (R7): a mount would expose a governance root (Aurum code /
    governance state / policy / authority records / identity keys / ledger) to the container. A
    subclass of MountDenied so it still fails closed, but a DISTINCT, loud failure — the substrate
    that governs the agent must never be in the agent's filesystem."""


@dataclass(frozen=True)
class Mount:
    host: str        # canonical, absolute host path
    container: str   # path inside the cage
    mode: str        # "ro" | "rw"

    def docker_arg(self) -> List[str]:
        return ["-v", f"{self.host}:{self.container}:{self.mode}"]


def _canon(path: str) -> str:
    """Absolute + symlink-resolved canonical path (R3). realpath resolves `..`/`.`
    and symlinks, so the value returned is the real on-disk target."""
    return os.path.realpath(os.path.abspath(path))


def _within(candidate_canon: str, base_canon: str) -> bool:
    """True iff `candidate_canon` is `base_canon` or a descendant of it (R4).

    Uses path-component ancestry (commonpath), NOT string prefix, so `/data` does
    not contain `/data-evil`. Both inputs must already be canonical absolute paths.
    Different Windows drives raise ValueError from commonpath -> not contained.
    """
    try:
        return os.path.commonpath([base_canon, candidate_canon]) == base_canon
    except ValueError:
        # Mixed drives / mixed abs+rel — not comparable, so not contained.
        return False


class MountJail:
    """Validates host-dir mounts against an allowlist. Deny-by-default, fail-closed."""

    def __init__(self, allowlist: List[str],
                 governance_roots: List[str] | None = None) -> None:
        # Canonicalise allowlist entries once. A non-absolute entry is dropped
        # (it cannot be a trustworthy root); deny-by-default means a smaller
        # allowlist is the safe failure direction.
        self._allow: List[str] = []
        for entry in allowlist or []:
            if not entry or not os.path.isabs(entry):
                continue
            self._allow.append(_canon(entry))
        # CONTAINMENT INVARIANT (R7): canonical governance roots no mount may overlap. Empty =>
        # dormant (back-compat: v1's in-cage kernel + group-mounted state). Supplied by the host
        # broker once governance moves off the container's filesystem (the migration).
        self._governance_roots: List[str] = [
            _canon(g) for g in (governance_roots or []) if g and os.path.isabs(g)
        ]

    def _overlaps_governance(self, candidate_canon: str) -> str | None:
        """The offending governance root if `candidate_canon` OVERLAPS one in either direction
        (it IS / is INSIDE a governance root, or it CONTAINS one), else None. Either direction
        leaks the substrate: mounting governance, a parent that holds it, or a child within it."""
        for gov in self._governance_roots:
            if _within(candidate_canon, gov) or _within(gov, candidate_canon):
                return gov
        return None

    @property
    def allowlist(self) -> List[str]:
        return list(self._allow)

    def is_allowed(self, host_path: str) -> bool:
        """True iff host_path canonicalises to within some allowlisted root (R2-R4).
        Empty allowlist -> always False (R1)."""
        if not host_path or not os.path.isabs(host_path):
            return False
        canon = _canon(host_path)
        return any(_within(canon, base) for base in self._allow)

    def validate_extra(self, host_path: str) -> str:
        """Return the canonical path for an allowed extra mount, or raise (R5).

        Refuses: non-absolute (R6), surface-level `..` traversal, and anything that
        canonicalises outside the allowlist (R2-R4, incl. symlink escapes via R3).
        """
        if not host_path or not os.path.isabs(host_path):
            raise MountDenied(f"mount source must be an absolute path: {host_path!r}")
        if ".." in Path(host_path).parts:
            # Defence in depth — realpath would resolve this, but reject the obvious
            # traversal attempt outright rather than relying solely on resolution.
            raise MountDenied(f"path traversal not permitted in mount source: {host_path!r}")
        canon = _canon(host_path)
        # R7 CONTAINMENT INVARIANT — absolute deny that OVERRIDES the allowlist: an allowlisted dir
        # that happens to expose a governance root is still a containment failure.
        gov = self._overlaps_governance(canon)
        if gov is not None:
            raise ContainmentError(
                f"CONTAINMENT FAILURE: mount source {host_path!r} overlaps the governance root "
                f"{gov!r} — governance code/state/keys/ledger must never be visible to the container"
            )
        if not self.is_allowed(host_path):
            raise MountDenied(
                f"mount source {host_path!r} is not within the mount allowlist "
                f"(deny-by-default). Allowed roots: {self._allow or '[]'}"
            )
        return canon

    def build_mounts(
        self,
        project_dir: str,
        group_dir: str,
        extras: Mapping[str, str] | None = None,
    ) -> List[Mount]:
        """Build the validated mount set for a cage run. FAIL CLOSED: if ANY extra
        is disallowed, raise — we never run with a partial/over-broad mount set.

        - project_dir -> /workspace/project (ro)   [structural, always present]
        - group_dir   -> /workspace/group   (rw)   [structural, per-group state]
        - extras{name: host_dir} -> /workspace/extra/{name} (ro), each allowlisted
        """
        mounts: List[Mount] = [
            Mount(_canon(project_dir), PROJECT_MOUNT, "ro"),
            Mount(_canon(group_dir), GROUP_MOUNT, "rw"),
        ]
        for name, host in (extras or {}).items():
            safe_name = os.path.basename(name.strip("/")) or name
            canon = self.validate_extra(host)  # raises MountDenied / ContainmentError
            mounts.append(Mount(canon, f"{EXTRA_MOUNT_BASE}/{safe_name}", "ro"))
        # R7 CONTAINMENT INVARIANT over the WHOLE set, structural mounts included: the group RW
        # mount must not expose a governance root (it does in v1, when governance state lives under
        # it — that overlap is the violation the host-side migration resolves; with governance_roots
        # supplied post-migration, this fails closed before such a cage can ever start).
        for m in mounts:
            gov = self._overlaps_governance(m.host)
            if gov is not None:
                raise ContainmentError(
                    f"CONTAINMENT FAILURE: mount {m.host!r} ({m.container}) exposes the governance "
                    f"root {gov!r} to the container — relocate governance off the container's "
                    f"filesystem (see the containment-invariant migration)"
                )
        return mounts

    @staticmethod
    def docker_args(mounts: List[Mount]) -> List[str]:
        args: List[str] = []
        for m in mounts:
            args += m.docker_arg()
        return args


def load_allowlist(path: str | None = None) -> List[str]:
    """Read the host-owned allowlist (a JSON array of absolute dir paths).

    DENY BY DEFAULT (R1): a missing or unreadable or malformed file yields an EMPTY
    allowlist — no extra host access — never an open one. Only reads; never writes.
    """
    p = path or DEFAULT_ALLOWLIST_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("allow") or data.get("paths") or []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if isinstance(x, str) and x]


def jail_from_file(path: str | None = None,
                   governance_roots: List[str] | None = None) -> MountJail:
    """Construct a MountJail from the host allowlist file (deny-by-default). `governance_roots`
    enables the R7 CONTAINMENT INVARIANT — supplied by the host broker once governance state lives
    off the container's filesystem (the migration); omitted in v1 (dormant), since today's cage
    runs the kernel in-container with state under the group mount."""
    return MountJail(load_allowlist(path), governance_roots=governance_roots)
