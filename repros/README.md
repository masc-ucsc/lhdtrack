# repros

Minimal reproductions of LiveHD issues the corpus surfaced. Each file is
self-contained and names the exact command that shows the problem.

| file | what it shows |
| --- | --- |
| [`reset_pin_cvc5_split/`](reset_pin_cvc5_split/) | CVC5 refutes a synchronous `reset_pin` crossing that lgyosys proves |
| [`relative_import_cgen_path/`](relative_import_cgen_path/) | A relative Pyrope import used to escape cgen's output path and crash |
| [`native_sim_custom_clock_hierarchy/`](native_sim_custom_clock_hierarchy/) | Native simulation observes a different same-edge child value than emitted Verilog |
| [`simfail_sibling_import_alias/`](simfail_sibling_import_alias/) | Simfail inlining drops a sibling-module alias, so automatic VCD replay cannot compile |
| [`generated_packed_stage_chain/`](generated_packed_stage_chain/) | Generated partial packed-array writes must feed the final output version |
| [`formal_bit_disjoint_word_cycle/`](formal_bit_disjoint_word_cycle/) | Post-collapse lane folding removes a false word-level SCC before cvc5 encoding |

A repro lands here when a corpus failure is reduced to something small enough
to hand to someone. The corpus tells you *that* something broke; a repro tells
you *what*.
