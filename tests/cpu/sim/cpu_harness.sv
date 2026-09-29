// SPDX-License-Identifier: MIT
// Matched with cpu_harness.prp. CPU memories survive periodic resets.
module cpu_harness(input wire clk, input wire rst, output wire [63:0] checksum);
  logic [11:0] phase;
  // No reset: the simulation flow initializes state to zero once at startup.
  logic [63:0] sum;
  wire [31:0] result;
  wire reset_active = rst || phase < 12'd4;
  cpu dut(.i_clk(clk), .i_rst(!reset_active), .o_out(result));
  always_ff @(posedge clk) begin
    if (rst) begin
      phase <= 0;
    end else begin
      phase <= phase + 12'd1;
    end
    // Observe every cycle, including initial reset and periodic CPU resets.
    sum <= (sum * 64'd131) + {32'b0, result};
  end
  assign checksum = sum;
endmodule
