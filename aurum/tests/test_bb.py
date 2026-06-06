"""Tests for BB — Black Box."""
from __future__ import annotations

import json

import pytest

from aurum.build_state import is_built
from aurum.spine.bb import BlackBox, _sign

pytestmark = pytest.mark.skipif(
    not is_built("BB"), reason="BB not built yet"
)


def _bb(**kwargs):
    return BlackBox(**kwargs)


# ---------------------------------------------------------------------------
# write / search
# ---------------------------------------------------------------------------

def test_write_returns_id():
    bb = _bb()
    pm_id = bb.write({"title": "test failure", "summary": "it broke"})
    assert isinstance(pm_id, str)
    assert len(pm_id) > 0


def test_write_assigns_id_if_missing():
    bb = _bb()
    pm_id = bb.write({"summary": "no id supplied"})
    assert pm_id
    assert pm_id in bb.all_ids()


def test_write_preserves_supplied_id():
    bb = _bb()
    pm_id = bb.write({"id": "fixed-id-001", "summary": "explicit id"})
    assert pm_id == "fixed-id-001"


def test_search_finds_entry():
    bb = _bb()
    bb.write({"title": "import failure", "summary": "ModuleNotFoundError on hugging_face"})
    bb.write({"title": "unrelated", "summary": "something else"})
    results = bb.search("ModuleNotFoundError")
    assert len(results) == 1
    assert results[0]["title"] == "import failure"


def test_search_case_insensitive():
    bb = _bb()
    bb.write({"summary": "APIKEY leak detected"})
    assert bb.search("apikey")


def test_search_returns_empty_when_no_match():
    bb = _bb()
    bb.write({"summary": "something benign"})
    assert bb.search("zzznomatch") == []


def test_write_rejects_duplicate_id():
    """BB is append-only — rewriting an existing id must raise, not silently replace."""
    bb = _bb()
    bb.write({"id": "pm1", "summary": "original"})
    with pytest.raises(ValueError, match="already exists"):
        bb.write({"id": "pm1", "summary": "attacker-replaced"})


def test_search_excludes_quarantined():
    bb = _bb()
    pm_id = bb.write({"id": "q1", "summary": "tampered postmortem"})
    # Manually corrupt the signature to simulate out-of-band tampering
    bb._db.execute(
        "UPDATE postmortems SET signature='deadbeef' WHERE id=?", (pm_id,)
    )
    bb._db.commit()
    bb.verify_on_load()
    results = bb.search("tampered")
    assert results == []


# ---------------------------------------------------------------------------
# verify_on_load — signature check
# ---------------------------------------------------------------------------

def test_verify_on_load_all_trusted():
    bb = _bb()
    bb.write({"id": "p1", "summary": "ok"})
    bb.write({"id": "p2", "summary": "also ok"})
    result = bb.verify_on_load()
    assert set(result["trusted"]) == {"p1", "p2"}
    assert result["quarantined"] == []


def test_verify_on_load_quarantines_tampered():
    bb = _bb()
    bb.write({"id": "good", "summary": "trusted"})
    bb.write({"id": "bad", "summary": "original"})
    # Corrupt bad entry's signature
    bb._db.execute(
        "UPDATE postmortems SET signature='not-a-valid-hmac' WHERE id='bad'"
    )
    bb._db.commit()
    result = bb.verify_on_load()
    assert "good" in result["trusted"]
    assert "bad" in result["quarantined"]


def test_verify_on_load_quarantined_hidden_from_search():
    bb = _bb()
    pm_id = bb.write({"id": "tampered", "summary": "find me"})
    bb._db.execute(
        "UPDATE postmortems SET signature='corrupt' WHERE id=?", (pm_id,)
    )
    bb._db.commit()
    bb.verify_on_load()
    assert bb.search("find me") == []


# ---------------------------------------------------------------------------
# PK.redact integration
# ---------------------------------------------------------------------------

def test_write_redacts_via_pk():
    from aurum.spine.pk import PolicyKernel
    pk = PolicyKernel()
    bb = _bb(pk=pk)
    pm_id = bb.write({"summary": "leak", "token": "super-secret-123"})
    # Read back the stored body and confirm token was redacted
    row = bb._db.execute(
        "SELECT body_json FROM postmortems WHERE id=?", (pm_id,)
    ).fetchone()
    body = json.loads(row["body_json"])
    assert body["token"] == "[REDACTED]"
    assert body["summary"] == "leak"


def test_write_without_pk_preserves_fields():
    bb = _bb()
    pm_id = bb.write({"summary": "no pk", "token": "should-stay"})
    row = bb._db.execute(
        "SELECT body_json FROM postmortems WHERE id=?", (pm_id,)
    ).fetchone()
    body = json.loads(row["body_json"])
    assert body["token"] == "should-stay"


# ---------------------------------------------------------------------------
# all_ids
# ---------------------------------------------------------------------------

def test_all_ids():
    bb = _bb()
    bb.write({"id": "x1"})
    bb.write({"id": "x2"})
    assert set(bb.all_ids()) == {"x1", "x2"}
