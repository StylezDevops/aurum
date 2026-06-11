"""PM — Preference Model. Storage, scope matching, check, stated-outranks-inferred."""
from __future__ import annotations

import os
import tempfile

from aurum.durability.evidence_ledger import EvidenceLedger
from aurum.durability.preference_model import PreferenceModel


def _pm(**kw):
    return PreferenceModel(os.path.join(tempfile.mkdtemp(), "pm.db"), **kw)


# -- accept (a): preferences live in PM ------------------------------------
def test_add_and_get():
    pm = _pm()
    pm.add({"statement": "use PowerShell", "requires": "powershell", "key": "shell"},
           provenance="stated")
    prefs = pm.get()
    assert len(prefs) == 1 and prefs[0]["statement"] == "use PowerShell"
    assert prefs[0]["status"] == "active"  # stated -> active


def test_inferred_is_proposed_until_confirmed():
    pm = _pm()
    pm.add({"statement": "maybe concise"}, provenance="inferred")
    assert pm.get()[0]["status"] == "proposed"


# -- scope matching --------------------------------------------------------
def test_applies_by_scope():
    pm = _pm()
    pm.add({"statement": "global g", "scope": "global"}, "stated")
    pm.add({"statement": "crm only", "scope": "domain", "domain": "crm"}, "stated")
    pm.add({"statement": "email only", "scope": "task_type", "task_type": "email"},
           "stated")
    got = {p["statement"] for p in pm.applies({"domain": "crm"})}
    assert got == {"global g", "crm only"}  # global + matching domain; not the task one


# -- check: respected / violated -------------------------------------------
def test_check_respected_and_violated():
    pm = _pm()
    pm.add({"statement": "no em dashes", "forbids": "—", "key": "dash"}, "stated")
    pm.add({"statement": "API-first", "requires": "api", "key": "api"}, "stated")
    res = pm.check("we used the api cleanly")
    assert set(res["respected"]) == set(p["pref_id"] for p in pm.get())
    assert res["violated"] == []
    bad = pm.check("we wrote a long — dash and no integration")
    assert len(bad["violated"]) == 2


# -- accept (d): stated overrides conflicting inferred ---------------------
def test_stated_outranks_inferred_on_same_key():
    pm = _pm()
    pm.add({"statement": "use powershell", "requires": "powershell", "key": "shell"},
           "stated")
    pm.add({"statement": "use bash", "requires": "bash", "key": "shell"}, "inferred")
    res = pm.check("the script is powershell")
    # the stated pref is respected; the inferred (conflicting on key 'shell') is suppressed
    stated_id = next(p["pref_id"] for p in pm.get() if p["provenance"] == "stated")
    inferred_id = next(p["pref_id"] for p in pm.get() if p["provenance"] == "inferred")
    assert stated_id in res["respected"]
    assert inferred_id not in res["violated"] and inferred_id not in res["respected"]


def test_add_logged_to_el():
    el = EvidenceLedger(os.path.join(tempfile.mkdtemp(), "el.db"))
    pm = _pm(el=el)
    pm.add({"statement": "x"}, "stated")
    rows = el.query({"source_organ": "PM"})
    assert len(rows) == 1 and rows[0]["action_type"] == "PROPOSAL"
