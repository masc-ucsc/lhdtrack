module false_scc (
    input  wire       push,
    input  wire       other_ready,
    input  wire [1:0] downstream_ready,
    output wire [1:0] valid,
    output wire       upstream_ready
);
  wire valid0;

  // The same circuit as false_scc.v, written as its bit-level DAG.
  assign valid0         = push & other_ready;
  assign upstream_ready = downstream_ready[valid0];
  assign valid[0]       = valid0;
  assign valid[1]       = push & upstream_ready;
endmodule
