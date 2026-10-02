// SPDX-License-Identifier: Apache-2.0
// Fixed benchmark point; the upstream mock remains parameterized and unchanged.
module br_mux_bin_structured_gates_mock_bench (
    input logic [2:0] select,
    input logic [79:0] data,
    output wire [15:0] out,
    output wire out_valid
);
  br_mux_bin_structured_gates #(.NumSymbolsIn(5), .SymbolWidth(16)) dut (
      .select(select), .in(data), .out(out), .out_valid(out_valid)
  );
endmodule
