"""Mount jail — the cage's host-fs containment boundary. Tests each requirement.

These exercise the LOGIC of the jail (path validation), which is the security
property; they need no docker. The live "agent can't escape its mounts" round-trip
is a separate, human-gated verification with a running container.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

from aurum.cage.mount_jail import (
    MountJail,
    MountDenied,
    load_allowlist,
    PROJECT_MOUNT,
    GROUP_MOUNT,
    EXTRA_MOUNT_BASE,
)


def _mkdir():
    return os.path.realpath(tempfile.mkdtemp())


def test_R1_deny_by_default_empty_allowlist():
    d = _mkdir()
    assert MountJail([]).is_allowed(d) is False
    assert MountJail([]).allowlist == []


def test_R2_explicit_grant_inside_and_outside():
    allow, outside = _mkdir(), _mkdir()
    jail = MountJail([allow])
    inside = os.path.join(allow, "a", "b")
    os.makedirs(inside)
    assert jail.is_allowed(allow) is True
    assert jail.is_allowed(inside) is True
    assert jail.is_allowed(outside) is False


def test_R3_symlink_escape_is_refused():
    if sys.platform.startswith("win"):
        pytest.skip("symlink creation needs privilege on Windows")
    allow, secret = _mkdir(), _mkdir()
    jail = MountJail([allow])
    link = os.path.join(allow, "escape")
    os.symlink(secret, link)  # symlink inside allow -> outside
    # Surface path is under allow, but it RESOLVES outside -> denied (R3).
    assert jail.is_allowed(link) is False
    with pytest.raises(MountDenied):
        jail.validate_extra(link)


def test_R4_string_prefix_sibling_not_contained():
    allow = _mkdir()
    jail = MountJail([allow])
    # /tmp/xxx vs /tmp/xxx-evil — prefix match would wrongly allow this.
    assert jail.is_allowed(allow + "-evil") is False


def test_R5_build_mounts_fails_closed_on_bad_extra():
    allow, outside = _mkdir(), _mkdir()
    jail = MountJail([allow])
    with pytest.raises(MountDenied):
        jail.build_mounts(allow, allow, {"data": outside})


def test_R6_relative_source_refused():
    jail = MountJail([_mkdir()])
    assert jail.is_allowed("relative/dir") is False
    with pytest.raises(MountDenied):
        jail.validate_extra("relative/dir")


def test_traversal_in_source_refused():
    allow = _mkdir()
    jail = MountJail([allow])
    with pytest.raises(MountDenied):
        jail.validate_extra(os.path.join(allow, "..", "elsewhere"))


def test_structural_mounts_and_docker_args():
    allow = _mkdir()
    proj, group, extra = _mkdir(), _mkdir(), os.path.join(allow, "shared")
    os.makedirs(extra)
    jail = MountJail([allow])
    mounts = jail.build_mounts(proj, group, {"shared": extra})
    # project RO, group RW are structural and always present.
    assert mounts[0].container == PROJECT_MOUNT and mounts[0].mode == "ro"
    assert mounts[1].container == GROUP_MOUNT and mounts[1].mode == "rw"
    # the allowed extra is jailed under /workspace/extra/<name>, read-only.
    assert mounts[2].container == f"{EXTRA_MOUNT_BASE}/shared" and mounts[2].mode == "ro"
    args = MountJail.docker_args(mounts)
    assert args.count("-v") == 3
    assert any(a.endswith(":/workspace/project:ro") for a in args)
    assert any(a.endswith(":/workspace/group:rw") for a in args)


def test_load_allowlist_deny_by_default_on_missing_or_bad_file(tmp_path):
    # Missing file -> empty (deny by default).
    assert load_allowlist(str(tmp_path / "nope.json")) == []
    # Malformed JSON -> empty, never an open allowlist.
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert load_allowlist(str(bad)) == []
    # Valid array form.
    good = tmp_path / "good.json"
    good.write_text('["/srv/a", "/srv/b", 5, ""]', encoding="utf-8")
    assert load_allowlist(str(good)) == ["/srv/a", "/srv/b"]
    # Object form with "allow" key.
    obj = tmp_path / "obj.json"
    obj.write_text('{"allow": ["/srv/c"]}', encoding="utf-8")
    assert load_allowlist(str(obj)) == ["/srv/c"]
