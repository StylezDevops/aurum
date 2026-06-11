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
    ("aurum.spine.policy_kernel", "PolicyKernel", "PK"),
    ("aurum.spine.black_box", "BlackBox", "BB"),
    ("aurum.spine.toolsmith", "Toolsmith", "TS"),
    ("aurum.durability.evidence_ledger", "EvidenceLedger", "EL"),
    ("aurum.durability.reproducibility_runner", "ReproducibilityRunner", "RR"),
    ("aurum.durability.memory_garbage_collector", "MemoryGarbageCollector", "MGC"),
    ("aurum.durability.knowledge_validity_engine", "KnowledgeValidityEngine", "KVE"),
    ("aurum.durability.goal_registry", "GoalRegistry", "GR"),
    ("aurum.durability.preference_model", "PreferenceModel", "PM"),
    ("aurum.durability.tool_catalog_manager", "ToolCatalogManager", "TCM"),
    ("aurum.novel.api_archaeologist", "APIArchaeologist", "AA"),
    ("aurum.novel.living_specification", "LivingSpecification", "LS"),
    ("aurum.novel.heterogeneous_verifier_panel", "HeterogeneousVerifierPanel", "HVP"),
    ("aurum.novel.epistemic_governor", "EpistemicGovernor", "EG"),
    ("aurum.novel.causal_simulator", "CausalSimulator", "CS"),
    ("aurum.novel.authority_governor", "AuthorityGovernor", "AG"),
    ("aurum.novel.outcome_interpreter", "OutcomeInterpreter", "OI"),
    ("aurum.extensions.skill_dependency_graph", "SkillDependencyGraph", "SDG"),
    ("aurum.extensions.substrate_mapper", "SubstrateMapper", "SM"),
    ("aurum.support.trust_ladder", "TrustLadder", "TL"),
    ("aurum.support.circuit_breaker", "CircuitBreaker", "CB"),
    ("aurum.support.shadow_mode", "ShadowMode", "SH"),
    ("aurum.support.cost_governor", "CostGovernor", "CG"),
    ("aurum.support.resource_scheduler", "ResourceScheduler", "RS"),
    ("aurum.support.sensorium", "Sensorium", "SEN"),
    ("aurum.observability.identity_drift_monitor", "IdentityDriftMonitor", "IDM"),
    ("aurum.observability.memory_poisoning_detector", "MemoryPoisoningDetector", "MPD"),
    ("aurum.observability.concentration_check", "ConcentrationCheck", "CC"),
    ("aurum.deferred.agent_orchestrator", "AgentOrchestrator", "AO"),
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
