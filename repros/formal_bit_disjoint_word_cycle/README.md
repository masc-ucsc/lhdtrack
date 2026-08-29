# Bit-disjoint feedback becomes a false word-level cycle

`br_amba_axi_demux` packs several ready/valid lanes into vectors. Each fork
valid bit excludes its corresponding ready bit, so the circuit is acyclic at
bit granularity. A word-level dependency graph nevertheless sees this ring:

```text
packed ready -> packed valid -> select -> ready[0] -> packed ready
```

The two small Verilog sources document the same circuit as a packed word-level
ring and as its scalar bit-level DAG. This reduced pair simplifies to the same
DAG before the solver, so the command is a semantic sanity check. The full
corpus case originally preserved enough vector/hierarchy structure to expose
the false cycle.

```sh
../../../livehd/bazel-bin/lhd/lhd lec \
  --impl verilog:false_scc_split.v --ref verilog:false_scc.v --top false_scc \
  --set formal.solver=cvc5 --workdir /tmp/false-scc-source
```

The full corpus trigger is the flat source-vs-netlist obligation:

```sh
cd ../..
PYTHONPATH=src python3 -m lhdtrack.cli run \
  --test br_amba_axi_demux --flow lec_netlist --tech sky130 --keep-work
```

Before the 2026-08-28 fix, both cvc5 legs returned `unsupported`, with a
diagnostic beginning `WORD-LEVEL CYCLE through ...`. The hierarchy driver now
re-runs value-preserving slice/concat folding after asymmetric hierarchy
collapse, so the packed false SCC is reduced to its lane-level DAG. It also
discards the unnamed `sub_<nid>` prefix of a generated partition wrapper while
retaining real instance hierarchy, and gives an exact packed-to-scalar register
split priority over speculative one-to-one state pairing.

The authoritative ASAP7 and sky130 rerun now proves both Pyrope-vs-netlist and
Verilog-vs-netlist with cvc5. Keep this repro as a guard and as documentation of
the useful feature: lane-aware cleanup must run after any hierarchy transform,
not only at front-end compile time.
