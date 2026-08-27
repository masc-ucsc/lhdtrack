// Parity generator benchmark
module parity
#(
    localparam BW = 8
)
(
    input logic [BW-1:0] data,
    output logic even_parity,
    output logic odd_parity
);
    always_comb begin
        even_parity = ^data;
        odd_parity = ~even_parity;
    end
endmodule
