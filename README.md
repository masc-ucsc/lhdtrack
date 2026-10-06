# lhdtrack

A daily quality-of-results regression for [LiveHD](https://github.com/masc-ucsc/livehd),
tracking **Pyrope vs Verilog** and **LiveHD vs the open-source reference flow** across
synthesis and simulation.

It answers four questions every day, per test:

| | question | how |
| --- | --- | --- |
| **QoR** | is LiveHD's synthesis competitive? | area / cells / delay / WNS vs `yosys + slang + abc` |
| **Speed** | is LiveHD fast? | compile, synthesis and simulation wall-clock vs `yosys`/`verilator` |
| **Memory** | is LiveHD lean? | peak RSS, per stage |
| **Timing fidelity** | does LiveHD's timer tell the truth? | LiveHD OpenTimer vs OpenSTA, same netlist, same Liberty |

Designs are small on purpose. Every run is a **full** recompile and resynthesis — this is
not an incrementality benchmark (that is [lhdsuite](../lhdsuite)'s job, on much larger
cores).

---

## Quick start

```bash
make toolchain          # build lhd, yosys, abc, verilator, opensta + stage Liberty
make run                # THE DAILY REGRESSION -- also the cron entry point
make report             # re-render target/ from data/, without re-measuring
make show               # the same numbers, in the terminal
make help               # everything else
```

For cron, `make run` is the whole interface:

```
0 3 * * *  cd /path/to/lhdtrack && make run >> var/cron.log 2>&1
```

It exits non-zero when a test fails — so cron mails you — and still leaves a
readable page behind.

Useful variations:

```bash
lhdtrack run --test add --test br_counter_incr    # a subset
lhdtrack run --tech sky130                        # one tech
lhdtrack run --flow syn_lhd_pyrope                # one flow
lhdtrack run --refresh yosys_abc                  # force a baseline re-measure
lhdtrack run --no-cache                           # ignore the baseline cache entirely
```

### Native USYN on ASAP7

```bash
make usyn RUN_ARGS="--test add --jobs 1"  # end-to-end smoke
make usyn                                # entire corpus: ABC and native USYN
```

This target uses the staged toolchain manifest, the same merged Liberty and each
case's SDC, OpenSTA on the emitted netlist, and both native and independent Yosys
LEC of that exact emission. USYN performs Boolean optimization itself; ABC is
used only for its optional cell mapping. Compiler SAT optimization is explicitly
off in this default comparison. The evaluation driver's `--satopt-profiles enabled`
explicitly enables `pass.satopt=true`; this is a recipe change from older runs
which incorrectly relied on an enabled compiler default. The separate standard
ABC SAT experiment still uses `LHDTRACK_ABC_SATOPT=false`.

Run the native residual ablation with the same staged compiler:

```bash
for stage in selection residual feedback; do
  python3 tools/run_verilog_eval.py --tech asap7 --satopt-profiles disabled \
    --mapper usyn --usyn-stage "$stage"
done
```

Each run retains all failed, skipped and undecided rows and records the stage's
actual settings. `--resume RUN_ID` restores those settings. `--lec-from RUN_ID`
checks each retained SAT profile's own measured netlist. Synthesis rows include
source, SDC, Liberty and emitted-netlist digests; native work is counted under the
map phase. Mapped cell totals are read from both ABC and USYN technology-map reports.
Native rows also retain per-stage proxy costs, additive work counters and their
decision-report digest. These definition-region estimates are separate from
mapped cell area and the process RSS measurements.

To combine the stages with a retained ABC run, use
`tools/report_usyn_ablation.py --abc RUN_ID --selection RUN_ID --residual RUN_ID
--feedback RUN_ID --output data/usyn-asap7.json`. Add `--improved RUN_ID` to
include a later native frequency iteration alongside the controlled stages.
It writes the full matrix and a
page under `target/`. Frequency ratios require equal source/SDC digests, complete
constrained timing and an unbounded proof of each exact netlist. Headline geomeans
retain the idiomatic, LEC-proven Pyrope population; the full table shows every case.
The same-slot footer uses the intersection of all variants' proven populations.
The optional improved run also reports a paired native-to-native frequency gain.
Regenerate the committed matrix without `var/` using
`python3 tools/report_usyn_ablation.py --from-summary data/usyn-asap7.json`.

### Retained ASAP7 evaluation (2026-10-02, satsuma)

[data/usyn-asap7-20261002-satsuma.json](data/usyn-asap7-20261002-satsuma.json)
contains all 195 configurations and the ABC, selection, residual, feedback and
improved-native variants. Every native variant synthesized 190 configurations
with five explicit constraint skips. ABC retained six synthesis timeouts.
The improved native run has 136 unbounded exact-netlist proofs; unresolved
checks and unavailable whole-design native-state timing remain visible.

On the same 23 proven idiomatic headline slots, frequency relative to ABC is
83.04% for selection, 89.46% with residual optimization, 89.43% with feedback,
and 94.73% after wide-sum prefix lowering. The arithmetic iteration improves
native frequency by 5.9% with a 1.36% area increase in the paired geomeans.
The 64-bit AddMop period falls from 1,549.4 ps to 411.6 ps (ABC: 416.4 ps),
with both engines proving the emission. Its native area rises by about 37%.
These are constrained cell timing results; native cost proxies are separate.

The JSON records exact compiler/source hashes, settings, source/SDC/netlist
and Liberty digests, native stage evidence, runtime/RSS and both proof outcomes.
All original observations remain in the append-only ledger. Enriched rows identify
the original row and retained decision report by hash. Runtime observations used
concurrent proof workers. The normalization audit found no ABC gate extraction in
644 structural native emissions. The final integrated compiler passes the
`make usyn` adder smoke with both checkers for both mappers.

### USYN lgcheck and timing follow-up (2026-10-02)

[data/usyn-followup-20261002-satsuma.json](data/usyn-followup-20261002-satsuma.json)
retains the final 195-slot run, fresh independent lgcheck sweeps, all additional
experiments, and full critical-path cell/load evidence for every ratio below 0.8.
Measured ratios without an unbounded exact-netlist proof are provisional and stay
out of headline geomeans. Bounded checks, timeouts and skips remain explicit.

The final direct lgcheck sweep reports 112 unbounded proofs, 41 six-step bounded
checks, 36 timeouts, one inconclusive result and five constraint skips: zero refutes
and zero checker errors. At least one engine gives an unbounded exact-netlist proof
for 136 emissions. These results do not establish equivalence for the unresolved
cases. Separate fresh audits of the previous emissions retain all 195 six-step
outcomes and the 78 unresolved/bounded cases retried at 12 steps, also without
refutes. The deeper audit applies to those previous emissions, not the final ones.
Integrity checks verify all 190 final emissions/model sets and 6,097 source files.
The producer diff exactly matches LiveHD commit `69cba5bdd`; the final normalization
audit retains 161 log digests and zero ABC gate extractions.

Native carry-save products, balanced comparisons of at least eight bits, and
mapping-only cell sizing reduce the below-0.8 population from 42 to 20. Explicit
flattening produces identical netlists on all 20 remaining cases. A separate
`pass.usyn.delay=50` experiment brings eight more above 0.8, with area tradeoffs;
its recipe is never pooled into the default headline. Eleven remaining default
cases have no eligible register-rooted endpoints, exposing the limits of the local
residual optimizer on combinational outputs. Per-case paths and next steps are
retained in the report. On the same 23 proven idiomatic headline slots, the
frequency geomean moves from 94.73% to 95.01% of ABC, with essentially unchanged
paired area. Many larger arithmetic gains remain provisional because their exact
netlist proofs time out; these do not enter that headline.

The Verilog driver now accepts one explicit mapping-delay override through
`LHDTRACK_LHD_SET` and records the actual target. The SDC-derived default is unchanged;
explicit targets change the mapping recipe and its cache key. Conflicting explicit
targets fail. Synthesis and proof use the same retained policy and emission.

Regenerate the report offline, or repeat direct lgcheck while retained work exists:

```bash
python3 tools/report_usyn_followup.py --from-summary data/usyn-followup-20261002-satsuma.json
python3 tools/check_usyn_netlists.py --summary data/usyn-followup-20261002-satsuma.json \
  --variant candidate --jobs 16 --steps 6 --timeout 300 --output var/lgcheck-repeat.json
# A deeper bounded-only check; this does not establish an unbounded proof.
python3 tools/check_usyn_netlists.py --summary data/usyn-followup-20261002-satsuma.json \
  --variant candidate --unresolved-only --bmc-only --steps 12 --timeout 300 \
  --output var/lgcheck-deep.json
```

The audit verifies retained source, emission and model digests before invoking the
manifest's lgcheck against the original Verilog. No ABC Boolean optimization is
added to USYN; ABC supplies optional technology mapping and physical cell sizing.

### Manual ASAP7 synthesis example

After `make toolchain` has staged the single merged ASAP7 Liberty, these two commands
synthesize the Verilog `br_apb_demux_select_onehot` design and emit its mapped gate-level
Verilog. Run them from the repository root:

```bash
var/toolchain/bin/lhd synth \
  --top br_apb_demux_select_onehot \
  --workdir var/manual/br_apb_demux_select_onehot-asap7/W \
  --emit-dir lg:var/manual/br_apb_demux_select_onehot-asap7/netlist \
  --set synth.liberty=var/toolchain/lib/asap7/asap7sc7p5t_RVT_TT_merged.lib \
  --set synth.sdc=tests/br_apb_demux_select_onehot/constraints/asap7.sdc \
  --set abc.delay=100 \
  -- -F tests/br_apb_demux_select_onehot/verilog/filelist.f -DSYNTHESIS

var/toolchain/bin/lhd compile \
  lg:var/manual/br_apb_demux_select_onehot-asap7/netlist \
  --top br_apb_demux_select_onehot.br_apb_demux_select_onehot \
  --emit-dir verilog:var/manual/br_apb_demux_select_onehot-asap7/netv \
  --workdir var/manual/br_apb_demux_select_onehot-asap7/Wemit
```

The first command performs compilation, coloring, ASAP7 mapping, and timing. The second
only emits the mapped `lg:` library as gate-level Verilog. With an `lhd` that supports
direct mapped-Verilog emission, add
`--emit verilog:var/manual/br_apb_demux_select_onehot-asap7/netlist.v` to `lhd synth` and
omit the second command.

---

## data/ and target/

```
data/ledger-<host>.jsonl     COMMITTED. Append-only. The only source of truth.
target/index.html            GENERATED. Machines that have reported.
target/results-syn-<host>.html  GENERATED. Synthesis QoR and STA diagnostics.
target/results-sim-<host>.html  GENERATED. Slop and LLVM simulation comparisons.
target/results-lec-<host>.html  GENERATED. Equivalence coverage and proof timings.
target/timeseries-<host>.html  GENERATED. That machine's history.
target/history-<host>.json   GENERATED. The series behind the chart.
```

`target/` is rebuilt from `data/` on every run. Published HTML snapshots
contain no unique measurements; deleting a page loses no ledger data.

**One ledger file per machine**, not one shared file. Two machines running cron
both append; a single `ledger.jsonl` would conflict on every push — two appends
at the same end of the same file, every night. Splitting by `uname -n` makes
those writes disjoint, so a merge is a fast-forward.

It also matches what the data means: wall clock and peak memory do not travel
between hosts, so `uname -n` is the boundary of what may be compared. Pages are
per-machine for the same reason, and the timeseries plots only that machine's
runs.

## Repository layout

```
lhdtrack/
├── MODULE.bazel                  livehd + yosys-slang + abc + verilator + opensta + Liberty pins
├── BUILD.bazel                   //:sync-toolchain -> var/toolchain/{bin,lib,toolchain.json}
├── pyproject.toml                installs the `lhdtrack` CLI
├── lhdtrack.toml                 defaults: techs, cycle budget, cache policy, gates
│
├── tests/                        THE corpus - one directory per test, identical shape
│   ├── add/
│   ├── br_counter_incr/
│   └── ...
│
├── flows/                        HOW each tool runs; file content is in the cache key
├── src/lhdtrack/                 the runner
├── tools/                        importers and generators
├── tech/                         pinned Liberty, one version per technology
├── packages/                     BUILD files for tools not in the Bazel Central Registry
├── data/                         COMMITTED: ledger-<host>.jsonl, one per machine
├── target/                       GENERATED: every page, re-derived from data/ each run
└── var/                          gitignored, machine-local: toolchain, cache, work, runs
```

### The shape of a test

Every test directory is identical, whatever it was ripped from:

```
tests/<name>/
  design.toml                   manifest: top, kind, configs, flows, status, gates
  README.md                     what it is, upstream repo + rev + path, known gaps
  LICENSE                       the upstream license, carried verbatim
  verilog/                      *.sv *.svh + filelist.f
  pyrope/                       *.prp + manifest.json
  data/                         optional runtime inputs, e.g. firmware images
  sim/  <top>_tb.prp            generated: LFSR stimulus, checksum result
        <top>_tb_verilator.cpp  generated: line-for-line twin of the above
        <top>_harness.sv/.prp   generated: registered wrapper (combinational tops only)
  constraints/  default.sdc     generated: real clock (sequential) or virtual (combinational)
```

Files in a test's `data/` directory are copied into each job's `data/` directory
and included in its cache key. Verilog runtime filenames are relative to the job
working directory; staged Pyrope files can use `../data/<file>`.

Test names are flat and globally unique. Imported names are kept verbatim
(`br_fifo_flops` from bedrock-rtl, `add` from circt-synth-tracker); the `suite`
field in `design.toml` — not the path — drives grouping in the report, so
recategorizing a test never breaks a URL or a cache key.

---

## Equivalence: two obligations

**Every test runs synthesis, simulation AND equivalence.** A tool that cannot
read a design produces a *skip with the tool's own error*, never a missing
category — coverage is a property of the corpus, a tool limitation is a property
of the run, and only one of those belongs in a manifest.

### `pyrope/` ≡ `verilog/` — two backends, one obligation

| flow | engine |
| --- | --- |
| `lec_lgyosys` | **baseline** — `--set formal.solver=lgyosys`, i.e. inou/yosys/lgcheck, the former `lhd check`, reasoning over the cgen-emitted Verilog |
| `lec_lhd` | `--set formal.solver=cvc5`, LiveHD's in-process SMT, reasoning over the LGraph |

Both prove the same thing, which is what makes their **times** comparable
and their **verdicts** checkable against each other. Because the two reason over
different representations, a code-generation bug surfaces as one backend proving
what the other refutes — a discrepancy no single-engine run can find. run.py
flags that as a backend disagreement rather than picking a winner.

**A verdict is not boolean.** `proven` and `refuted` are answers; `timeout`,
`inconclusive`, `unsupported` and `error` are the absence of one. A timeout
exhausted the configured time budget; an inconclusive result returned before
the budget after exhausting the backend's available proof strategies. Neither
is a refutation or a proof. `[run].lec_timeout_s` bounds each obligation so one
hard design cannot stall a nightly.

### sources ≡ synthesized netlist

`lec_netlist` checks both source descriptions against the tech-mapped netlist synthesis produced
— **very different Verilog**: no shared module boundaries, no shared signal
names, registers and memories turned into DFF cells (`dfxtp_1` / `DFFHQx4`)
plus mux logic, combinational logic rewritten by ABC into an unrecognisable
gate structure. Comparing two sources a human wrote is often settled
structurally; this is what actually exercises the equivalence engine.

One mapped design therefore carries three independently named obligations:

| source vs netlist | engine |
| --- | --- |
| Pyrope vs mapped netlist | cvc5 |
| Verilog vs mapped netlist | lgyosys |
| Verilog vs mapped netlist | cvc5 (backend discriminator) |

The third check distinguishes a synthesis error from an lgyosys-only
refutation. A hierarchy mismatch is not a failure: both readers flatten through
unmatched wrappers until they reach comparable machine state and outputs.

It also checks something nothing else does: **that synthesis preserved the
design**. Every area and delay number in the synthesis tables is a claim about a
netlist; this is what says the netlist is still the circuit. A refutation here
is far more serious than two source descriptions differing.

It has already earned its keep. On 2026-08-28 it initially refuted 11 designs,
and those refutations were
**real**: a generate-block instance name collision made
two distinct registers flatten to one hierarchical name, `pass.abc`'s register
read-back disambiguated them on the implementation side only, and the mapped
netlist computed `{credit_initial[5:3], credit_initial[5:3]}` where the RTL gives
`credit_initial` — 56 of 64 input values wrong. Confirmed independently with
iverilog against the PDK's own cell models, then fixed upstream in lhd's slang
reader.

Two things that triage taught, both worth keeping:

- **`pass.abc.register=false` proving what `register=true` refutes does NOT mean
  the netlist is fine.** Both settings emit different netlists, so one proving
  says nothing about the other. That inference cost an hour and pointed at the
  wrong component.
- **Get an oracle that shares nothing with the tool under test.** `lhd lec --set
  formal.solver=lgyosys --lib lg:models` is not one (the Liberty models never
  reach the yosys leg, so a netlist refutes against itself), and yosys cannot
  read the PDK's UDP-based models at all. iverilog can, and that is what settled
  it.

The last apparent refutation, `br_tracker_linked_list_ctrl`, exposed two more
independent defects. Its hand-written Pyrope shift/rotate network computed the
last mux but never stored it into the final packed-array stage. The direct Slang
reader repeated the same mistake because symbol-granularity dependency analysis
put a module-level output read before generated partial writers. Both are now
fixed and cvc5 proves Pyrope-vs-Verilog and both source-vs-netlist obligations
for ASAP7 and sky130. lgyosys exhausts its quick proof strategy on this design
and is reported as `inconclusive`, not refuted.

`br_amba_axi_demux` exposes the remaining cvc5 limitation in the flat-netlist
matrix: bit-disjoint combinational feedback packed into one vector. Each fork
valid bit deliberately excludes its corresponding ready bit, so the bit-level
graph is acyclic; LiveHD's word-level dependency graph merges the lanes and
reports a false SCC through the downstream demux. The hierarchical source check
can cut at module interfaces, but after hierarchy is collapsed against a flat
mapped netlist the cvc5 encoder requires an acyclic term graph and refuses the
word-level SCC (`operand ... has no encodable driver`). This is `unsupported`,
not a timeout; raising the budget cannot help.

ASAP7 uses the staged merged Liberty for this obligation, so both technologies
are measured.

The mapped cells need behavioural models — `lhd pass liberty gensim` generates
them from the same Liberty the netlist was mapped against. Without them every
standard cell is an opaque black box and the proof degrades to UNKNOWN, which
would look like a solver limitation rather than a missing input.

The netlist checked here is the SAME kind the synthesis rows measure: `pass abc`
runs with the synth flows' knobs (`memory=true`, `register_max_bits=0`,
`flatten=true`) and the SDC-derived `delay`, so a verdict covers the netlist
whose area and delay the report quotes rather than a differently mapped sibling.

