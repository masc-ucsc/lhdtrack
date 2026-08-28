# br_gate_mock

<!-- What this block does, in a sentence or two. -->

| | |
| --- | --- |
| top | `br_gate_mock` |
| kind | sequential |
| suite | gate |
| upstream | _fill in_ |
| revision | _fill in_ |
| upstream path | _fill in_ |

## Known gaps

**`status.pyrope = "none"`, and NO front end can read this file under
`-DSYNTHESIS`** — by upstream's design, not by any tool's limitation.
`verilog/br_gate_mock.sv` opens with

```systemverilog
`ifdef SYNTHESIS
`ifndef BR_PPA_SYNTHESIS
`BR_ASSERT_STATIC(do_not_synthesize_br_gate_mock_modules_a, 0)
`endif
`endif
```

i.e. "these are behavioural models; branch this file per vendor technology and
replace them with real standard cells". Every flow here compiles with
`-DSYNTHESIS`, so the guard fires. (It fires as a raw *unknown directive*
rather than as the static assert it means, because the `` `include
"br_asserts.svh" `` that defines `BR_ASSERT_STATIC` comes seven lines LATER in
the same file — an upstream ordering bug that only shows once the guard is
reached at all.)

The same guard, verbatim, is what keeps these tests unseeded:

| | |
| --- | --- |
| via `br_gate_mock.sv` in the filelist | every `br_cdc_*`, `br_csr_cdc`, `br_mux_bin_structured_gates` |
| its own copy of the guard | `br_mux_bin_structured_gates_mock` ("include br_mux_bin_structured_gates.sv instead of this file!!") |

**The escape is upstream's own**: adding `-DBR_PPA_SYNTHESIS` skips the guard,
and lhd then elaborates the whole `br_cdc_*` family cleanly (measured
2026-08-27). It was NOT taken here because the define has to land on ALL of
`lhd`, `yosys + slang` and `verilator` in the same change — `flows/` and
`tools/` pass `-DSYNTHESIS` in eleven places — or the three front ends
elaborate different circuits and an LEC refutation stops meaning "the two
languages disagree" (see AGENTS.md). Verifying the yosys leg needs the
yosys-slang plugin staged, which `make toolchain-local` does not provide.

Separately, `br_amba_pkg`, `br_cdc_pkg` and `br_lfsr_taps` name a **package**
as their top, and `br_misc_unused` has no outputs to checksum: those four are
corpus shape, not a front-end gap either.

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed br_gate_mock` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
