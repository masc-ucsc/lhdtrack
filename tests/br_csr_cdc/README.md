# br_csr_cdc

Crosses packed CSR requests, responses, and abort pulses between the upstream
and downstream clocks.

| | |
| --- | --- |
| top | `br_csr_cdc` |
| kind | sequential |
| suite | csr |
| upstream | Bedrock RTL |
| revision | `0a990f222cd970fdaec42c6e0a4e372cf0e1400f` |
| upstream path | `csr/rtl/br_csr_cdc.sv` |

## Known gaps

The hand-written Pyrope uses separate 55-bit request and 34-bit response CDC
specializations, matching the two Verilog elaborations. Their resetless packed
delay state is written explicitly. CVC5 proves the pulse, toggle, both CDC
register specializations, and the top module after collapsing one-sided helper
hierarchy. The Yosys-backed comparison times out without a counterexample. The
cgen-emitted Pyrope and untouched RTL also match over the one-million-cycle
LiveHD checksum (`4110951272065868108`); Verilator differs because of the
separately tracked native custom-clock scheduling issue.

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed br_csr_cdc` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