The two obligations are reported in **separate sections** and never averaged
together: a test whose Pyrope is refuted against its Verilog can still have its
netlist proven against that same Verilog, which correctly says "synthesis is
sound, the source rewrite is not".

A definitive verdict is **written back** into `design.toml` when `status.lec` was
`"none"` — unknown, so filling it in is strictly an improvement, and without it
nothing ever becomes comparable since the headline gate reads the manifest. An
explicit claim is never overwritten: a manifest saying `proven` while a solver
says `refuted` stays a failure.

## The three-flow rule

Both LiveHD front ends run, always. This is the single most important design decision
in the tracker.

```
verilog/  ──▶ yosys + slang + abc -liberty ─┐
verilog/  ──▶ lhd compile verilog + synth  ─┼──▶  mapped .v  ──▶  qor_endpoint
pyrope/   ──▶ lhd synth                    ─┘                     ├ area, cells, flops
                                                                  ├ OpenSTA     → wns, path
                                                                  └ lhd OpenTimer → max_delay
```

- `lhd_verilog` ↔ `lhd_pyrope` isolates **Pyrope vs Verilog as a language** — identical backend.
- `yosys_abc` ↔ `lhd_verilog` isolates **LiveHD vs yosys+slang/abc as a tool** — identical source.

With only `yosys_abc` and `lhd_pyrope` every delta is confounded and no daily movement can
be attributed to anything. The same applies on the simulation side (`verilator`,
`sim_lhd_verilog`, `sim_lhd_pyrope`).

