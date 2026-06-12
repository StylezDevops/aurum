"""IdentityScopeMapper — resolve an AG authority band to a LEAST-PRIVILEGE access scope.

The bridge between governance authority and real cloud access (Azure managed-identity / RBAC
shaped, but provider-agnostic): a capability class at a given authority band resolves to the
NARROWEST role + resource scope that band justifies — never standing god-access. Three
non-negotiables, asserted by construction AND test:

  • MONOTONIC LEAST PRIVILEGE: advisory → no access; readonly → read only; code → read+write but
    NOT delete (irreversible); full → +delete. Access only ever GROWS with the band, never skips.
  • IRREVERSIBLE ONLY AT THE TOP: a delete/destroy control-plane action is permitted ONLY at the
    full band — the same asymmetry the gate enforces, projected onto RBAC notActions below it.
  • A HARD CEILING the agent can never cross: it is NEVER granted a privilege-escalation role
    (Owner / User Access Administrator — the right to grant itself more) and never a
    subscription-wide / root scope, at ANY band. The agent gets task access, not the keys.

It mints NO credentials and holds NO secret: it returns a SCOPE REQUEST (identity principal +
role + resource scope + ttl) that OneCLI / the managed-identity provider turns into a short-lived
(JIT) token at request time — so `docker inspect` and the ledger stay clean. Fail-safe: an unknown
band or an unbound capability class resolves to NO ACCESS (the narrowest possible), never broad.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from ..types import AuthorityBand

# Band → least-privilege role TIER. None = no role (no access). The SAME role at code/full — the
# resource SCOPE + notActions are what bound it, not an ever-bigger role.
_BAND_ROLE: Dict[str, Optional[str]] = {
    "advisory": None,         # advise-only — no role assignment at all
    "readonly": "Reader",     # read the scoped resource, no mutation
    "code": "Contributor",    # mutate the scoped resource, but NOT delete (notActions)
    "full": "Contributor",    # +delete, still scoped — never Owner
}
_BAND_RANK = {"advisory": 0, "readonly": 1, "code": 2, "full": 3}

# Roles the agent may NEVER hold at any band — they confer the power to escalate its own access
# or unbounded blast radius. Enforced as a ceiling on every grant.
_FORBIDDEN_ROLES = frozenset({"owner", "user access administrator", "co-administrator"})

_DEFAULT_TTL = 900  # seconds — JIT/short-lived; re-request as authority changes


class ScopeGrant(TypedDict):
    capability_class: str
    band: str
    identity: str               # the managed-identity principal (a NAME, never a secret)
    role: Optional[str]         # least-privilege role; None = no access
    scope: Optional[str]        # narrowest resource scope; None = no access
    actions: List[str]          # permitted control-plane actions
    not_actions: List[str]      # explicit denials (e.g. */delete below the full band)
    ttl_seconds: int            # short-lived
    granted: bool               # False = no access (advisory / unbound / unknown band)


def _scope_is_bounded(scope: Optional[str]) -> bool:
    """A scope is BOUNDED iff it names a resource below the subscription — never root '/' nor a
    bare '/subscriptions/<id>' (subscription-wide). Fail-safe: an empty/None scope is unbounded
    (→ no access). Azure-shaped but only structural: it never trusts a scope it can't see narrow."""
    if not scope:
        return False
    s = scope.rstrip("/")
    if s in ("", "/"):
        return False
    parts = [p for p in s.split("/") if p]
    # /subscriptions/<id>  →  2 parts, subscription-wide → NOT bounded.
    if len(parts) <= 2 and parts[:1] == ["subscriptions"]:
        return False
    return True


class IdentityScopeMapper:
    """Maps (capability_class, authority band) → a least-privilege ScopeGrant. `bindings` binds a
    capability class to its resource scope: {capability_class: {"scope": "<resource scope>"}}.
    Stateless and pure; mints nothing. An unbound class or unknown band → no access (fail-safe)."""

    def __init__(self, bindings: Optional[Dict[str, Dict[str, Any]]] = None, *,
                 identity: str = "aurum-managed-identity", ttl_seconds: int = _DEFAULT_TTL) -> None:
        self._bindings = dict(bindings or {})
        self.identity = identity
        self.ttl_seconds = int(ttl_seconds)

    def scope_for(self, capability_class: str, band: Any, *,
                  resource: Optional[str] = None) -> ScopeGrant:
        """The least-privilege grant for a class at a band. `resource` overrides the bound scope
        (e.g. a specific record). Unknown band → advisory (no access). Unbound class with no scope
        → no access. A delete action is included ONLY at the full band."""
        role = _BAND_ROLE.get(str(band))                 # unknown band → None (advisory floor)
        scope = resource or (self._bindings.get(capability_class, {}) or {}).get("scope")
        rank = _BAND_RANK.get(str(band), 0)
        actions: List[str] = []
        not_actions: List[str] = []
        if rank >= _BAND_RANK["readonly"]:
            actions.append("*/read")
        if rank >= _BAND_RANK["code"]:
            actions.append("*/write")
        else:
            not_actions.append("*/write")
        if rank >= _BAND_RANK["full"]:
            actions.append("*/delete")                   # irreversible — full band only
        else:
            not_actions.append("*/delete")
        granted = bool(role) and _scope_is_bounded(scope)
        grant: ScopeGrant = {
            "capability_class": capability_class, "band": str(band),
            "identity": self.identity,
            "role": role if granted else None,
            "scope": scope if granted else None,
            "actions": actions if granted else [],
            "not_actions": not_actions if granted else [],
            "ttl_seconds": self.ttl_seconds, "granted": granted,
        }
        # Defence in depth: a grant must NEVER exceed the ceiling, whatever the config said.
        if not self.is_within_ceiling(grant):
            return {**grant, "role": None, "scope": None, "actions": [],
                    "not_actions": [], "granted": False}
        return grant

    @staticmethod
    def is_within_ceiling(grant: ScopeGrant) -> bool:
        """A grant is within the hard ceiling iff: its role is not a privilege-escalation role; it
        permits delete ONLY at the full band; and any granted scope is bounded below a subscription.
        A not-granted (no-access) grant is trivially within the ceiling."""
        if not grant["granted"]:
            return True
        if grant["role"] and grant["role"].strip().lower() in _FORBIDDEN_ROLES:
            return False
        if "*/delete" in grant["actions"] and grant["band"] != "full":
            return False
        return _scope_is_bounded(grant["scope"])

    def ladder(self, capability_class: str, *,
               resource: Optional[str] = None) -> Dict[str, ScopeGrant]:
        """The grant at EACH band for a class — for inspection/tests (shows the monotonic ladder).
        Keyed by band name."""
        return {b: self.scope_for(capability_class, b, resource=resource)
                for b in ("advisory", "readonly", "code", "full")}
