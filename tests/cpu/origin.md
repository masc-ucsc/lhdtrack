# Origin

The reference is the Veryl simulator comparison CPU, from
[veryl-lang/veryl](https://github.com/veryl-lang/veryl) at
[`46ffa40d940b5540f33a25fda2d4d894dd2aa9a9`](https://github.com/veryl-lang/veryl/tree/46ffa40d940b5540f33a25fda2d4d894dd2aa9a9/crates/simulator/compare/cpu).
Original files: `crates/simulator/compare/cpu/test.sv`, `test.veryl`, and `program.hex`.

The upstream dual license permits MIT; the verbatim [LICENSE](LICENSE) and
[COPYRIGHT](COPYRIGHT) are retained. Pyrope and benchmark adapters are MIT derivatives.

The SystemVerilog adapter removes the timing testbench, renames the external
wrapper from `top` to `cpu`, and changes the image path to `data/program.hex`.
The upstream `$readmemh` call is retained. All other core RTL is unchanged.
The Pyrope equivalent uses typed pipeline records, native word shifts, signed
comparisons, and `std.readmemh("../data/program.hex")` for the same instruction
memory. Both languages load the one shared firmware image at simulation startup;
register/data memories remain unreset. Synthesis through LiveHD preserves the
externally initialized memory instead of specializing it to this firmware.

The simulation harness restarts the CPU every 4096 clocks without clearing its
memories. It accumulates the public output on every cycle, including initial reset and
periodic restarts, with identical harnesses in both languages. The checksum
register has no reset. Simulation resolves unspecified initial state and
unknown bits to zero, matching the lhdtrack two-state policy.

See [design.toml](design.toml) for machine-readable provenance and [README.md](README.md)
for commands and verification status. Sources and firmware are entirely local.
