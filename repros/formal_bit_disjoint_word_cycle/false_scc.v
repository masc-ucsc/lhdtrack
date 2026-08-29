module false_scc (
    input  wire       push,
    input  wire       other_ready,
    input  wire [1:0] downstream_ready,
    output wire [1:0] valid,
    output wire       upstream_ready
);
  wire [1:0] ready;
  wire       select;

  assign ready[0] = upstream_ready;
  assign ready[1] = other_ready;

  // Each output bit excludes its corresponding ready bit. There is no
  // bit-level cycle: valid[0] depends only on ready[1], while the feedback
  // through upstream_ready enters ready[0] and therefore only valid[1].
  assign valid[0] = push & ready[1];
  assign valid[1] = push & ready[0];

  assign select = valid[0];
  assign upstream_ready = downstream_ready[select];
endmodule
