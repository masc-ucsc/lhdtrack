# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_delay_valid.sv
br_misc_tieoff_one.sv
br_misc_tieoff_zero.sv
br_misc_unused.sv
br_mux_onehot.sv
br_math_pkg.sv
br_ram_data_rd_pipe.sv
