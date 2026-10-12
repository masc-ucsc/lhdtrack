module Direct(input clock, reset, valid, flush, input [7:0] d, output logic [7:0] q);
  always @(posedge clock)
    if (reset) q <= 0;
    else q <= flush ? 0 : valid ? d : q;
endmodule
module OneTemp(input clock, reset, valid, flush, input [7:0] d, output logic [7:0] q);
  always @(posedge clock)
    if (reset) q <= 0;
    else begin
      automatic logic [7:0] a = valid ? d : q;
      q <= flush ? 0 : a;
    end
endmodule
module ChainedTemps(input clock, reset, valid, flush, input [7:0] d, output logic [7:0] q);
  always @(posedge clock)
    if (reset) q <= 0;
    else begin
      automatic logic [7:0] a = valid ? d : q;
      automatic logic [7:0] b = flush ? 0 : a;
      q <= b;
    end
endmodule
