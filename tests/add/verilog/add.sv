// Simple adder benchmark
module add
#(
    localparam BW = 8
)
(
    input logic [BW-1:0] a,
    input logic [BW-1:0] b,
    output logic [BW-1:0] result
);
    always_comb result = a + b;      

endmodule
