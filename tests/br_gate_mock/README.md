# br_gate_mock

This fixture checks eleven combinational behavioral models in the upstream Bedrock-RTL
gate library. The library file is a collection of modules, not a module named
`br_gate_mock`; `verilog/br_gate_mock_bench.sv` supplies the fixed benchmark top.

The three inputs exercise buffer, inverter, AND, OR, XOR and mux truth tables. Every
model output is a separate bit of the eleven-bit DUT output and enters the checksum.
Clock buffer/inverter/mux models are sampled as data; the fixture does not drive
generated clocks. The latch-based clock gates and sequential synchronizers are outside
this fixture's coverage.

The upstream sources and license are unchanged. The fixture uses the existing
`BR_PPA_SYNTHESIS` exemption, like the other gate-library consumers. Its handwritten
Pyrope implementation passes unbounded native and independent Yosys-backed LEC.

Both harnesses and drivers are generated from the elaborated DUT ports with
`lhdtrack import seed br_gate_mock`. They share the LFSR, reset schedule and output
fold. Verilator's recorded reference uses 40,000,000 cycles, tuned to 0.5–2 seconds.

Provenance and tool flows are recorded in `design.toml`. The fixture has no overridable
top-level parameters; the existing library's intermediate modules remain unchanged.
