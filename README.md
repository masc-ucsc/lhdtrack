# lhdtrack

A daily quality-of-results regression for [LiveHD](https://github.com/masc-ucsc/livehd),
tracking **Pyrope vs Verilog** and **LiveHD vs the open-source reference flow** across
synthesis and simulation.

It answers four questions every day, per test:

| | question | how |
| --- | --- | --- |
| **QoR** | is LiveHD's synthesis competitive? | area / cells / delay / WNS vs `yosys + abc` |
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

---

## data/ and target/

```
data/ledger-<host>.jsonl     COMMITTED. Append-only. The only source of truth.
target/index.html            GENERATED. Machines that have reported.
target/report-<host>.html    GENERATED. One machine's latest run.
target/timeseries-<host>.html  GENERATED. That machine's history.
target/history-<host>.json   GENERATED. The series behind the chart.
```

`target/` is gitignored and rebuilt from `data/` on every run, so nothing in it
is worth committing or worth resolving a conflict over. Deleting it loses
nothing.

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
├── MODULE.bazel                  livehd + yosys + abc + verilator + opensta + Liberty pins
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
  sim/  <top>_tb.prp            generated: LFSR stimulus, checksum result
        <top>_tb_verilator.cpp  generated: line-for-line twin of the above
        <top>_harness.sv/.prp   generated: registered wrapper (combinational tops only)
  constraints/  default.sdc     generated: real clock (sequential) or virtual (combinational)
```

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

**A verdict is three-state.** `proven` and `refuted` are answers; `timeout`,
`unsupported` and `error` are the absence of one. Collapsing a timeout into
"fail" reports a design as wrong when the solver merely gave up; collapsing it
into "pass" is far worse. `[run].lec_timeout_s` bounds each obligation so one
hard design cannot stall a nightly.

### `verilog/` ≡ its own synthesized netlist

`lec_netlist` proves the RTL against the tech-mapped netlist synthesis produced
— **very different Verilog**: no shared module boundaries, no shared signal
names, registers turned into `sky130_fd_sc_hd__dfxtp_1` instances, combinational
logic rewritten by ABC into an unrecognisable gate structure. Comparing two
sources a human wrote is often settled structurally; this is what actually
exercises the equivalence engine.

It also checks something nothing else does: **that synthesis preserved the
design**. Every area and delay number in the synthesis tables is a claim about a
netlist; this is what says the netlist is still the circuit. A refutation here
is far more serious than two source descriptions differing.

The mapped cells need behavioural models — `lhd pass liberty gensim` generates
them from the same Liberty the netlist was mapped against. Without them every
standard cell is an opaque black box and the proof degrades to UNKNOWN, which
would look like a solver limitation rather than a missing input.

The two obligations are reported in **separate sections** and never averaged
together: `br_delay`'s Pyrope is refuted against its Verilog while its netlist
is proven against that same Verilog, which correctly says "synthesis is sound,
the Pyrope rewrite is not".

A definitive verdict is **written back** into `design.toml` when `status.lec` was
`"none"` — unknown, so filling it in is strictly an improvement, and without it
nothing ever becomes comparable since the headline gate reads the manifest. An
explicit claim is never overwritten: a manifest saying `proven` while a solver
says `refuted` stays a failure.

## The three-flow rule

Both LiveHD front ends run, always. This is the single most important design decision
in the tracker.

```
verilog/  ──▶ yosys synth + abc -liberty   ─┐
verilog/  ──▶ lhd compile verilog + synth  ─┼──▶  mapped .v  ──▶  qor_endpoint
pyrope/   ──▶ lhd synth                    ─┘                     ├ area, cells, flops
                                                                  ├ OpenSTA     → wns, path
                                                                  └ lhd OpenTimer → max_delay
```

- `lhd_verilog` ↔ `lhd_pyrope` isolates **Pyrope vs Verilog as a language** — identical backend.
- `yosys_abc` ↔ `lhd_verilog` isolates **LiveHD vs yosys/abc as a tool** — identical source.

With only `yosys_abc` and `lhd_pyrope` every delta is confounded and no daily movement can
be attributed to anything. The same applies on the simulation side (`verilator`,
`sim_lhd_verilog`, `sim_lhd_pyrope`).

Every mapped netlist — whoever produced it — lands on the **same Liberty, same SDC, and the
same two timing engines**, so area and delay are comparable by construction rather than by
hope.

---

## The two timers must have seen the same circuit

`lhd pass opentimer` runs over the **raw** `lg:` netlist, whose registers are
still behavioural — so LiveHD's own timer never sees them. OpenSTA runs over
the **normalized** netlist, where those registers are real cells.

On a combinational design normalization adds nothing and the two agree closely
(`add`: OpenTimer 1.8893 ns vs OpenSTA 1.8731 ns, **0.86% apart**). On a design
that is mostly registers they are not timing the same circuit at all:
`br_delay`'s raw netlist contains **zero** flip-flops, so every endpoint
OpenTimer reports is a single clock buffer and its "critical path" is 0.052 ns
against 0.826 ns for the same design with its registers present.

That is a netlist difference, not a timer error. `qor_endpoint` therefore
computes the correlation **only when normalization did not change the register
count**, and otherwise records `delta_note` naming what each timer saw. Blaming
a 97% timer disagreement on the timer would have been a confident wrong answer.

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
var/toolchain/bin/{lhd, yosys, abc, verilator, sta}
var/toolchain/lib/{sky130/*.lib, asap7/*.lib}
var/toolchain/toolchain.json      every version string and file hash
```

The runner resolves tools **only** from `toolchain.json`, never from `PATH`. That is what
makes the cache key trustworthy — a stray Homebrew yosys cannot silently redefine a baseline.

`bazel_dep(livehd)` already brings `lhd` and OpenTimer, so lhdtrack pins only yosys, abc,
verilator (all three from the Bazel Central Registry), OpenSTA (built from source; it is
the awkward one — TCL, CUDD, Eigen, SWIG), and the two Liberty archives.

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

Note also that every yosys invocation passes **all** Liberty files
(`-liberty a -liberty b …`). sky130 ships one file so the distinction is
invisible there; ASAP7's `liberty[0]` is the AND-OR family, which contains no
flops, and `dfflibmap` died with "D flip-flops are not supported".

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

`lhdtrack report` renders from `site/ledger.jsonl` alone — the HTML is a pure rendering of
the ledger and is regenerated on every run, so a measured regression is visible rather than
silently retried.

- **`site/report.html`** — tool versions, tech, host and date in the header; one synthesis
  table per suite (rows = tests; column groups `yosys+abc` │ `lhd·verilog` │ `lhd·pyrope`,
  each with area / delay / time / peak-mem plus ratio-to-baseline, geomean footer); a
  simulation table in the same shape; the OpenTimer↔OpenSTA correlation column; cached rows
  visibly dated.
- **`site/timeseries.html`** — geomean trends per flow, segmented at any host or Liberty
  change.
- **`site/history.json`** — the raw series behind the page.

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
| emitted netlist | **hybrid** — mapped combinational cells, but flops stay behavioural `always @(posedge clk)` |
| Liberty, one-shot | `--set synth.liberty=PATH` (it sets both `pass.abc` and `pass.opentimer`) |
| Liberty, manual pass | `--set pass.abc.library=PATH` — the one-shot **rejects** this spelling |
| Liberty default | `$HAGENT_TECH_DIR/sky130_...` — **not** whatever the caller asked for |
| multi-family tech | `pass.abc.library` is a single file, so ASAP7's five families cannot be expressed |

Two of these shape the flows directly.

**The hybrid netlist is normalized before it is measured.** A LiveHD netlist is
passed through yosys `proc` → `techmap` → `dfflibmap` → `abc`, which maps only
what LiveHD left behavioural: cells already read from the Liberty are
blackboxes, so the combinational logic LiveHD mapped is a boundary `abc` does
not re-optimize. The result is structural, which means **one counter
(`yosys stat`) and one timer (OpenSTA) for all three flows**. What normalization
adds is the register implementation LiveHD did not map itself — which the yosys
baseline already counts, so this is what makes them comparable rather than what
makes them differ. Such rows are tagged `norm` in the report.

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
