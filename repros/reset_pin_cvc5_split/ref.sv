module bit_toggle(input logic src_bit, dst_clk, output logic dst_bit);
  logic [1:0] sync;
  always_ff @(posedge dst_clk) sync <= {sync[0], src_bit};
  assign dst_bit = sync[1];
endmodule

module pulse_repro(
  input logic src_clk, src_rst, src_pulse, dst_clk,
  output logic dst_pulse
);
  logic src_level, dst_level, dst_level_d;
  always_ff @(posedge src_clk) begin
    if (src_rst) src_level <= 1'b0;
    else if (src_pulse) src_level <= ~src_level;
  end
  bit_toggle bit_toggle_inst(.src_bit(src_level), .dst_clk, .dst_bit(dst_level));
  always_ff @(posedge dst_clk) dst_level_d <= dst_level;
  assign dst_pulse = dst_level ^ dst_level_d;
endmodule
