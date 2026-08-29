# Generated packed-stage chain

The source assigns the packed array's initial stage before a generate loop and
reads its final stage in a module-level output assignment. Continuous assigns
are concurrent, so source order must not make the output observe only the first
write.

The original Slang dependency scheduler saw every read and partial write as a
dependency on the whole `stages` symbol. That made a false cycle; its source-order
fallback lowered `out = stages[2]` before either generated writer. The emitted
Pyrope had the same shape as `bad.prp`: it computes `_mux_2`, but reads
`stages__w1` and drops the final store. `good.prp` is the required version chain.

```sh
lhd lec --impl bad.prp --ref ref.sv --top generated_packed_stage_chain --set formal.solver=cvc5
lhd lec --impl good.prp --ref ref.sv --top generated_packed_stage_chain --set formal.solver=cvc5
```

The first command refutes; the second proves. The corpus instance that exposed
this was `br_tracker_linked_list_ctrl`'s generated rotate network.
