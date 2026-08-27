# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_delay.sv
br_delay_valid.sv
br_misc_unused.sv
br_mux_onehot.sv
br_csr_demux_select_onehot.sv
br_demux_addr_decode.sv
br_csr_demux.sv
