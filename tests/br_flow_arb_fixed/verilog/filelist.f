# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_arb_fixed_internal.sv
br_misc_unused.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_arb_core.sv
br_flow_arb_fixed.sv
