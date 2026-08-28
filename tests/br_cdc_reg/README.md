# br_cdc_reg

<!-- What this block does, in a sentence or two. -->

| | |
| --- | --- |
| top | `br_cdc_reg` |
| kind | sequential |
| suite | cdc |
| upstream | Bedrock RTL |
| revision | `0a990f222cd970fdaec42c6e0a4e372cf0e1400f` |
| upstream path | `cdc/rtl/br_cdc_reg.sv` |

## Known gaps

The Pyrope is hand-written for the single pinned configuration. Its resetless
delay cells use the same partial packed-state shape as the Verilog helpers, so
LiveHD can relate their actual machine flops after collapsing the reference-only
hierarchy. CVC5 proves all 18 state cones. The Yosys-backed comparison times out
without a counterexample on this multi-clock design.

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed br_cdc_reg` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
