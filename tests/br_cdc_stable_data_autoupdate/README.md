# br_cdc_stable_data_autoupdate

Detects source-data changes, transfers the latest 32-bit value through the
fixed two-stage CDC register, and registers the destination update pulse/data.

| | |
| --- | --- |
| top | `br_cdc_stable_data_autoupdate` |
| kind | sequential |
| suite | cdc |
| upstream | Bedrock RTL |
| revision | `0a990f222cd970fdaec42c6e0a4e372cf0e1400f` |
| upstream path | `cdc/rtl/br_cdc_stable_data_autoupdate.sv` |

## Known gaps

The Pyrope is hand-written for the pinned configuration and its cgen-emitted
Verilog matches the untouched reference's one-million-cycle checksum
(`11552834563771747320`). Its hand-written `br_cdc_reg` helper mirrors the
resetless packed delay state in the Verilog hierarchy; CVC5 proves the source
equivalence after collapsing the reference-only helper modules. The
Yosys-backed LEC still times out without a counterexample.

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed br_cdc_stable_data_autoupdate` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
