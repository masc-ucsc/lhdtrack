# CVC5 reset-pin backend split

The Pyrope `reset_pin` register and the explicit SystemVerilog synchronous-reset
register emit equivalent Verilog, and the Yosys-backed LEC proves the pair. The
in-process CVC5 LEC instead refutes after the reset phase once the register feeds
an unreset destination-clock synchronizer.

```sh
lhd lec --impl impl.prp --ref ref.sv --top pulse_repro --set formal.solver=cvc5
lhd lec --impl impl.prp --ref ref.sv --top pulse_repro --set formal.solver=lgyosys
```

The corpus works around the split by spelling the synchronous reset as an
explicit data mux in Pyrope.
