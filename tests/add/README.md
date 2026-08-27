# add

An unsigned `BW`-bit adder — the smallest useful synthesis benchmark, and the
control case for the whole corpus. If `add` shows a surprising delta, the
surprise is in the flow, not in the design.

| | |
| --- | --- |
| top | `add` |
| kind | combinational |
| suite | comb |
| upstream | [circt-synth-tracker](https://github.com/uenoku/circt-synth-tracker) |
| revision | `85721d2fd9cf49ce6eb436f7d4671590b55288c1` |
| upstream path | `benchmarks/comb/microbenchmarks/add.sv` |
| licence | Apache-2.0 WITH LLVM-exception (see `LICENSE`) |

The two `// RUN:` lines at the top of the upstream file were removed: they are
circt-synth-tracker's lit harness, not part of the design.

## Known gaps

- **`lec = "none"`** — the Pyrope and Verilog sides have not yet been proven
  equivalent, so this test is reported but excluded from the headline geomean.
  Run `lhdtrack run --test add --flow lec_pyrope_vs_verilog` once the toolchain
  is built, then set `[status].lec` to the verdict.
- Combinational, so the simulation track wraps it in a registered harness
  (`sim/add_harness.sv` / `.prp`). Synthesis still reads the **bare** module, so
  the harness flops never appear in this test's area.

## Generated files

`sim/` and `constraints/` come from `lhdtrack import seed add` and carry a
`lhdtrack-generated` marker. Remove the marker to take a file over by hand; the
generator will not touch it again.
