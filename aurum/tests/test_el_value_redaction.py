"""EL value-redaction, fully closed: secrets in NORMAL-named string fields are scrubbed (email,
key=value, bare opaque tokens) WITHOUT redacting id/hash-shaped runs (uuids, digests) — so the
leak is closed without the uuid regression that would break replay/lineage join keys.
"""
# Author: Daniel Styles <me0wc0w73@gmail.com>
from __future__ import annotations

import pytest

from aurum.build_state import is_built
from aurum.durability.redaction import LinearRedactor
from aurum.kernel import GovernanceKernel
from aurum.spine.policy_kernel import PolicyKernel

_TOKEN = "ghp_AbC123dEf456GhI789jkl012MnO345pqRsT"   # 40-char mixed-case token (a real secret shape)
_UUID = "a825cfb3bf0b72eb6c4d5e6f70819a2b"           # uuid4().hex — 32 lowercase hex (an id)
_CANON = "a825cfb3-bf0b-72eb-6c4d-5e6f70819a2b"      # canonical uuid — hex + dashes
_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # sha256 hex (a hash)


# ── LinearRedactor: bare tokens redacted, id/hash shapes preserved ─────────────
def test_bare_opaque_token_is_redacted():
    assert LinearRedactor().redact(f"deploy with {_TOKEN} now") .count("[REDACTED]") == 1
    assert _TOKEN not in LinearRedactor().redact(f"deploy with {_TOKEN} now")


def test_id_and_hash_shapes_are_preserved():
    r = LinearRedactor()
    assert _UUID in r.redact(f"see action {_UUID}")           # uuid4 hex — exempt
    assert _CANON in r.redact(f"decision {_CANON} fired")     # canonical uuid — exempt
    assert _SHA in r.redact(f"prev_hash {_SHA}")              # sha256 digest — exempt


def test_email_and_kv_still_redacted():
    r = LinearRedactor()
    assert "x@y.com" not in r.redact("mail x@y.com please")
    assert "Sekret" not in r.redact("token=SekretValue0123")


# ── PK.redact: key-name + value-scan compose; uuids in normal keys survive ─────
def test_pk_redacts_secret_in_normal_field_keeps_uuid():
    pk = PolicyKernel()
    red = pk.redact({"content": f"deploy key {_TOKEN}", "decision_id": _UUID,
                     "password": "hunter2", "note": "contact x@y.com"})
    assert _TOKEN not in str(red["content"])          # bare token in a NORMAL field → scrubbed
    assert red["decision_id"] == _UUID                # uuid join-key → preserved
    assert red["password"] == "[REDACTED]"            # sensitive key-name → wholesale
    assert "x@y.com" not in str(red["note"])          # email in a normal field → scrubbed


# ── end-to-end: replay join survives redaction; a content secret is scrubbed ───
@pytest.mark.skipif(not is_built("PK", "EL", "AG", "CA", "BB"),
                    reason="governance organs not all built")
def test_kernel_redaction_preserves_replay_join_and_scrubs_content(tmp_path):
    k = GovernanceKernel(home=str(tmp_path))
    k.record_outcome_verdict("t1", "file_write", satisfied=True)   # logs decision_id (uuid) join key
    chain = k.why_authority_chain("file_write")
    assert chain and chain[-1]["outcome_event"] is not None        # the decision_id join SURVIVED redaction
    assert k.el.verify_chain() is True

    k.el.append({"event_id": "", "timestamp": "", "source_organ": "GOV", "action_type": "VOTE",
                 "object_ids": ["x"], "payload": {"content": f"leaked {_TOKEN}"},
                 "evidence_confidence": 1.0, "evidence_source": "t", "prev_hash": "", "hash": ""})
    voted = k.el.query({"source_organ": "GOV", "action_type": "VOTE"})
    assert voted and all(_TOKEN not in str(r["payload"]) for r in voted)   # token scrubbed in the durable record
    assert k.el.verify_chain() is True                              # redaction is consistent with the hash