Every mapped netlist — whoever produced it — lands on the **same Liberty, same SDC, and the
same two timing engines**, so area and delay are comparable by construction rather than by
hope.

---

## The two timers must have seen the same circuit

`lhd pass opentimer` runs over the **raw** `lg:` netlist. That netlist is now
structural (the flows ask for `pass.abc.memory=true` and `register_max_bits=0`),
so the two timers see the same registers; they still differ when

- **(a)** `pass color synth` produced more than one region — `flatten=true` then
  emits a wrapper plus `__c<n>` modules, and OpenTimer cuts the wrapper's native
  glue into zero-arrival boundaries (`native-comb-boundary` in its diagnostics;
  `br_amba_axi_shrinker` read OpenTimer 358 ps against OpenSTA 1676 ps that
  way), or
- **(b)** a memory was refused by mem_lower and stays behavioural, so OpenTimer
  never saw the logic normalization mapped for OpenSTA.

`qor_endpoint` computes `delta_pct` only when neither holds and otherwise
records `delta_note`. On a combinational design the two agree closely (`add`:
OpenTimer 1.8893 ns vs OpenSTA 1.8731 ns, **0.86% apart**); on a structural
sequential one normalization changes nothing but the odd duplicate flop yosys
merges, recorded as `merged_flops` (`br_fifo_flops`: 1111 → 1110). Blaming a
78% timer disagreement on the timer would have been a confident wrong answer.

