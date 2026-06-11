import importlib, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aurum.base import Unbuilt
from aurum.build_state import BUILT, is_built

ORGANS = [
    ("aurum.spine.policy_kernel","PolicyKernel","PK"),("aurum.spine.black_box","BlackBox","BB"),
    ("aurum.spine.toolsmith","Toolsmith","TS"),("aurum.durability.evidence_ledger","EvidenceLedger","EL"),
    ("aurum.durability.reproducibility_runner","ReproducibilityRunner","RR"),
    ("aurum.durability.memory_garbage_collector","MemoryGarbageCollector","MGC"),
    ("aurum.durability.knowledge_validity_engine","KnowledgeValidityEngine","KVE"),
    ("aurum.durability.goal_registry","GoalRegistry","GR"),("aurum.durability.preference_model","PreferenceModel","PM"),
    ("aurum.durability.tool_catalog_manager","ToolCatalogManager","TCM"),
    ("aurum.novel.api_archaeologist","APIArchaeologist","AA"),("aurum.novel.living_specification","LivingSpecification","LS"),
    ("aurum.novel.heterogeneous_verifier_panel","HeterogeneousVerifierPanel","HVP"),("aurum.novel.epistemic_governor","EpistemicGovernor","EG"),
    ("aurum.novel.causal_simulator","CausalSimulator","CS"),("aurum.novel.authority_governor","AuthorityGovernor","AG"),
    ("aurum.novel.outcome_interpreter","OutcomeInterpreter","OI"),
    ("aurum.extensions.skill_dependency_graph","SkillDependencyGraph","SDG"),("aurum.extensions.substrate_mapper","SubstrateMapper","SM"),
    ("aurum.support.trust_ladder","TrustLadder","TL"),("aurum.support.circuit_breaker","CircuitBreaker","CB"),
    ("aurum.support.shadow_mode","ShadowMode","SH"),("aurum.support.cost_governor","CostGovernor","CG"),
    ("aurum.support.resource_scheduler","ResourceScheduler","RS"),("aurum.support.sensorium","Sensorium","SEN"),
    ("aurum.observability.identity_drift_monitor","IdentityDriftMonitor","IDM"),
    ("aurum.observability.memory_poisoning_detector","MemoryPoisoningDetector","MPD"),
    ("aurum.observability.concentration_check","ConcentrationCheck","CC"),
    ("aurum.deferred.agent_orchestrator","AgentOrchestrator","AO"),
]

fails=[]
for modpath,cls,organ in ORGANS:
    try:
        m=importlib.import_module(modpath); c=getattr(m,cls); inst=c()
        assert getattr(c,"ORGAN",None)==organ, f"{organ} ORGAN attr mismatch"
    except Exception as e:
        fails.append(f"IMPORT/INST {organ}: {e!r}")
print(f"organs imported+instantiated: {len(ORGANS)-len([f for f in fails if f.startswith('IMPORT')])}/{len(ORGANS)}")

# probe one method per organ raises Unbuilt (AO via __getattr__)
probe_fail=[]
for modpath,cls,organ in ORGANS:
    if BUILT.get(organ, False):
        continue  # built: real body, not a stub
    m=importlib.import_module(modpath); inst=getattr(m,cls)()
    names=[n for n in dir(inst) if not n.startswith("_") and callable(getattr(type(inst),n,None))]
    ok=False
    if organ=="AO":
        try: inst.anything()
        except Unbuilt: ok=True
    for n in names:
        if ok: break
        f=getattr(inst,n)
        for args in [(),(None,),(None,None),(None,None,None)]:
            try:
                f(*args)
            except Unbuilt:
                ok=True; break
            except TypeError:
                continue
            except Exception:
                break
    if not ok: probe_fail.append(organ)
built=[o for o in BUILT if BUILT[o]]
stub_count=len(ORGANS)-len(built)
print(f"built organs (skipped stub-probe): {sorted(built)}")
print(f"stub organs whose stub raises Unbuilt: {stub_count-len(probe_fail)}/{stub_count}", "FAILS:"+",".join(probe_fail) if probe_fail else "")

# harness logic: with nothing built, all 12 gates must be skip (not pass, not fail)
from aurum.gates import GATES
live=[g for g in GATES if is_built(*GATES[g][0])]
print(f"assertion gates LIVE at current build state: {live}")
print(f"assertion gates total: {len(GATES)} (expected 12)")

# simulate flipping EL+CB built -> ERR_001 goes live
BUILT["EL"]=True; BUILT["CB"]=True
live2=[g for g in GATES if is_built(*GATES[g][0])]
print(f"after EL+CB built, LIVE gates: {live2} (expected AURUM_ERR_001 + _011)")
BUILT["EL"]=False; BUILT["CB"]=False

print("types schema check:", end=" ")
from aurum.types import calculate_block_hash
ev={"event_id":"e1","timestamp":"t","source_organ":"EL","action_type":"VOTE","object_ids":[],"payload":{},"evidence_confidence":1.0,"evidence_source":"x","prev_hash":"0","hash":""}
h=calculate_block_hash(ev); print("hash len", len(h), "deterministic", h==calculate_block_hash(ev))
print("ALL FAILS:", fails if fails else "none")
