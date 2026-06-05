# AURUM — scaffold

Typed interface scaffold + AURUM_ERR assertion harness for the AURUM architecture.
This is **not** the implementation. It is the skeleton Opus codes *into*: every
organ method raises `aurum.base.Unbuilt` until implemented, and the twelve
compliance-gate assertions are wired to stay **skipped** until their organ exists,
then go **live** (must pass before commit).

Authoritative spec: `aurum_organs_spec.md` (ships alongside this repo).

## Worked examples already in the repo

Two organs ship as **reference implementations** (not stubs), so Opus has a
concrete pattern to match:

- **EL** (`aurum/durability/el.py`) — SQLite (WAL), DB-level append-only triggers
  raising `AURUM_ERR_001`, a STORED generated column on `capability_class` for
  hot-path reads, recursive-CTE `lineage()`, application-level hash chain in
  `append()`/`verify_chain()`, fail-safe append, and a separate archive table so
  MGC compression never deletes from the live table.
- **CS** (`aurum/novel/cs.py`) — a **separate** SQLite file from EL (EL is
  immutable/trigger-enforced; CS is mutable graph + leases — mixing them would
  break the append-only triggers). Graph nodes/edges, `whatif` blast-radius via
  recursive CTE, and TTL + heartbeat leases (a crashed holder's lease lapses so
  MGC reclaims the artifact). CS writes events to EL; EL never writes to CS.

`build_state.BUILT` has EL and CS flipped to True. `AURUM_ERR_011` (EL fail-safe)
is LIVE and passes; `AURUM_ERR_001` is implemented but stays skipped until CB is
built (it needs the CB-lockout half). `tests/test_reference_el_cs.py` exercises
both organs directly.

### Storage decisions (carry through Tiers 1–3)
SQLite, WAL mode, one file per store. EL and CS are SEPARATE files by design.
Migrate the EL append path to Postgres only at Tier 4 (multi-agent), where
single-writer SQLite hits `SQLITE_BUSY` under concurrent agents — the hash chain
and fail-safe logic live in `EL.append`, so that's a backing-store swap behind an
unchanged interface. Other mutable stores (BB corpus, LS constitution, KVE
metadata) also stay separate from EL.

## Layout

```
aurum/
  types.py          # shared TypedDicts (ELEvent, EGScore, CSNode, EndpointEntry, ...)
  base.py           # Unbuilt convention for stubs
  build_state.py    # BUILT registry — flip an organ True as you implement it
  gates.py          # AURUM_ERR gate -> required-organs registry (pytest-free)
  spine/            # Tier 0   PK, BB, TS                 (mock stubs per handoff)
  durability/       # Tier 0.5 EL, RR, MGC, KVE, GR, PM, TCM
  novel/            # Tier 1   AA, LS, HVP, EG, CS, AG, OI
  extensions/       # Tier 2   SDG, SM
  support/          # Tier 3   TL, CB, SH, CG, RS, SEN
  observability/    # Tier 3.5 IDM, MPD, CC  (DERIVED VIEWS over EL, not organs)
  deferred/         # Tier 4   AO (documented boundary, not built in v1)
tests/
  test_structure.py   # GREEN on the bare scaffold: all 29 classes import + stub correctly
  test_assertions.py  # AURUM_ERR_001..012 — skip-until-built compliance gates
verify_no_pytest.py   # standalone soundness check (no pytest / no network needed)
```

28 active organs + AO (deferred) = 29 classes.

## Build order (from the spec; also the implementation sequence)

1 PK · 2 EL · **3 IDM+MPD (pull forward — passive telemetry baseline)** · 4 RR ·
5 CG · 6 CS-minimal · 7 GR · 8 CS-full core · 9 TCM · 10 AA · 11 HVP · 12 EG ·
13 PM · 14 OI · 15 AG · 16 SDG · 17 SM · 18 LS · 19 MGC · 20 KVE · 21 CC ·
22 RS · 23 TL/CB/SH/SEN.
(BB, TS spine stubbed first alongside PK. CS deep `project` and AO deferred.)

## How the assertion harness works

`build_state.BUILT` starts all-False. Each `AURUM_ERR_0NN` test calls `_gate(...)`,
which **skips** while its required organs are unbuilt — a skip is *not* a pass.
As you implement an organ, flip its flag in `build_state.BUILT`; its gate goes live
and you must fill in the test body (marked `TODO(opus)`) so it genuinely catches the
violation. **Do not weaken an assertion to make it pass** — a blocked gate is the
system protecting itself from its own code generation.

The main execution loop must refuse to initialize if any LIVE assertion fails.

## Running

```bash
pip install pytest
pytest -q            # test_structure green; AURUM_ERR_* skipped until organs are built
```

No environment? `python3 verify_no_pytest.py` checks scaffold soundness with stdlib only.

---

## Handoff prompt for Opus

> You are the primary build agent implementing the AURUM architecture exactly as
> specified in `aurum_organs_spec.md`, into the scaffold in this repo.
>
> 1. STRICT DEPENDENCY ORDER. Implement organs in the Build Order. Do not start a
>    capability organ (AA, HVP) until the telemetry layer (EL, IDM, MPD) is
>    functional. EL is critical infrastructure — build and harden it first after PK.
> 2. ASSERTIONS AS COMPLIANCE GATES. As you finish an organ, flip its flag in
>    `aurum/build_state.py` and implement the matching `AURUM_ERR` test body in
>    `tests/test_assertions.py`. Each gate, once live, must genuinely catch its
>    violation. Never weaken an assertion to make it pass. The execution loop must
>    fail to initialize if any live assertion fails.
> 3. TYPES. Use the TypedDicts in `aurum/types.py` and strict hints throughout.
>    Replace `raise Unbuilt(...)` bodies with real logic; keep the signatures.
> 4. BACKGROUND ORGANS register with RS (`aurum/support/rs.py`) — never spin up raw
>    unmanaged threads. RS is stubbed early so the interface exists from the start.
> 5. SPINE. PK/BB/TS are mock stubs satisfying the named signatures and
>    trust-tagging assumptions; deepen them as needed but preserve fail-closed posture.
>
> Begin with EL: implement append (hash-chained, fail-safe), query, lineage,
> verify_chain, health. Then make AURUM_ERR_001 and _011 live and green. Print the
> EL implementation and the now-passing gates before moving to the next organ.
