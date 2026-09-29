# CPU datapath constraint. Period in ps; reset excluded from datapath timing.
create_clock -name clk -period 400.0 [get_ports i_clk]
set_input_delay -clock clk 0.0 [all_inputs -no_clocks]
set_output_delay -clock clk 0.0 [all_outputs]
set_false_path -from [get_ports i_rst]
