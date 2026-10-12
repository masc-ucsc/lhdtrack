# Synthesis attributes: implementation plan

Status: core source-attribute, grouping and exploration support is implemented;
evaluation and the remaining parity/reporting work are ongoing. The design below
was revised 2026-10-09 after fourteen rulings. Its "what exists" table records the
starting point, not today's implementation status. Section 10 records measured
evidence and remaining gaps as of 2026-10-10.

## 0. What exists today

The plan is an extension, not a greenfield feature. These are the pieces it builds on
and, where noted, changes.

| Piece | Where | State |
| --- | --- | --- |
| Pyrope **scope** attributes `{ ::[abc='…', color=<int\|"label">, ware=true\|false, delay=<ps>] … }` | `inou/prp/prp2lnast.cpp` `parse_scope_attributes` | exists; strict vocabulary; string labels are interned **per file** into dense ints and the label text is lost at parse time; the block lowers to an `attr_set(%__region_<id>_<seq>, "__region", …)` marker |
| Marker → node color + `coloring_info{algorithm:"block-attr", region_opts}` | `upass/tolg/upass_tolg.cpp` (`cur_color_`, `write_region_info`) | exists; `__region_ware` / `__region_delay` key names are hard-coded at the consumer |
| Attributes on declarations `x::[k=v]`, modules `mod f::[k=v](…)`, calls `f::[name=u3](…)` | grammar + `upass/attributes` | exist syntactically; no synthesis meaning yet; attribute **keys are bare identifiers** (`tree-sitter-pyrope/grammar.js` `attribute_assignment`) |
| SystemVerilog `(* … *)` | `inou/slang/` | **absent**: the reader never looks at attributes |
| `pass.color synth` cones | `pass/color/color_synth_cones.cpp`, `color_common.cpp` | exists; a source-seeded color is a **wall** (`kSeeded`: never grown, never merged into); algorithm ids are shifted above the seeded maximum; soft bounds `min_color_nodes` (12) and `max_gate` (30000 **predicted AIG**); `name_weight` biases boundaries onto stable names |
| `pass.partition` | `pass/partition/pass_partition.cpp` | exists; a region is a connected component of one color, except seeded colors, which are fused across disconnected clouds; `pass.abc` absorbs same-color child bodies into the parent region |
| Per-region overrides `flow\|delay\|load\|adder\|block_size\|multiplier\|barrel\|ware` | `pass/synth/region_driver.cpp` `parse_region_opts_entry`; CLI `pass.abc.region_opts` JSON or `coloring_info` | exists |
| Shared arithmetic blaster `Blast_options{adder, block_size, multiplier, reverse_barrel, …}` and kind registry | `pass/synth/blast.hpp`, `pass/synth/arith.hpp` | exists; used by **both** mappers |
| USYN option surface | `pass/usyn/pass_usyn.cpp` | exists; `adder` (no `brent`), `adder_block` (not `block_size`), `multiplier` default `csa` with no `auto`, no `barrel`; the `auto` trial loop lives on the ABC side (`region_driver.cpp`) |
| `lhd synth --set synth.*` | `lhd/lhd_kernel_common.cpp`, `lhd_options.cpp` | exists as a kernel-plumbed namespace (`synth.liberty`, `synth.mapper`, `synth.opentimer`, `synth.threads`, `synth.specialize`); `--set abc.adder=…` resolves to `pass.abc.adder` |
| Incremental region cache | `pass/synth/region_cache.hpp` | exists; rows keyed by module name `<top>__c<color>` plus digest and salt |
| Profile-guided simulation tuner | `lhd/lhd_sim_tune*.{hpp,cpp}`, `docs/opt_loop_incr.md` ruling I14 | exists; `sim.tune.profile=auto\|on\|off`, `sim.tune.file` / `sim.tune.export` (`lhd-sim-tune-file-1`), explicit `--set` pins frozen |
| Source anchors | `hhds::SourceId` (`upass_tolg.cpp` `cur_srcid_`) | exist on every node |

## 1. Objective and agreed rules

