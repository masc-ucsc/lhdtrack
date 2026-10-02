// SPDX-License-Identifier: Apache-2.0
// Benchmark fixture for the eleven combinational behavioral gate models.
// Clock-buffer/inverter/mux models are sampled as data; no generated clock is driven.
module br_gate_mock_bench (
    input logic a,
    input logic b,
    input logic sel,
    output wire [10:0] out
);
  br_gate_buf buf_dut (.in(a), .out(out[0]));
  br_gate_clk_buf clk_buf_dut (.in(a), .out(out[1]));
  br_gate_inv inv_dut (.in(a), .out(out[2]));
  br_gate_clk_inv clk_inv_dut (.in(a), .out(out[3]));
  br_gate_and2 and_dut (.in0(a), .in1(b), .out(out[4]));
  br_gate_or2 or_dut (.in0(a), .in1(b), .out(out[5]));
  br_gate_xor2 xor_dut (.in0(a), .in1(b), .out(out[6]));
  br_gate_mux2 mux_dut (.in0(a), .in1(b), .sel(sel), .out(out[7]));
  br_gate_clk_mux2 clk_mux_dut (.in0(a), .in1(b), .sel(sel), .out(out[8]));
  br_gate_cdc_pseudostatic pseudo_dut (.in(a), .out(out[9]));
  br_gate_cdc_maxdel maxdel_dut (.in(a), .out(out[10]));
endmodule
