# br_delay

A fixed-latency delay line: `NumStages` pipeline registers, reset to
`InitValue`, with every intermediate stage also exposed on `out_stages`. The
smallest genuinely *sequential* block in the corpus, and the one that keeps the
synthesis track honest about flops — its cell count is dominated by registers
rather than by combinational logic.

| | |
| --- | --- |
| top | `br_delay` |
| kind | sequential |
| suite | rtl |
| upstream | [bedrock-rtl](https://github.com/xlsynth/bedrock-rtl) |
| revision | `0a990f222cd970fdaec42c6e0a4e372cf0e1400f` |
| upstream path | `delay/rtl/br_delay.sv` |
| licence | Apache-2.0 (see `LICENSE`) |

## Imported macro headers

`br_delay.sv` `` `include ``s `br_registers.svh` and `br_asserts_internal.svh`,
so those travel with it in `verilog/`. slang resolves an include relative to the
including file, but verilator only searches its `-I` list — keeping the headers
beside the source is what makes both front ends see the same files, which is the
premise of the whole comparison.

Assertions are inert here: every flow compiles with `-DSYNTHESIS` and without
`BR_ASSERT_ON`, so `BR_ASSERT_STATIC` / `BR_ASSERT_IMPL` expand to nothing.

## Known gaps

- **All three simulators disagree** (first measured 2026-08-26). `verilator`,
  `lhd sim` on the Verilog, and `lhd sim` on the Pyrope each fold a different
  checksum, so the simulation row fails its gate:

  | flow | checksum |
  | --- | --- |
  | `sim_verilator` | 16614508649855063626 |
  | `sim_lhd_verilog` | 13622586069984811406 |
  | `sim_lhd_pyrope` | 13491777193715335997 |

  `add` agrees across all three, so the harness machinery — LFSR, fold, reset
  schedule — is sound. What is specific to this test is `out_stages`, a packed
  2-D port (`[NumStages:0][Width-1:0]`) that the harness flattens to 40 bits.
  Note that **verilator and `lhd·verilog` read the same SystemVerilog** and
  still disagree, which points at a front-end difference in how that packed
  array is flattened rather than at the Pyrope rewrite. Unresolved; this is a
  finding, not a reason to weaken the gate.

- **LiveHD's netlist is slower and larger here** (measured 2026-08-26, sky130,
  all four numbers from OpenSTA on the same SDC):

  | netlist | cells | flops | critical path (OpenSTA) |
  | --- | ---: | ---: | ---: |
  | `yosys + abc` | 64 | 32 | **0.489 ns** (flop → flop) |
  | `lhd·verilog`, normalized | 155 | 40 | **0.826 ns** (flop → flop) |
  | `lhd·pyrope`, normalized | 580 | 34 | none timed — see below |

  LiveHD lowers the shift chain through a memory rather than as plain flops.
  From Verilog that costs 2.4x the cells and 1.7x the delay; from Pyrope the
  array becomes a `cgen_memory_8rd_5wr` submodule of 505 cells — an
  8-read/5-write memory for a 4-stage shift register — so the whole design is
  580 cells against yosys's 64.

- **`lhd·pyrope` has no timed path.** OpenSTA's worst path is the zero-delay
  `in[0] -> out_stages[0]` feedthrough, meaning none of its 34 registers is on
  the timing graph at all. The clock most likely does not propagate into the
  `cgen_memory` submodule. Reported as a note rather than as a 0 ns critical
  path, which would read like an extraordinarily fast design.

- **`lec = "none"`** — Pyrope and Verilog have not been proven equivalent yet.
  Reported, but excluded from the headline geomean.
- One config only. `pyrope/br_delay.prp` pins `WIDTH` and `NUM_STAGES` as
  comptime constants; a second parameter point needs a second monomorphic
  module or `[pyrope] param_binding = "set"`.
- `out_stages` is a packed 2-D port upstream (`[NumStages:0][Width-1:0]`). The
  Pyrope side flattens it to one `(NumStages+1)*Width` integer with explicit
  per-range bit assigns. That flattening is the prime suspect for the checksum
  disagreement above.
- The Pyrope is written entirely with **literals** (`[4]u8`, `u40`), not named
  constants. This `lhd` build rejects an array size that is a named constant
  ("a named constant must fold before lowering") and has removed the
  `int(min=,max=)` type in favour of sized `uN`.

## Generated files

`sim/` and `constraints/` come from `lhdtrack import seed br_delay` and carry a
`lhdtrack-generated` marker.
