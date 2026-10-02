# br_mux_bin_structured_gates_mock

The upstream file declares `br_mux_bin_structured_gates`, not a module named after
the mock file. `verilog/br_mux_bin_structured_gates_mock_bench.sv` provides a fixed
top around that behavioral model: five 16-bit lanes, a three-bit selector and separate
data/valid outputs. Selectors 0–4 exercise every lane; 5–7 exercise invalid selections.
Both observable outputs enter the harness checksum.

The upstream source and synthesis prohibition are unchanged. `[sim]` and `[lec]` use
`verilog_profile = "behavioral"`, which omits `SYNTHESIS` in both Verilog simulators,
port extraction and source-equivalence elaboration. Their defaults remain the existing
synthesis profile for all other tests. This is a behavioral simulation benchmark:
`[synth].skip_reason` explicitly skips physical synthesis and RTL/netlist comparisons.
Use `br_mux_bin_structured_gates` for the real structured-gate synthesis benchmark.

The handwritten Pyrope DUT passes unbounded native and independent Yosys-backed
source LEC. Generated paired harnesses drive the same LFSR and fold the same outputs;
Verilator's recorded reference uses 43,478,261 cycles, tuned to 0.5–2 seconds.

Recreate the harnesses with `lhdtrack import seed br_mux_bin_structured_gates_mock`.
Provenance is in `design.toml`. The wrapper has no overridable parameters; upstream
intermediate modules keep their original parameter declarations.