## Timing fidelity is a first-class column

`qor_endpoint` times every netlist twice: once with LiveHD's built-in OpenTimer
(`lhd pass opentimer`) and once with OpenSTA, on the same netlist, the same `.lib` and the
same `.sdc`. It reports `opentimer_ns`, `opensta_ns` and `delta_pct`.

This is independent of which flow produced the netlist, so a LiveHD timing bug shows up as a
column that turns red across many tests rather than as a QoR number that is quietly wrong.
`lhdtrack.toml` sets the threshold past which a disagreement fails the test.

---

## Synthesis runs bare, simulation runs wrapped

Synthesis reads the **bare top** from `verilog/` and `pyrope/`. Simulation reads the
**harness** in `sim/`, which instantiates that bare top.

Keeping them separate matters: the registered wrapper that makes a combinational block
clockable would otherwise show up as wrapper flops in that block's area number.

This also settles constraint generation, since `tools/gen_sdc.py` has exactly two shapes:

- sequential top → `create_clock` on the detected clock port
- combinational top → a virtual clock plus `set_input_delay` / `set_output_delay`, which is
  what OpenSTA needs before it will report an input-to-output path

---

## Testbenches are generated, not ported

`tools/gen_testbench.py` emits a matched pair from the top's port list:
`sim/<top>_tb.prp` and `sim/<top>_tb_verilator.cpp`. Both drive every input from the same
LFSR and fold every output into the same checksum over N cycles.

