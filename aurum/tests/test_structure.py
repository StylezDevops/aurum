"""Structural smoke test — must pass GREEN on the bare scaffold.

Verifies every organ module imports, every organ class instantiates, and stub
methods raise the Unbuilt convention rather than something random. This is the
proof the scaffold itself is sound before any organ logic exists.
"""
from __future__ import annotations

import importlib

import pytest

from aurum.base import Unbuilt

# (module path, class name, organ id)
ORGANS = [
    ("aurum.spine.pk", "PolicyKernel", "PK"),
    ("aurum.spine.bb", "BlackBox", "BB"),
    ("aurum.spine.ts", "Toolsmith", "TS"),
    ("aurum.durability.el", "EvidenceLedger", "EL"),
    ("aurum.durability.rr", "ReproducibilityRunner", "RR"),
    ("aurum.durability.mgc", "MemoryGarbageCollector", "MGC"),
    ("aurum.durability.kve", "KnowledgeValidityEngine", "KVE"),
    ("aurum.durability.gr", "GoalRegistry", "GR"),
    ("aurum.durability.pm", "PreferenceModel", "PM"),
    ("aurum.durability.tcm", "ToolCatalogManager", "TCM"),
    ("aurum.novel.aa", "APIArchaeologist", "AA"),
    ("aurum.novel.ls", "LivingSpecification", "LS"),
    ("aurum.novel.hvp", "HeterogeneousVerifierPanel", "HVP"),
    ("aurum.novel.eg", "EpistemicGovernor", "EG"),
    ("aurum.novel.cs", "CausalSimulator", "CS"),
    ("aurum.novel.ag", "AuthorityGovernor", "AG"),
    ("aurum.novel.oi", "OutcomeInterpreter", "OI"),
    ("aurum.extensions.sdg", "SkillDependencyGraph", "SDG"),
    ("aurum.extensions.sm", "SubstrateMapper", "SM"),
    ("aurum.support.tl", "TrustLadder", "TL"),
    ("aurum.support.cb", "CircuitBreaker", "CB"),
    ("aurum.support.sh", "ShadowMode", "SH"),
    ("aurum.support.cg", "CostGovernor", "CG"),
    ("aurum.support.rs", "ResourceScheduler", "RS"),
    ("aurum.support.sen", "Sensorium", "SEN"),
    ("aurum.observability.idm", "IdentityDriftMonitor", "IDM"),
    ("aurum.observability.mpd", "MemoryPoisoningDetector", "MPD"),
    ("aurum.observability.cc", "ConcentrationCheck", "CC"),
    ("aurum.deferred.ao", "AgentOrchestrator", "AO"),
]


def test_all_29_modules_present():
    # 28 active organs + AO (deferred) = 29 classes.
    assert len(ORGANS) == 29


@pytest.mark.parametrize("modpath,clsname,organ", ORGANS)
def test_organ_imports_and_instantiates(modpath, clsname, organ):
    mod = importlib.import_module(modpath)
    cls = getattr(mod, clsname)
    inst = cls()
    assert getattr(cls, "ORGAN", None) == organ


@pytest.mark.parametrize("modpath,clsname,organ", ORGANS)
def test_stub_methods_raise_unbuilt(modpath, clsname, organ):
    """A representative public method on each STUB raises Unbuilt. Organs flipped
    to built in build_state are skipped — they have real bodies now."""
    from aurum.build_state import BUILT
    if BUILT.get(organ, False):
        pytest.skip(f"{organ} is built — no longer a stub")
    mod = importlib.import_module(modpath)
    cls = getattr(mod, clsname)
    inst = cls()
    # AO is the deferred Agent Orchestrator: a methodless documented boundary whose
    # __getattr__ raises Unbuilt on ANY attribute access (verify_no_pytest.py special-
    # cases it the same way). Assert that contract directly instead of requiring an
    # enumerable named method — keyed off the DEFERRED marker so any future deferred
    # boundary inherits the same handling.
    if getattr(cls, "DEFERRED", False):
        with pytest.raises(Unbuilt):  # module-level import (line 13); no local shadow
            inst.any_deferred_call()
        return
    # find first public callable that isn't a property
    methods = [
        n for n in dir(inst)
        if not n.startswith("_") and callable(getattr(type(inst), n, None))
    ]
    assert methods, f"{organ} exposes no public methods"
    # call one with no args where possible; tolerate TypeError (needs args) but
    # the moment it executes the body it must be Unbuilt.
    probed = False
    for name in methods:
        fn = getattr(inst, name)
        try:
            fn()
        except Unbuilt:
            probed = True
            break
        except TypeError:
            # needs arguments; try with a spray of dummies
            try:
                fn(None)
            except Unbuilt:
                probed = True
                break
            except TypeError:
                try:
                    fn(None, None)
                except Unbuilt:
                    probed = True
                    break
                except TypeError:
                    continue
    assert probed, f"{organ}: no probed method raised Unbuilt"
