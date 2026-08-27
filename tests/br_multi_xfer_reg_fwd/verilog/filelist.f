# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_misc_unused.sv
br_multi_xfer_checks_sendable_data_impl.sv
br_multi_xfer_checks_sendable_data_intg.sv
br_math_pkg.sv
br_shift_left.sv
br_shift_right.sv
br_multi_xfer_reg_fwd.sv
