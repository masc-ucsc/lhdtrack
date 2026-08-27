// Barrel shifter benchmark (left shift)
module barrel_shifter
#(
    localparam BW = 8
)
(
    input logic [BW-1:0] data,
    input logic [$clog2(BW)-1:0] shift_amt,
    output logic [BW-1:0] shifted
);
    always_comb begin
        shifted = data << shift_amt;
    end
endmodule