Express known synthesis decisions in source attributes and retain learned decisions in
the workdir. Improve partition quality without coupling independently synthesized
regions through a later global sharing or resynthesis pass.

- `synth.color=ID` is a synthesis-only attribute. It does not constrain simulator
  partitioning or scheduling.
- Equal explicit IDs request the same synthesis region. Different explicit IDs are never
  merged into one region.
- An explicit ID is a **seed** (ruling 2): the overlap algorithm grows it with unlabeled
  logic. `synth.grow=false` restores the exact-region (**wall**) behavior the scope
  channel has today. There is one ID concept, not two.
- Size bounds are **soft in both directions** (ruling 3): the existing `min_color_nodes`
  and `max_gate` targets, in their existing predicted-AIG unit. An explicit group may fall
  below the minimum or grow past the maximum at will; it is never split and never
  force-merged. The earlier "~1k mapped cells" goal is withdrawn.
- IDs match across synthesis scopes. Repeated instances of one module implementation
  reuse one synthesized body; they are not expanded into a region containing all physical
  copies. A per-instance annotation or specialization creates a distinct body and cache
  entry.
- Warn about zero overlap within a requested group when existing overlap data makes it
  inexpensive; never run a new traversal for the warning.
- Exploration state lives in the workdir as versioned JSON. Exploration runs **only on
  explicit request** (ruling 4); an ordinary `lhd synth` never starts an arithmetic trial.
- Arithmetic attributes and `lhd synth` defaults share one option registry across both
  mappers (ruling 6). A fixed choice is never explored.
- The resolved annotation hierarchy is dumpable (ruling 10).

Runtime failure or backend resource refusal must name the offending group without
silently changing its membership.

## 2. Source surface

### 2.1 Positions

Attributes attach at four positions (ruling 9), all of which the Pyrope grammar already
parses:

| Position | Pyrope | Meaning |
| --- | --- | --- |
| Module declaration | `mod alu::[…](a:S32, b:S32) -> (y:S32) { … }` | defaults for everything defined in the body (same rule as a block around the whole body) |
| Code block | `{ ::[…] … }` | defaults for operations defined in the block |
| Method / instance call | `alu::[name=u0, …](a=x, b=y)` | that **occurrence** joins the group; see §3 for the re-scope rule |
| Variable declaration (`reg`, memory, `mut`, `wire`) | `mut acc::[…] = 0`, `reg pc::[…] = 0` | anchors the whole cone that defines the named thing (§4) |

### 2.2 Pyrope spellings (ruling 1)

Both forms are legal and canonicalize to the same registry entries:

```
{ ::[synth=(color="crit", adder="cla")] … }          // tuple-valued attribute
{ ::[synth.color="crit", synth.adder="cla"] … }      // dotted keys
```

- Dotted keys need a grammar change: `attribute_assignment` currently takes a bare
  identifier lvalue. This is step-1 work and gates every example below.
- Conflicting values for one key inside one attribute list are an error, whichever
  spelling carries them.
- Values are string or integer literals. Integer `1` and string `"1"` are different IDs.
- The existing bare scope keys `color=`, `abc=`, `ware=`, `delay=` remain as
  **compatibility aliases** on `{ ::[…] }` blocks only. Default policy: a legacy bare
  `color=` keeps today's wall semantics (`synth.grow=false`) so no existing design
  changes behavior; only the `synth.color` spelling grows. Changing that default is a
  one-line decision, recorded here so it is not silent.

### 2.3 SystemVerilog spelling (ruling 12)

The escaped dotted identifier is the only spelling:

```systemverilog
(* \synth.color = "crit", \synth.adder = "cla" *)
```

It attaches to a module declaration, a named `begin : blk … end`, an instance, or a
variable declaration. `inou/slang` reads no attributes today, so this is a new reader
feature (symbol/statement attribute lookup), not a fixture check. Unknown `\synth.*` keys
are hard errors; every other attribute stays ignored. No `synth_<key>` underscore alias
and no string-bundle form.

### 2.4 Keys

