# Simfail inlining drops sibling import aliases

The counterexample generator can re-emit a Verilog hierarchy as several
Pyrope files and then inline those files into one self-contained
`simfail_<top>.prp`. It removes sibling imports because the sibling definitions
are now local, but it also removes the alias binding used at call sites.

Observed after `lhd lec` refuted the hand-written `br_cdc_reg.prp` against the
native-Verilog `br_cdc_reg` hierarchy with `formal.simfail_run=true`:

```prp
// Re-emitted br_cdc_bit_toggle.prp (valid before concatenation):
const br_gate_cdc_sync_t = import("br_gate_cdc_sync.br_gate_cdc_sync")
mut br_gate_cdc_sync = br_gate_cdc_sync_t::[name=br_gate_cdc_sync](...)

// Concatenated simfail_br_cdc_reg.prp (invalid after import stripping):
mut br_gate_cdc_sync = br_gate_cdc_sync_t::[name=br_gate_cdc_sync](...)
pub mod br_gate_cdc_sync(...) -> (...) { ... }
```

The generated JSON and Pyrope witness are written, but automatic VCD replay
fails with:

```text
call to undefined function 'br_gate_cdc_sync_t'
```

When a sibling import is localized, preserve its alias as a local lambda alias
(for example `const br_gate_cdc_sync_t = br_gate_cdc_sync`) and apply any
reference-side collision rename to the alias target as well.
