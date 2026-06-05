import importlib, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aurum.base import Unbuilt
from aurum.build_state import BUILT, is_built

ORGANS = [
    ("aurum.spine.pk","PolicyKernel","PK"),("aurum.spine.bb","BlackBox","BB"),
    ("aurum.spine.ts","Toolsmith","TS"),("aurum.durability.el","EvidenceLedger","EL"),
    ("aurum.durability.rr","ReproducibilityRunner","RR"),
    ("aurum.durability.mgc","MemoryGarbageCollector","MGC"),
    ("aurum.durability.kve","KnowledgeValidityEngine","KVE"),
    ("aurum.durability.gr","GoalRegistry","GR"),("aurum.durability.pm","PreferenceModel","PM"),
    ("aurum.durability.tcm","ToolCatalogManager","TCM"),
    ("aurum.novel.aa","APIArchaeologist","AA"),("aurum.novel.ls","LivingSpecification","LS"),
    ("aurum.novel.hvp","HeterogeneousVerifierPanel","HVP"),("aurum.novel.eg","EpistemicGovernor","EG"),
    ("aurum.novel.cs","CausalSimulator","CS"),("aurum.novel.ag","AuthorityGovernor","AG"),
    ("aurum.novel.oi","OutcomeInterpreter","OI"),
    ("aurum.extensions.sdg","SkillDependencyGraph","SDG"),("aurum.extensions.sm","SubstrateMapper","SM"),
    ("aurum.support.tl","TrustLadder","TL"),("aurum.support.cb","CircuitBreaker","CB"),
    ("aurum.support.sh","ShadowMode","SH"),("aurum.support.cg","CostGovernor","CG"),
    ("aurum.support.rs","ResourceScheduler","RS"),("aurum.support.sen","Sensorium","SEN"),
    ("aurum.observability.idm","IdentityDriftMonitor","IDM"),
    ("aurum.observability.mpd","MemoryPoisoningDetector","MPD"),
    ("aurum.observability.cc","ConcentrationCheck","CC"),
    ("aurum.deferred.ao","AgentOrchestrator","AO"),
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