| Key | Values | Notes |
| --- | --- | --- |
| `synth.color` | int or string | group ID; a string is a name, kept verbatim in IR and reports |
| `synth.grow` | `true` (default) / `false` | `false` = exact region (today's wall) |
| `synth.adder` | `auto` `rca` `cska` `cla` `prefix` `brent` | shared registry, §6 |
| `synth.multiplier` | `auto` `array` `tree` `csa` `sn` | `csa` is a multiplier strategy, not the adder `cska` |
| `synth.barrel` | `auto` `log` `reverse` | |
| `synth.block_size` | non-negative int | CSKA skip-block / CLA group width; 0 = auto |
| `synth.delay` | ps | region timing budget; 0 = area objective (existing `delay=`) |
| `synth.ware` | bool | existing `ware=` |
| `synth.abc` | string | per-region ABC flow (existing `abc=`); ABC mapper only, error under USYN |
| `synth.stop_arith` | bool | cut (true) or do not cut (false) at the covered wide adders, multipliers, dividers, overriding the global `pass.color.synth.stop_arith`; false also keeps them out of ware extraction |
| `synth.stop_cmp` | bool | the same for wide `<`/`>` comparisons |
| `synth.stop_shift` | bool | the same for runtime shifters |
| `synth.stop_mux` | bool | true = muxes are control-grouped, false = muxes stay in the data cone |

### 2.5 Worked examples

Block and variable, inside one module:

```
mod mac(a:S16, b:S16, en:Bool) -> (y:S32) {
  mut acc::[synth.color="mac"] = 0           // whole defining cone of acc joins "mac"
  { ::[synth.color="pp", synth.multiplier="tree"]
    mut p = a * b                            // the product tree is its own seed "pp"
    if en { acc = acc + p }                  // acc's write cone: "mac" (variable wins)
  }
  y = acc
}
```

Instance call (Dino-style lanes):

```
mut r0 = alu::[name=u0, synth.color="ex0"](a=op_a[0], b=op_b[0])
mut r1 = alu::[name=u1, synth.color="ex1"](a=op_a[1], b=op_b[1])
```

Two bodies of `alu` are synthesized and cached, one per call-site color. A third,
unannotated `alu(…)` call keeps the shared definition body.

Module default with an inner exact region:

```
mod alu::[synth.color="ex"](a:S32, b:S32, op:U4) -> (y:S32) {
  { ::[synth.color="carry", synth.grow=false, synth.adder="brent"]
    mut s = a + b
  }
  …                                          // everything else: "ex"
}
```

Called as `alu::[synth.color="ex0"](…)`, the inner block becomes the per-instance group
`ex0/carry` (§3).

## 3. Region identity and hierarchy reuse

- A group ID is a **path of segments** (ruling 9b). A top-level `crit` and an
  instance-nested `ex0/crit` are different groups by construction. Each segment is typed
  (int or string) and keeps its source text, locations and anchors in IR and reports; the
  dense color number is a temporary.
- **Call-site re-scope.** `alu::[synth.color="ex0"](…)` colors the callee's unannotated
  remainder `ex0` at that instantiation. An explicit block inside the callee keeps its
  intent as the nested group `ex0/<inner>`, distinct from other instances' `<inner>` and
  from `ex0`. Every annotated call therefore mints N+1 groups and one specialized body;
  unannotated calls keep reusing the definition body.
- Matching IDs are resolved in the synthesis composition **before** any temporary color
  is assigned. The per-file label interning in `prp2lnast.cpp` is removed: today two
  unrelated files can both mint color 1 and be absorbed together downstream. An ID is
  never namespaced implicitly by file or instance.
- Implementation/cache identity also includes the elaborated module and specialization;
  an ID alone never proves two bodies interchangeable.
- Track definition-level membership separately from occurrence counts: a body
  instantiated N times is mapped once and instantiated N times; report both.
- **Region module names** (ruling 14): an explicit group is named by its sanitized path
  (`<top>__g_ex0_carry`; a collision is an error); an automatic cone takes the stable
  anchor name the `name_weight` machinery already favors (its anchor register or output)
  instead of `__c<color>`. Cold and warm runs implement the same naming with no side
  file.

## 4. Annotation meaning and preservation

| Target | Meaning |
| --- | --- |
| Module declaration | Default color for every operation defined in the body; identical to a block around the whole body. An explicit inner annotation creates an inner boundary (nested group), never two IDs on one node. |
| Code block | Default for operations defined in the block; same inner-boundary rule. |
| Instance call | The occurrence joins the group and re-scopes the callee's inner groups (§3). |
| Register / variable | Anchor the **whole defining cone** (ruling 11): every write's right-hand side, the SSA merge logic, and their transitive fan-in, stopping at upstream state, primary inputs or another explicit ID. Q consumers / downstream readers are not pulled in. |
| Memory | Anchor the storage implementation and its interface. Address, write-data, control and consumer cones are treated explicitly; native memory and port semantics are preserved (memories stay black boxes to the mapper). |

- Clock, reset and shared enable/stall logic never pull all their consumers into a data
  group. The existing D-cone / control-cone distinction is reused; an explicit annotation
  on control logic is honored.
- A group whose anchored logic is a single node or wiring only gets a cheap
  "nothing to synthesize here" warning with its source span. It is a warning only: an
  explicit group is never force-merged (ruling 3).
- Anchors and provenance survive SSA, inlining, cprop, bitwidth, specialization,
  partitioning and arithmetic lowering via the existing `SourceId` channel. Logic that is
  optimized away leaves an explanatory record, not a fake nonempty region.
- If differently annotated computations become one value, first attempt a legal cut and
  single ownership of the shared producer. Warn with both source locations when the
  requests cannot be met exactly. Never duplicate gates or state to hide a conflict; a
  genuinely indivisible object with incompatible explicit IDs is an unresolved-conflict
  diagnostic, not a false success.
- Do not advertise a spelling until the relevant reader accepts it (grammar change, Slang
  adapter).

## 5. Constrained overlap algorithm

1. Collect explicit anchors on the prepared, optimized graph. Establish which elaborated
   bodies are unique and which occurrences reuse them.
2. Form one seed region per explicit path ID with all its anchors, regardless of the
   soft targets. A foreign explicit ID is a traversal boundary. A `synth.grow=false` seed
   is a wall exactly as `kSeeded` is today.
3. Compute cone overlap with the existing machinery. Grow seeded regions before
   creating automatic regions; prefer the greatest useful overlap with stable tie-breaks
   and the current soft size/cut heuristics. Explicit groups ignore both soft bounds.
4. Merge only compatible candidates: unlabeled with unlabeled, unlabeled with one named
   region, or regions carrying the same path ID. Reject every union that would hold two
   different explicit IDs.
5. Shared, unannotated logic gets one owner here; other regions receive boundary signals
   from that owner. Never copy a shared cone into each consumer. Cones with no useful
   overlap, or not admitted by the growth heuristics, remain for automatic coloring.
6. Run normal coloring on the remainder, preserving seeded ownership and explicit-ID
   exclusions through cleanup, size adjustment, partition emission and hierarchy reuse.

The existing first-owner traversal must not decide shared ownership before the explicit
seeds are considered. Record the selected owner and meaningful rejected merges. Keep
surviving ownership stable across unrelated edits; no global renumbering or greedy
reshuffle when a cone is inserted.

Distinct IDs may be connected by ordinary wires. Warn about contradictory ownership or a
requested cone that must be cut, aggregated by group/cut rather than per bit. A same-ID
group with disconnected overlap components stays one group (partition already fuses
seeded colors) and gets the inexpensive zero-overlap warning.

A contraction such as `A -> B -> A` can have a cyclic region dependency graph even when
the combinational netlist is acyclic. Synthesis reasons at signal dependencies and
produces a valid stitched implementation or reports a backend limitation; it never splits
A or merges A with B to satisfy a region-DAG assumption. This is consistent with the
legalize ruling that a loop Sub is never split or inlined and a true combinational loop
through it is an error. Simulation scheduling is unaffected by `synth.color`.

## 6. Arithmetic options and precedence

One option registry and resolver serve global defaults, source attributes, region
overrides and persisted exploration results, for **both mappers** (ruling 6).

| Canonical | ABC today | USYN today | Work |
| --- | --- | --- | --- |
| `synth.adder` | `pass.abc.adder` `auto\|rca\|cska\|cla\|prefix\|brent` | `pass.usyn.adder` `auto\|rca\|cska\|cla\|prefix` | add `brent` to USYN |
| `synth.multiplier` | `pass.abc.multiplier` `auto\|array\|tree\|csa\|sn` | `pass.usyn.multiplier` `csa\|tree\|array\|sn` (default `csa`) | add `auto` to USYN |
| `synth.barrel` | `pass.abc.barrel` `auto\|log\|reverse` | — | expose in USYN |
| `synth.block_size` | `pass.abc.block_size` | `pass.usyn.adder_block` | alias `adder_block` onto the canonical name |

- Both mappers already share the models (`pass/synth/blast.hpp` carries `adder`,
  `block_size`, `multiplier`, `reverse_barrel`); the gap is option plumbing plus porting
  the `auto` trial loop from `region_driver.cpp`. **USYN parity is a work item of this
  plan**, not a limitation to document.
- A pinned value the active mapper cannot honor is a **hard error** naming the source
  location and the mapper. Never fall back silently; never reinterpret an unknown value.
  `synth.abc` (a flow string) under `synth.mapper=usyn` is such an error.
- Existing spellings (`abc.adder`, `pass.abc.adder`, `pass.usyn.adder_block`) stay
  accepted and normalize through the registry; `synth.<key>` becomes a kernel-plumbed
  label that fans out by `synth.mapper`, like `synth.liberty` does today.
- A new architecture needs an implementation and a registry entry before source or CLI
  accepts it (`Kent` remains illustrative).

Resolution, most specific first:

1. An explicit command-line override targeting a region/operation wins there (the
   existing `region_opts` contract).
2. The most specific source choice (variable, then call, then block, then module) wins
   over enclosing defaults; those win over the global selector.
3. A concrete resolved choice pins the selector and disables exploration of it.
4. A resolved `auto` uses a compatible stored decision when one exists, otherwise the
   heuristic below. An absent local choice inherits; an explicit `auto` is a deliberate
   local request.

**Heuristic without a profile** (ruling 5): the region's resolved `delay` is the
objective axis. `delay=0` (no timing target) picks the smallest implementation; a timed or
critical region picks the fastest; the default heuristic is ABC's existing `auto` policy.
There is no separate objective knob.

Report each effective choice and its origin. Different operators in one color may carry
different fixed implementations; resolve them during lowering without splitting the
region. Shared computations with conflicting explicit choices need a source-level
diagnostic. Existing multiplier/internal-adder coupling rules stay and are documented.
An arithmetic selection chooses the initial structure handed to the mapper; ABC may still
restructure and technology-map inside the region.

## 7. Workdir exploration and profiling

### 7.1 Trigger (ruling 4)

`synth.tune.profile` has three modes, but unlike the simulation tuner the default
**never explores**:

| Mode | Behavior |
| --- | --- |
| default (`auto`) | apply defaults and attributes; **replay** a compatible stored decision from the workdir; never start a trial |
| `on` | explicit exploration run: try the bounded candidate set, persist verdicts |
| `off` | ignore the store; shared defaults only |

The no-annotation workflow is two runs: run 1 with `--set synth.tune.profile=on`
explores and stores; run 2 (default) replays. An attribute or explicit `--set` pins its
selector and is never explored. A simulation-only invocation never launches a synthesis
search.

### 7.2 Storage

```text
<workdir>/synth_tune/
    state.json       # compatible selected choices and exploration status
    history.jsonl    # attempted choices, measurements, validation and verdicts (append-only)
    applied.json     # exact choices and provenance used by this synthesis
```

Versioned schema, atomic updates. Tune history is separate from compiled-body cache
entries; a missing or damaged store never authorizes stale netlist reuse. Follow
`lhd.incremental` for persistence and record why a stored decision was accepted or
ignored.

A decision record holds: group path ID, implementation/operation identity, widths and
signedness, logic and boundary context fingerprints, compiler/mapper identity, Liberty
hash, constraints, selected implementation, measured cells/area, whole-design timing
where applicable, synthesis cost, validation level, origin. Occurrence counts are
recorded separately; an ID alone is not a cache key.

### 7.3 Verdict and validation

- The verdict axis is the region's `delay` setting (ruling 5): area when `delay=0`,
  delay when timed. Exploration keeps an incumbent, a bounded candidate set and a
  time/attempt budget, and records accepted, rejected, failed and inconclusive attempts.
  Unchanged regions are reused while one operation or region is tried. Explicit IDs are
  never recolored during exploration.
- **Validation** (ruling 7): by default a trial is trusted structurally, exactly like
  today's `adder=auto` (same shared blaster, same mapper). `synth.tune.validate=region`
  runs a per-region LEC (candidate vs. its unmapped source region, bounded by the soft
  maximum) before a decision is persisted; `=design` runs whole-design LEC once on the
  final selection. The record states which level ran. Simulation agreement is coverage,
  never a proof.

### 7.4 Outputs (ruling 8)

JSON is the structured result; text is its rendering, never a parser input. After an
exploration run, stdout prints, per explored operation: effective defaults and pins,
candidates tried, selected choice and origin, cells/timing/time, validation level, cache
hits/misses and the stopping reason — and the **pin-hint** that would freeze the
decision in source:

```
synth_tune: ALU.prp:42  `mut sum = a + b`   adder: auto -> cla   (812 -> 741 cells, 3 trials)
            pin with:  mut sum::[synth.adder="cla"] = a + b
```

`synth.tune.export=FILE` writes the selected decisions in a versioned file and
`synth.tune.file=FILE` replays one, mirroring `lhd-sim-tune-file-1`, so a decision can
travel between workdirs or be committed beside a design without annotating it. The
compiler never rewrites `.prp` source.

### 7.5 Annotation hierarchy dump (ruling 10)

The resolved hierarchy is inspectable on every run: `synth_groups.json` in the report
directory plus a readable tree, listing each group by path ID, its origin (source span,
call site, CLI, tune store), members per definition and per occurrence, specialized vs.
reused bodies, the effective per-operator arithmetic choice with provenance, and rejected
merges and conflicts. It plugs into the existing `lhd tool tree/grep` filter vocabulary
(`color:`, `from:`) rather than a new command.

### 7.6 Simulation profile

Simulation profiling may supply activity and cost evidence to prioritize synthesis
experiments through common orchestration. It never changes simulator colors or authorizes
pruning unobserved hardware behavior; simulation and synthesis choices and objectives
stay separate.

## 8. Incremental contract

- Cache reuse requires equal computation, interface and resolved mapping policy, with
  compatible compiler/backend/Liberty context. An ID is a stable binding name, never
  permission to reuse a changed body.
- Boundary names and ordering survive unrelated edits: region modules are named by path
  ID or stable anchor (§3), never by temporary node numbers or dense color indices.
- A changed region or local implementation choice rebuilds its affected bodies and any
  genuinely changed interfaces/context. The exploration history file or another region's
  selection never salts an unrelated region.
- Warm unchanged runs replay selected choices and stitched bodies without repeating
  trials. Cold and warm runs implement the same policy; cache enablement alone never
  changes port order or mapped decisions.
- Sharing and boundaries are established before mapping. No post-mapping global sharing
  pass moves cells, adds ports or rewrites independently cached bodies.

## 9. Implementation sequence and evidence

1. **Common metadata and resolver.** Path IDs, source anchors, option provenance; the
   grammar change for dotted keys and the tuple form; the Slang attribute adapter;
   removal of per-file label interning; the mapper-neutral registry with USYN aliases and
   the hard-error rule. Keep every existing source/CLI spelling accepted. Test metadata
   survival through lowering.
2. **Seeded synthesis coloring.** Seed-and-grow with `synth.grow=false` walls, soft
   bounds ignored for explicit groups, same-ID unions and different-ID exclusions carried
   through overlap merging, size adjustment, partition emission and hierarchy reuse;
   call-site re-scoping and per-call specialized bodies; path-ID region naming;
   `synth_groups.json`.
3. **Focused correctness and reuse tests.** Same/different IDs, shared producers, zero
   overlap, soft-bound overflow in both directions, nested blocks and re-scoped inner
   groups, legacy `color=` walls, memory/control boundaries, repeated definitions,
   specialization, the trivial-group warning, `A -> B -> A`. Prove small annotated/plain
   fixtures equivalent and check mapped variants. Verify one unrelated edit does not
   invalidate surviving groups; compare cold and warm naming and mapping policies.
   **Development gate**: LEC over the existing LEC-passing block set while the synthesis
   code changes; a LEC timeout passes, a refutation is a bug.
4. **Dino evaluation in `../lhdsuite`** (ruling 13): `dino/pyrope2/` through
   `bench/synth.sh`, results on the `opt_loop_synth` scoreboard. Stage one immutable
   compiler and remeasure matched flat and guided variants with identical source bytes,
   Liberty, SDC and arithmetic settings; a diagnostic mode may ignore grouping for the
   flat reference and records that it did. Group both lanes' EX/MEM D cones and related
   execution control first (via instance-call colors on the lane ALUs), then a fetch
   group around PC and IF/ID. Preserve memory boundaries. Measure cells, timing, cone
   sharing/depth, compile/color/ABC time and cache reuse; compare an unchanged warm run,
   one ALU edit, one control edit and an edit in a repeated module body. The guardrail
   is cells at or below the flat reference with timing reported; the verdict rule of §6
   applies to arithmetic choices. Per the later user instruction, retain only a small
   subset of measured QoR-improving annotations in Pyrope. Archive unannotated source
   snapshots for baseline/profile/replay comparisons. For every lhdsuite design, edit
   the maintained `pyrope2/` tree; never hand-edit autogenerated `pyrope/`.
5. **Persisted arithmetic exploration and USYN parity.** Workdir decisions, explicit-only
   trigger, pin-hints, export/import, `synth.tune.validate`; USYN exposure of `brent`,
   `barrel`, `multiplier=auto` and the ported trial loop. Test precedence, pinning,
   replay without trials, stale/corrupt stores, bounded unsuccessful attempts, per-region
   invalidation and the unsupported-value error. Then connect simulation-profile
   attribution without touching simulation grouping.

Code to extend: `inou/prp/prp2lnast.cpp`, `../tree-sitter-pyrope/grammar.js`,
`inou/slang/`, `upass/tolg/upass_tolg.cpp`, `pass/color/color_synth_cones.cpp`,
`pass/color/color_common.cpp`, `pass/partition/pass_partition.cpp`,
`pass/synth/region_driver.cpp`, `pass/synth/region_cache.hpp`, `pass/synth/arith.hpp`,
`pass/usyn/pass_usyn.cpp`, `lhd/lhd_kernel_common.cpp`. The `lhd_sim_tune*` code is a
lifecycle reference for storage and export, not a reason to couple simulation
partitioning to synthesis IDs or to self-trigger exploration.

The first success criterion is an annotation contract that preserves behavior, same-ID
membership, different-ID exclusion and localized reuse. Dino must then show whether guided
grouping recovers QoR: current mapped-cone analysis finds 4,831 shared cells between the
two flat EX/MEM data cones and a 12,543-cell union, which motivates grouping them but does
not predict guided quality. Failed or regressing experiments stay in the append-only
ledger.


## 10. Implementation and evaluation evidence — 2026-10-10

LiveHD repairs are committed as `0f648871f` (synthesis policy replay, shared-region
reuse, clock gates and independent memory identities), `050a901c2` (warm reuse of
lowered memories), and `8c235c827` (keep synthesis policies out of value-attribute
propagation). The last fixes a valid annotated scalar passed through a runtime tuple
in an imported helper; the maintained Dino source does not need a workaround.
The full optimized LiveHD suite passes: **4,716 passed, two skipped**. The new small
regression is checked against hand-written Verilog, with bit-fuzz, state matching and
a Verilog/Pyrope round trip. Contract fixtures were not edited.

### Small retained arithmetic subset

Run `20261010T143501-syntha`, frozen compiler `272836131b322bf7`, identical ASAP7
Liberty/constraints within each pair. Source equivalence proves all three annotations.

| Pyrope design | Pin | Cells, baseline → pinned | Critical path, baseline → pinned | Exact mapped proof |
| --- | --- | --- | --- | --- |
| mul | `synth.multiplier="csa"` | 460 → 391 | 920.1475 → 426.5706 ps | proven |
| mul_add_uns | `synth.multiplier="sn"` | 2554 → 1870 | 2354.3418 → 943.6793 ps | timeout |
| fmaa | `synth.multiplier="sn"` | 5282 → 3959 | 3467.3511 → 1205.0906 ps | timeout |

The two mapped-proof timeouts are unresolved, not proofs. These pins use the agreed
structural-trust default and development gate; no refutations occurred. The add_mop
candidate was rejected because it increased cells. No Verilog source was annotated.

### Maintained Dino Pyrope2

Compiler `89129e098993f366` (`8c235c827`), frozen binary/runfiles, ASAP7 Liberty SHA256
`01c72c559a01128db5a83134d28bd5b7c8e411851488599fc3417bf849d0d1a7`, 400 ps
clock, zero IO delays, identical fixed Brent/SN arithmetic settings, no trials. Both
variants consume the same annotated bytes; the flat diagnostic ignores grouping.
Plain versus annotated source is proven unbounded across all nine definitions before
measurement. Mapped whole-core equivalence remains unproved.

| Grouping | Run | Flat cells / guided cells | Flat / guided critical path | Cold flat / guided synthesis | Guided warm |
| --- | --- | --- | --- | --- | --- |
| execution + fetch | `20261010T153924-dino-attributes` | 37651 / 39884 | 1487.4773 / 1324.6500 ps | 64.806 / 55.214 s | 2.884 s; 18/18 ABC hits, STA hit |
| lanes | `20261010T154506-dino-attributes` | 37651 / 39912 | 1487.4773 / 1418.2674 ps | 60.878 / 38.203 s | 1.624 s; 23/23 ABC hits, STA hit |

Neither grouping meets the cells-at-or-below-flat guardrail; neither is retained in
Dino. Execution/fetch edits rebuild 1/18 regions for the ALU, 3/18 for control and
9/18 for the shared generic StageReg body. Their synthesis times are 35.757, 41.430
and 9.798 seconds. These deliberate functional edits test invalidation/locality; the
base annotation proof does not prove an edited core equivalent to the unedited core.

Both guided variants retain 3,888 register bits versus 3,577 flat. The 311 extra bits
are largely unused instruction fields crossing stage interfaces (including write-back
instruction bits). This is a boundary-demand issue worth addressing before broader
source guidance; no global post-mapping cleanup was added. Earlier generated-source
Dino experiments remain in the append-only ledger under `dino_pyrope`; future source
work uses `dino_pyrope2`. The adapter is `tools/profile_dino_attributes.py`; suite
measurements go through the snapshotted `bench/synth.sh` and the opt-loop scoreboard.

### Current complete-cohort replay check

All **28/28** source equivalence checks pass on compiler `89129e098993f366`, run
`20261010T154743-syntha`. Run `20261010T154754-syntha` measures the unannotated
source snapshot in 84 sequential baseline/profile/replay jobs, with eight attempts
and a 15-second soft trial budget. It is in progress. Reports are generated by
`tools/report_synthesis_attributes.py`; different compilers/cohorts are never pooled
for ratios, and exact mapped proof verdicts are shown separately from source proofs.

### Remaining implementation gaps

- USYN exploration still uses its native logical proxy rather than mapped ASAP7 timing;
  mapped timing objective parity and complete region delay/load forwarding remain open.
- Some provenance/stopping-reason, width/sign/source-context and rejected-merge reports
  need richer presentation; fixed inherited defaults can still be labeled "explored".
- Full tree/filter integration and empty module/block elision warnings remain incomplete.
- Simulation-profile attribution has not yet been connected to experiment prioritization.
- Dino boundary bit demand and hierarchical state pruning need further investigation;
  source groups are not a substitute for fixing this QoR loss.
