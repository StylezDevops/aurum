"""IdentityScopeMapper — AG authority band → LEAST-PRIVILEGE managed-identity / RBAC scope.

A class's authority band resolves to the narrowest role+scope it justifies (never standing
god-access); access grows monotonically with the band; delete is permitted only at the full
band; and a hard ceiling forbids privilege-escalation roles and subscription-wide scopes at
ANY band. The kernel resolves a class's LIVE band, so demoting authority tightens the grant.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.integrations.identity import IdentityScopeMapper
from aurum.kernel import GovernanceKernel

# An Azure-shaped, resource-scoped binding (below the subscription → bounded).
_SCOPE = ("/subscriptions/SUB/resourceGroups/rg-aurum/providers/"
          "Microsoft.Storage/storageAccounts/aurumdata")


def _mapper():
    return IdentityScopeMapper(bindings={"file_write": {"scope": _SCOPE}})


# ── monotonic least privilege ──────────────────────────────────────────────────
def test_band_ladder_is_monotonic_least_privilege():
    ladder = _mapper().ladder("file_write")
    assert ladder["advisory"]["granted"] is False                 # advise-only → no access
    assert ladder["readonly"]["actions"] == ["*/read"]
    assert ladder["code"]["actions"] == ["*/read", "*/write"]      # mutate, but NOT delete
    assert "*/delete" in ladder["full"]["actions"]                 # +delete only at the top
    # access only ever grows; delete is full-band only
    assert "*/delete" not in ladder["code"]["actions"]
    assert "*/delete" in ladder["code"]["not_actions"]
    assert ladder["readonly"]["role"] == "Reader"
    assert ladder["code"]["role"] == "Contributor" == ladder["full"]["role"]  # scope bounds it, not role


def test_unbound_class_is_no_access_even_at_full():
    grant = _mapper().scope_for("unbound_class", "full")
    assert grant["granted"] is False and grant["role"] is None and grant["scope"] is None


def test_unknown_band_falls_to_no_access():
    grant = _mapper().scope_for("file_write", "superuser")        # not a real band
    assert grant["granted"] is False


# ── the hard ceiling ────────────────────────────────────────────────────────────
def test_subscription_wide_scope_is_refused():
    m = IdentityScopeMapper(bindings={"x": {"scope": "/subscriptions/SUB"}})  # subscription-wide
    assert m.scope_for("x", "full")["granted"] is False           # ceiling: not bounded below a sub
    assert IdentityScopeMapper(bindings={"x": {"scope": "/"}}).scope_for("x", "full")["granted"] is False


def test_resource_override_must_also_be_bounded():
    m = _mapper()
    ok = m.scope_for("file_write", "code", resource=_SCOPE + "/blobServices/default")
    assert ok["granted"] is True and ok["scope"].endswith("/default")


def test_is_within_ceiling_rejects_escalation_role_and_low_band_delete():
    base = {"capability_class": "x", "band": "full", "identity": "i", "role": "Owner",
            "scope": _SCOPE, "actions": ["*/read"], "not_actions": [], "ttl_seconds": 1,
            "granted": True}
    assert IdentityScopeMapper.is_within_ceiling(base) is False                     # Owner forbidden
    delete_low = {**base, "role": "Contributor", "band": "code", "actions": ["*/delete"]}
    assert IdentityScopeMapper.is_within_ceiling(delete_low) is False               # delete below full
    no_access = {**base, "granted": False, "role": None, "scope": None}
    assert IdentityScopeMapper.is_within_ceiling(no_access) is True                 # no access is fine


# ── kernel: the grant tracks LIVE authority (JIT, never standing) ────────────────
@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_kernel_scope_for_tightens_as_authority_is_demoted(tmp_path):
    k = GovernanceKernel(home=str(tmp_path),
                         identity_bindings={"file_write": {"scope": _SCOPE}})
    k.ag.set_authority("file_write", 0.96)                         # full band
    full = k.scope_for("file_write")
    assert full["band"] == "full" and "*/delete" in full["actions"]

    k.ag.set_authority("file_write", 0.55)                        # demote → readonly band
    tight = k.scope_for("file_write")
    assert tight["band"] == "readonly"
    assert tight["actions"] == ["*/read"]                         # lost write + delete (JIT tighten)


@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_kernel_scope_for_unbound_is_no_access(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))                      # no identity_bindings
    assert k.scope_for("file_write")["granted"] is False          # least privilege: no standing access
