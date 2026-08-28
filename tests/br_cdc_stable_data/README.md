# br_cdc_stable_data

Synchronizes an infrequently changing 32-bit value through the fixed
two-stage `br_cdc_reg` configuration, then registers the destination update
pulse and data.

| | |
| --- | --- |
| top | `br_cdc_stable_data` |
| kind | sequential |
| suite | cdc |
| upstream | Bedrock RTL |
| revision | `0a990f222cd970fdaec42c6e0a4e372cf0e1400f` |
| upstream path | `cdc/rtl/br_cdc_stable_data.sv` |

## Known gaps

The Pyrope is hand-written for the single pinned configuration and keeps a
local hand-written `br_cdc_reg` helper because lhdtrack stages each benchmark's
Pyrope directory independently. Its cgen-emitted Verilog and the untouched
reference produce the same one-million-cycle Verilator checksum
(`970599117477954039`). After collapsing the reference-only helper hierarchy and
matching the resetless packed delay state, CVC5 proves the source equivalence.
The Yosys-backed LEC still reaches its timeout without a counterexample.

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed br_cdc_stable_data` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
