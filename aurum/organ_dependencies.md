# Organ dependency map

**Authoritative, code-derived** list of which organs are consumed by which. Each edge is
"**X reads/calls Y**", extracted from the actual constructor injection + `self._<organ>.<method>()`
calls in `aurum/`, NOT from the spec. Keep current as wiring changes.

Regenerate the raw evidence with:
```
rg "self\._(el|cs|tcm|gr|pm|mgc|oi|ca|dd|bb|hvp|ag|cg|tl|rs|sen|sh)\.[a-z_]+\(" aurum/aurum
rg "self\._[a-z_]+\._[a-z]" aurum/aurum     # must be EMPTY (no cross-organ private reach)
```

## Edges (consumer → consumed.method)

| Consumer | Consumed | Public method(s) called |
|----------|----------|-------------------------|
| CB  | EL  | `append` |
| AG  | EL  | `append` |
| HVP | EL  | `append` |
| EG  | EL  | `append`, `query` |
| PM  | EL  | `append` |
| TCM | EL  | `append` |
| GR  | EL  | `append` |
| GR  | CS  | `add_node` |
| OI  | EL  | `append` |
| OI  | PM  | `check` |
| LS  | EL  | `append` |
| LS  | PM  | `add` |
| LS  | CS  | `whatif` (via `rollback(cs=…)`) |
| AA  | EL  | `append` |
| AA  | GR  | `active` |
| AA  | TCM | `recommend_domain` |
| KVE | EL  | `append` |
| KVE | MGC | `archive` |
| RR  | EL  | `get_event`, `decision_for_el_seq` |
| RR  | CS  | `graph` (via `verify_pointer(cs=…)`) |
| IDM | EL  | `snapshots` |
| MPD | EL  | `iter_events`, `count_decisions`, `count_conflicts` |
| MGC | EL  | `archive_region` |
| MGC | CS  | `is_leased`, `whatif` |
| MGC | TCM | `retirement_candidates` |
| CA  | EL  | `tip_seq`, `log_conflict` |
| DD  | CA  | `conflicts` |
| DD  | OI  | `effectiveness` |
| DD  | EL  | `append` |

## Consumed organs (edge targets) — public read/write surface

These are the only organs that need an interface; route ALL callers through it.

- **EL** (consumed by nearly everything) — **reader:** `tip_seq`, `query`, `lineage`,
  `verify_chain`, `health`, `get_event`, `iter_events`, `events_for_object`, `snapshots`,
  `decision_for_el_seq`, `count_decisions`, `count_conflicts`. **writer:** `append`,
  `log_conflict`, `log_decision` (decision+snapshot, atomic), `write_evidence_snapshot`,
  `archive_region` (structural compression — EL owns its archive table).
- **CS** (MGC, GR, RR, LS) — `add_node`, `add_edge`, `graph`, `whatif`, `lease`, `heartbeat`,
  `release`, `is_leased`. (`project` stays deferred.)
- **TCM** (AA, MGC) — `register`, `record_use`, `check_overlap`, `recommend_domain`,
  `taxonomy`, `select`, `retirement_candidates`.
- **GR** (AA) — `add`, `get`, `active`, `expire`, `depends`, `health`, `touch`.
- **PM** (OI, LS) — `add`, `get`, `applies`, `check`.
- **OI** (DD) — `interpret`, `trend`, `effectiveness`, `sample_for_human`,
  `record_human_verdict`, `proxy_calibration`.
- **CA** (DD) — `arbitrate`, `conflicts`.
- **MGC** (KVE) — `scan`, `archive`, `restore`, `merge`, `lineage`, `compress`.

## NOT interfaced (no inbound edge — interface would be over-engineering)

`IDM`, `MPD`, `CC` (derived views — consumed by nothing), `AO` (deferred boundary),
`KVE`, `TL`, `RS`, `SEN`, `SH`, `SDG`, `SM`, and the spine stubs `PK`/`BB`/`TS` (not yet built).
`BB` is referenced only as an injected `bb_record` *callable* (OI, AA), never as an organ object,
so it crosses no private boundary.

## Invariant

No organ may touch another organ's `_underscore` attributes. The `self\._x\._y` grep above MUST
return zero hits inside `aurum/aurum/`. Every edge here is a public method call, so a wiring
mismatch surfaces as an AttributeError at the call site, never a silent no-op.