For a QoR and throughput tracker the testbench has exactly two jobs: drive **identical work**
into both simulators, and catch a miscompile. It does both — all three simulators must print
the same checksum or the test fails, which makes the testbench a cross-simulator oracle.
Functional correctness is the LEC gate's job, not the testbench's.

`cycles` is tuned per test so each simulation lands in the 2-10 s window. No VCD is written
on any side (a tracer in the measured interval turns a simulation benchmark into a filesystem
benchmark), and the exec leg is best-of-3.

---

## The Pyrope side: `auto` then `idiomatic`

Importing a test seeds `pyrope/` with `lhd compile verilog --emit-dir pyrope:` and records
`status.pyrope = "auto"`. The test is runnable the day it lands.

Machine-emitted Pyrope enters the same LGraph the Verilog does, so an `auto` row measures
the two **front ends**, not the two **languages**. The report keeps the two populations
apart and the headline geomean aggregates `idiomatic` rows only. Rewriting a block by hand
and flipping it to `status.pyrope = "idiomatic"` is what promotes it into the real
comparison.

`status.lec` gates whether a test's numbers are *compared* or merely *reported*: until
`lhd lec --impl pyrope --ref verilog` proves the two sides equivalent, a QoR win might just
be a different circuit.

---

## Tooling: Bazel-native, no Docker

`bazel run //:sync-toolchain` builds everything from source and lays down a symlink farm:

```
var/toolchain/bin/{lhd, yosys, yosys_slang, abc, verilator, sta}
var/toolchain/lib/{sky130/*.lib, asap7/*.lib}
var/toolchain/toolchain.json      every version string and file hash
```

The runner resolves tools **only** from `toolchain.json`, never from `PATH`. That is what
makes the cache key trustworthy — a stray Homebrew yosys cannot silently redefine a baseline.

`bazel_dep(livehd)` brings `lhd` and OpenTimer. lhdtrack pins stock Yosys and ABC,
builds the yosys-slang plugin against that exact Yosys ABI, and also pins Verilator,
OpenSTA, and both technology libraries.

Everything runs natively on macOS and Linux, so wall-clock and peak-RSS numbers are real on
both. Nothing in the measured path goes through a container.

### Technology

Two technologies, one pinned version each, on every test:

| tech | Liberty | time unit | pinned in |
| --- | --- | --- | --- |
| `sky130` | `sky130_fd_sc_hd__tt_025C_1v80` | 1 ns | `tech/sky130/` |
| `asap7` | `asap7sc7p5t_{AO,INVBUF,OA,SEQ,SIMPLE}_RVT_TT` | 1 ps | `tech/asap7/` |

**Every delay is in the library's own time unit**, and the unit travels with the
number: it is read from the Liberty at staging time, stored in `toolchain.json`,
recorded on every row as `sta.time_unit`, and used to label the report's delay
column (`delay ps` for ASAP7, `delay ns` for sky130). Converting ASAP7 to
nanoseconds would print 0.154 where both the tool and the SDC say 154; labelling
it "ns" without converting made 7nm look 60x slower than 130nm. `lhdtrack check`
cross-checks the unit declared in `lhdtrack.toml` against the one the Liberty
states, and `qor_endpoint` refuses to correlate the two timers when lhd's
declared unit disagrees with the library's.

Measured on this corpus, ASAP7 comes out **6-15x faster** than sky130, which is
the sanity check that the scaling is right.

**Constraints are per technology**, `tests/<name>/constraints/<tech>.sdc`, for
the same reason. One shared `period = 10.0` means 10 ns on sky130 and 10 ps on
ASAP7, and the 7nm run comes back with -73 ns of slack for a reason that is
about units rather than about the design.

Synthesis uses those periods too. `tools/retarget_sdc.py` selects, per design,
the closest still-unmet ASAP7 target from 100/200/300/400 ps using the fastest
current Yosys/LiveHD-Verilog OpenSTA result. Sky130 uses a shared loose 20 ns
(50 MHz) target. The flows convert the SDC period to picoseconds for ABC's
`-D`/`abc.delay`; OpenSTA continues to read the same SDC in the Liberty's native
unit, so mapping and signoff cannot silently target different clocks.

**Reset is a false path.** It arrives from a top-level port with no logic in
front of it and fans out to every register, so on a design with a short datapath
it wins the critical path with a number describing the testbench boundary rather
than the design. On `br_delay` it was **44% of LiveHD's reported critical path**
(1.545 ns → 0.826 ns once excluded), hiding the register-to-register path the
benchmark exists to measure. In silicon a synchronous reset *is* a real path; it
is excluded here because this corpus compares datapath quality and a reset tree
is a global-routing problem solved after synthesis. A test that needs it timed
checks in its own constraints file.

**The I/O budget is zero**, also deliberately. An external delay is part of the
arrival time OpenSTA reports but not of the path delay LiveHD's OpenTimer
reports, so any non-zero budget shows up as pure disagreement between the two
timers — a 20% budget made them look 98% apart on `br_delay`, whose worst path
is a zero-logic feedthrough, so every nanosecond of the "critical path" was the
budget itself.

