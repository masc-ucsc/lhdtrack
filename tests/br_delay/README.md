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

- **`lec = "proven"` since 2026-08-27 — it took THREE fixes, and the reference
  really was the side that was wrong.** The refutation (`out_stages(ref=0
  impl=254)` at step 1) came apart as follows:

  1. **livehd, `inou/slang`.** Upstream drives lane 0 combinationally
     (`assign stages[0] = in;`) and flops only lanes 1..NumStages. The reader
     saw ONE variable written by two kinds of driver and lowered the whole
     packed `stages` array as one register, so lane 0 got a flop too: the
     reference was a FIVE-stage delay line with a registered `out_stages[7:0]`.
     Such a variable is now split into a combinational composite (what every
     read resolves to) and a hidden flop (what an edge-process write targets).
     Regression: `//inou/slang:slang_compile-nocheck_slang_partial_reg`.

  2. **livehd, `upass/tolg`.** With the reference fixed the refutation moved to
     the very first post-reset step, on the IMPL side: `reg
     stages:[4]u8:[reset_pin=ref rst] = 0` lowered to a memory with **no reset
     at all** — the restore sweep was gated on the module's *implicit* reset
     input, which a declaration that names its own reset deliberately does not
     mint. The reset value was silently demoted to a power-on `INIT` and the
     user writes were never gated, so `in` landed in stage 0 during reset.

  3. **This file (`pyrope/br_delay.prp`), twice.** A Pyrope reg array defaults
     to `ordering="program"` — reads and writes resolve in program order — so
     the ASCENDING shift loop read the entry it had just written and `in` fell
     through all four stages in ONE cycle (`out_stages` 0xfefefefe00 against the
     Verilog's 0xfe00). The loop now writes high stage first; `:[ordering="old"]`
     on the declaration is the equivalent one-attribute spelling.

     And the RESET was a four-cycle sweep. `reg stages:[4]u8 = 0` on an ARRAY
     is a reset value, but an array is a memory and a memory has no parallel
     reset port, so Pyrope realizes it as a one-entry-per-cycle restore — while
     upstream's `BR_REGI` resets every stage on ONE edge. cvc5 proved it anyway
     (its two reset-hold cycles plus the memory's zero power-on contents hide
     the difference); lgcheck's bounded miter leaves `rst` free, found a
     single-cycle pulse, and correctly refuted. The reset is now the explicit
     `if rst != 0 { stages[j] = 0 }` arm, which is one edge on both sides.
     lgcheck now answers INCONCLUSIVE rather than refuting — an honest third
     state on a memory-versus-flat-register miter, and not a failure.

  The tell that (1) was real, and the reason a machine-emitted Pyrope side would
  never have found it: both sides of THAT proof share the front end, and only
  hand-written Pyrope implementing what the SystemVerilog says disagreed.

- **This is the ONLY test in the corpus whose cvc5 proof is BOUNDED** — `PASS(6)`
  ("equivalent for 6 cycles from reset, deeper cycles not checked"), where the
  other 150 are inductive. Induction never closes here at any `formal.bound`
  (12, 24 measured) for a structural reason: the Pyrope side is an ARRAY, i.e. a
  memory with its own sweep counter, while the reference is a flat packed
  register, so the two state spaces do not correspond and the miter falls back
  to BMC. lgcheck cannot decide it either (INCONCLUSIVE), so on its own this row
  has no independent backing.

  **It is nonetheless closed, by chaining two engines on two links:**

  | link | engine | verdict |
  | --- | --- | --- |
  | array `.prp` ≡ packed-vector `.prp` | cvc5 | PROVEN (inductive, 16 ms) |
  | packed-vector `.prp` ≡ `verilog/` | lgcheck | PROVEN (unconditional) |

  Neither engine closes the direct obligation and neither link is bounded, so
  the two together prove what one alone cannot. The packed spelling is the same
  design with `reg stages:u32` and bit-range writes instead of `[4]u8` and
  element writes; it is deliberately NOT what this file contains, because the
  point of the `idiomatic` tier is the idiomatic spelling — but it is the reason
  the bounded row can be trusted.

- One config only, and the top is monomorphic by construction: `Width = 8`,
  `NumStages = 4` are `localparam` in `verilog/br_delay.sv` and literals in
  `pyrope/br_delay.prp`. A second parameter point needs a second test.
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
