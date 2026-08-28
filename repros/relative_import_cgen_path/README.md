# Relative-import cgen output path

A relative Pyrope import retains `../lib/core.core` as its internal graph
identity. Before the cgen filename fix, that identity was appended verbatim to
the emit directory. `File_output` consequently tried to open a path outside the
directory, reported `Bad file descriptor`, and the compile process segfaulted.

```sh
lhd compile app/top.prp --top relative_top \
  --emit-dir verilog:out --workdir work
```

The graph identity and generated Verilog module identity must remain unchanged;
only the emitted file basename is sanitized to `.._lib_core.core.v`.