ASAP7 is published as five cell-family Liberty files. The toolchain merges those
families deterministically into one complete library, because both Yosys ABC mapping
and LiveHD's `pass.abc.library` accept one Liberty path.

Note that circt-synth-tracker's "ASAP7" and "sky130" are mockturtle *genlib* abstractions
embedded into its AIG judge, not Liberty. Genlib cannot be read by OpenSTA and yields no
real area or delay, so lhdtrack uses real Liberty throughout.

---

## Caching baselines

```
key = sha256( test sources + config params + flow recipe file
            + tool version (from toolchain.json) + Liberty hash + host class )
```

`yosys_abc` and `verilator` read through `var/cache/`. LiveHD flows never do — the lhd SHA
moves daily, so they would always miss anyway, and re-measuring them is the entire point.

A baseline is re-measured automatically when the test changes, a tool version bumps, a flow
recipe is edited, the Liberty changes, or the host changes. Force it with
`lhdtrack run --refresh <flow>`.

Every cached row carries `measured`, the date it was actually taken, and the report displays
it. Without that, a six-week-old verilator number silently becomes today's comparison point
with nothing on the page saying so.

**Host identity is part of the key.** A number from another machine is not evidence; the
report refuses to plot a series across a host or Liberty change.

---

## Ledger row

One JSON object per (date, test, config, tech, flow), appended to
`data/ledger-<host>.jsonl`:

```json
{"date":"2026-08-26","host":"masc-a","run_id":"20260826T0300Z","cached":false,
 "measured":"2026-08-26","test":"br_fifo_flops","config":"d16w32","suite":"rtl",
 "tech":"sky130","flow":"lhd_pyrope","pyrope_status":"idiomatic","lec":"proven",
 "passed":true,
 "versions":{"lhd":"<sha>","yosys":"0.64","abc":"0.64-yosyshq","verilator":"5.046",
             "opensta":"<sha>","liberty":"sky130_fd_sc_hd__tt_025C_1v80@<rev>"},
 "qor":{"cells":1234,"flops":320,"area_um2":9876.5},
 "sta":{"opentimer_ns":1.23,"opensta_ns":1.27,"delta_pct":3.3,"wns_ns":-0.07},
 "time_ms":{"elab":120,"synth":840,"map":300,"sta":210,"total":1470},
 "peak_rss_kb":{"elab":180000,"synth":920000,"max":920000}}
```

Peak RSS is recorded **per stage**, not per run: a whole-run number hides which stage is the
hog, which is the only thing the number is useful for.

---

## The report

`make report` rebuilds the host pages from `data/ledger-<host>.jsonl` and the
selected evaluation specifications. Results are separated by domain and host:

- **`target/results-syn-<host>.html`** — synthesis QoR, mapper comparisons and
  synthesis diagnostics. A selected Verilog evaluation retains its recorded
  baseline and exact-netlist proof gates. Other technologies and Pyrope synthesis
  remain in the linked `results-syn-<host>-full.html` page.
- **`target/results-sim-<host>.html`** — Verilator, LHD Verilog Slop and LHD Pyrope
  Slop retain their setup / C++ compilation / execution / throughput columns.
  Two extra columns show **LLVM execution speed relative to Slop**, one per
  source language: Slop execution time divided by LLVM execution time, so above
  1× means LLVM helps. LLVM setup and compilation do not contribute to these
  ratios. Pairs require matching cycles, checksums, host, LiveHD version and run;
  failed or unmatched measurements stay visible without entering the comparison.
  This backend comparison includes all Pyrope source styles because it compares
  the same source through two backends. Exact-netlist simulation checks have
  their own section below the source-simulation results.
- **`target/results-lec-<host>.html`** — equivalence verdicts, proof timings and
  coverage plots. Bounded proofs, timeouts, skipped and missing checks remain
  distinct. Language equivalence and emitted-netlist obligations stay separate.
- **`target/timeseries-<host>.html`** and **`target/history-<host>.json`** — history
  and the series behind its charts.

All result pages link to each other and the machine index. Ratio charts offer
a compact overview, a per-test view, metric switches, hover details and a
test/config filter; tables remain sortable.

The existing `sim_lhd_verilog` and `sim_lhd_pyrope` flows explicitly select Slop.
The planner also schedules their `_llvm` counterparts when those language flows
are declared. `--flow` can select any individual backend. Both backends use the
same startup policy and cycle counts, with profiling off and one compiler job
per flow. Execution is measured best of three, separately from setup and host
compilation; all measurements remain gated against the recorded checksum.
To refresh the complete simulation matrix without synthesis or LEC reruns:

```sh
lhdtrack run --no-cache --flow sim_verilator \
  --flow sim_lhd_verilog --flow sim_lhd_pyrope \
  --flow sim_lhd_verilog_llvm --flow sim_lhd_pyrope_llvm
```

`auto` and `idiomatic` Pyrope are aggregated separately everywhere.

**Every table sorts by column.** Click a header — `area` under `lhd·pyrope`, `error` in the
STA table, `s` next to a LEC verdict — and the rows reorder by it; click again for
descending, a third time to get the generated order back. The header carries the state
(`↕` idle, `↑`/`↓` active) and headers are focusable, so Tab and Enter work too.

Three details are load-bearing, because the tables are not rectangles of cells:

- A **skipped flow collapses five columns into one `<td colspan="5">`**, so the sorter
  resolves the table's real column grid instead of indexing `row.cells[i]` — which would
  read the wrong column for exactly the rows a reader is checking.
- A **badge is not a value.** `246.49` with a `norm` or `cached` chip beside it is still
  246.49; without skipping the chips the whole area column would be typed as text and
  `246.49` would sort above `60.06`.
- **A blank sorts last in both directions**, and the geomean `<tfoot>` never moves. An
  absent measurement is not a very small one, and parking un-run flows at the top of an
  ascending "time s" column would read as though they had been the fastest.

