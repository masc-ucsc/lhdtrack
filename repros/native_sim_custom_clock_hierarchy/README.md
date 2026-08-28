# Native simulation of a custom-clock hierarchy

This is the reduced shape behind the CDC simulation split. The DUT is
Pyrope/Verilog LEC-proven, and cgen-emitted Verilog produces the same checksum
as the untouched RTL (`716913426322971318` for one million cycles). Native
Pyrope simulation of the same clocked hierarchy instead produces
`6170648312958112256`.

The generated harness initially tied explicit DUT clocks to zero, which froze
the DUT and produced `1152921504606846975`. Driving a real low/high edge fixes
that harness error but exposes a second issue: the parent checksum register
observes a different same-edge child value than Verilog nonblocking semantics.

```sh
lhd sim harness.prp tb.prp --workdir work --arg cycles=1000000
```

Wish: native hierarchical simulation should schedule every register on the
same edge from the old state, including a parent reading a custom-clock child,
so a cgen-equivalent design also has the same native checksum.