---

## The corpus

172 tests, imported from two upstreams and kept independent of them — sources
copied in, transitive dependencies resolved, licence carried verbatim, exact
revision recorded.

| upstream | tests | notes |
| --- | ---: | --- |
| [circt-synth-tracker](https://github.com/uenoku/circt-synth-tracker) | 9 | combinational microbenchmarks. `DatapathBench` and `ELAU` are empty submodules upstream, so they cannot be imported yet |
| [bedrock-rtl](https://github.com/xlsynth/bedrock-rtl) | 163 | sequential library blocks, at the parameter sets bedrock's own `PPA.md` measures |

**Dependencies come from bedrock's `BUILD.bazel` graph**, not from guessing. A
bedrock module rarely stands alone: it imports a package or instantiates
siblings, and copying only its own `.sv` fails the moment anything elaborates it
("Non-constant range in declaration"). The importer parses every
`verilog_library` rule, walks the transitive closure, and writes `filelist.f` in
dependency order — `br_fifo_flops` pulls 26 sources.

**Port widths come from verilator**, with yosys as fallback and a regex scan that
is never silently accepted. Both real front ends elaborate, so both resolve a
width that is a parameter expression — but verilator's SystemVerilog support is
much stronger, and this corpus is full of designs yosys cannot read
(`br_math_pkg` defeats its parser outright). 26 tests elaborate under neither;
those have their synthesis and simulation flows disabled in the manifest with
the reason recorded, and still contribute an equivalence result.

**No top module takes a parameter.** Upstream tops are parameterized; the
importer's job is to choose one point and then stop having a choice. Five
consumers spell an override differently —

| consumer | how it would say `NumRequesters = 16` |
| --- | --- |
| yosys | `hierarchy -chparam NumRequesters 16` |
| slang / `lhd` | `-GNumRequesters=16` |
| verilator | `-GNumRequesters=16` |
| generated harness | `br_arb_fixed #(.NumRequesters(16)) dut` |
| Pyrope `.prp` | *cannot say it at all* |

— so any one of them drifting makes LEC compare two different circuits and
report a refutation that says nothing about either language. `tools/monomorphize.py`
rewrites the top's parameter port list to `localparam` at the point `design.toml`
had chosen, empties `[[config]].params`, and drops the `#(...)` from the harness.
The result elaborates identically with no flags anywhere, and the `.prp`'s fixed
widths match by construction rather than by agreement. Intermediate modules keep
their parameters — the top overrides them, which is what makes them reusable.

`lhdtrack check` fails a top that regains an overridable parameter, because a
re-import silently reintroduces one. The `[[config]].id` still names the point
(`br_arb_fixed#nr16`); the `.sv` is where it is now stated.

## Adding a test

```bash
lhdtrack new my_block --top my_block --kind sequential
# drop SystemVerilog into tests/my_block/verilog/, then:
lhdtrack import seed my_block      # pyrope seed, testbench pair, harness, SDC
lhdtrack check my_block
lhdtrack run --test my_block
```

Or import in bulk from a pinned upstream:

```bash
tools/import_circt_tracker.py --list
tools/import_circt_tracker.py add alu barrel_shifter
tools/import_bedrock.py add br_fifo_flops --config d4w8 --config d16w32
```

Importers copy the upstream `LICENSE` verbatim and record repo, revision and source path in
both `README.md` and `design.toml`'s `[provenance]` block.

---

## Measured LiveHD constraints

Recorded from the first real runs (2026-08-26, `lhd 0.1.0`) so they are not
rediscovered one test at a time:

| | |
| --- | --- |
| `int(min=,max=)` type | **removed** — use sized `uN` / `sN` |
| `int(x)` cast | **removed** — use `uN(x)`, `signed(x)`, `unsigned(x)` |
| `u64(x)` cast | not a built-in cast; unnecessary anyway (Pyrope ints are unbounded) |
| array size | must be a **literal**; a named comptime constant does not fold |
| `mut a:[] = nil` + `++=` | rejected — an array needs a sized element type at declaration |
| module import | `import("file.entity")`; `import("file")` yields a value that cannot be called |
| netlist emission | no `pass cgen`; feed the `lg:` library back through `lhd compile --emit-dir verilog:` |
| emitted netlist | **structural** when `pass.abc.memory=true register_max_bits=0` — combinational cells, `DFF*` cells for flops and bit-blasted memories; behavioural only for memories mem_lower refuses (ROM with init, whole-array update/reset, negedge, type==2, read_all) and for `memory=false` |
| memories, native flops | `pass.abc.memory` defaults to **false** (memories stay behavioural); `register_max_bits` defaults to **4096** and silently keeps a bigger region's flops native — `br_ram_flops` (5808 bits) then falls back to yosys `dfflibmap+abc` with no `-D`: 906 ps against 360 ps mapped by lhd (ASAP7) |
| Liberty, one-shot | `--set synth.liberty=PATH` (it sets both `pass.abc` and `pass.opentimer`) |
| Liberty, manual pass | `--set pass.abc.library=PATH` — the one-shot **rejects** this spelling |
| Liberty default | `$HAGENT_TECH_DIR/sky130_...` — **not** whatever the caller asked for |
| multi-family tech | `pass.abc.library` is one file; the toolchain merges ASAP7's five families first |

Two of these shape the flows directly.

**Normalization is a safety net, not the register implementation.** LiveHD's
flows map flops and memories themselves; `qor_endpoint._normalize` still runs
yosys `proc` → `memory_map` → `techmap` → `dfflibmap` → `abc` so that whatever a
flow left behavioural (a refused memory, a `memory=false` run) is mapped and
**one counter (`yosys stat`) and one timer (OpenSTA) serve all three flows**.
Cells already read from the Liberty are blackboxes, so the logic LiveHD mapped
is a boundary `abc` does not re-optimize. On a fully structural netlist it only
merges duplicate flops (`opt -fast`) and drops the `_const0_/_const1_` models;
the row records `merged_flops`. Rows where a memory stayed behavioural are
tagged `norm`.

**The Liberty default is a trap.** `pass.abc.library` falls back to
`$HAGENT_TECH_DIR`, so a run asking for ASAP7 silently mapped sky130 and only
failed later when normalization could not find sky130 cells in the ASAP7
library. Both LiveHD flows now pass the Liberty explicitly, and a technology
with more than one cell family **skips with a note** rather than measuring the
wrong library.

## Related repositories

| repo | relationship |
| --- | --- |
| [`livehd`](../livehd) | the tool under test; supplies `lhd` and OpenTimer as a Bazel dep |
| [`lhdsuite`](../lhdsuite) | large cores, incremental flows. Disjoint from lhdtrack by design |
| [`circt-synth-tracker`](../circt-synth-tracker) | the report model; source of the combinational suite |
| [`bedrock-rtl`](../bedrock-rtl) | source of the sequential suite, and of its PPA parameter sets |
| [`HighTide`](https://github.com/VLSIDA/HighTide) | the hermetic-Bazel-tooling model |

### Verilog-only ABC/USYN evaluation

`python3 tools/run_verilog_eval.py --jobs 4` measures the current staged LiveHD
against the retained ASAP7 Yosys synthesis baseline. It runs Verilog through
`abc` and `usyn`, retains each emitted netlist, then checks its exact bytes with
cvc5 and the Yosys cross-check. Timeouts are coverage outcomes, not refutations.
`--test add` creates a separate smoke report; `--resume RUN_ID` finishes an
interrupted run using the same toolchain. No Pyrope or simulation flow runs.

Completed results append to the existing ledger immediately. The host report
refreshes about every 30 seconds while results arrive. A
`data/verilog-eval-<host>.json` selection records the fresh run and the retained
baseline row identities, so `make report` reproduces this focused view without
reviving old LiveHD values. Historical measurements remain in the ledger.

`--lec-from RUN_ID` reruns only verification with the currently staged LiveHD,
using the exact retained netlists from that synthesis run. The report records
both binaries and run IDs. Bounded proofs count as proven and display their
bound; the cvc5 and lgcheck fallback bounds are both six checked steps.

Synthesis footers compare each metric with Yosys+Slang+ABC using the geometric
mean of baseline / LiveHD (>1 is better). Unverified rows remain included;
refuted netlists are excluded. Each column reports its matched sample count,
omitting missing, zero, or non-finite measurements.

Verilog evaluations run cvc5 once, followed directly by independent `lgcheck`
on the original RTL and retained netlist, with the same Liberty-derived cell
models. Both use a six-step bound. Use `--jobs 4 --lec-jobs 32` to retain
synthesis measurement concurrency while running proofs in parallel.

The evaluation also audits independent-oracle coverage. Older runs used the
`lhd` cross-check command, which repeats the native proof before invoking
`lgcheck`; its outer watchdog can expire without reaching Yosys. The audit
finishes those missed checks directly. Native results remain unchanged;
replacement oracle results are appended with input hashes, logs, and timing
provenance. Previous ledger entries are retained. For a live sweep started
with an older driver, run
`python3 tools/recheck_missing_oracles.py --run RUN --watch`.

A LEC-only rerun may consume retained netlists from another host. New proof
measurements and the report belong to the current host; synthesis timings and
baselines from the source host are never imported. For example:

```sh
python3 tools/run_verilog_eval.py --lec-from 20260925T111208 --reemit --lec-jobs 32
```

Stage the current binaries and matching Liberty in `var/toolchain/toolchain.json`
first. The optional `lgcheck` and `yosys2` entries select LiveHD's independent
checker explicitly; otherwise they come from the staged LiveHD runfiles.
The report records the source synthesis host/run and hashes each checked
netlist. `--reemit` accepts only transparent instance renaming, not logic changes.

The evaluation defaults to both `pass.satopt=true` and `pass.satopt=false`
profiles, for synthesis and for emitted-netlist LEC. Use `--satopt-profiles enabled`
or `--satopt-profiles disabled` to select just one. Synthesis without satopt
(`syn_lhd_verilog[_usyn]_no_satopt`) gets its own table; a "SAT optimization
effect" table then pairs each design's two profiles (satopt=false ÷ satopt=true,
>1 means satopt helps) for QoR, synthesis time, memory, and native LEC time.
Each profile checks its own retained netlist bytes with native LEC and
independent lgcheck; neither inherits the other profile's proof. Liberty model
generation and Verilog export are shared setup outside both timers. Timed
commands include input loading, compilation and proof; native phase timings
are retained in the ledger and shown in the time-cell tooltip. The frontend
is given the selected top explicitly so unused cell models are not elaborated.

`python3 tools/run_host_refresh.py --jobs 4` refreshes all declared simulator
flows and Sky130 synthesis on the current host, including fresh Verilator and
Yosys baselines. It publishes completed, checksum-gated design/config groups
incrementally to `target/results-syn-<host>-full.html`, linked from the result pages.
Use `--test add` for a smoke run or `--resume RUN_ID` after interruption; a
resume requires the same staged toolchain.

Simulation recipes pass runtime application arguments as `+name=value` (for
example `+cycles=10000000`) to both LiveHD and Verilator drivers. Pyrope test
signature defaults still apply when an argument is absent. Generated Verilator
drivers retain `--cycles N` as a compatibility alias. Migrating these recipe and
harness files changes their source hashes, so existing measurements remain in
the ledger but new runs use new cache entries.
