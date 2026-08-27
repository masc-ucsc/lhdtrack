# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_amba_pkg.sv
br_rr_state_internal.sv
br_misc_unused.sv
br_arb_rr_internal.sv
br_arb_rr.sv
br_math_pkg.sv
br_counter.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_reg_fwd.sv
br_flow_reg_rev.sv
br_flow_reg_both.sv
br_amba_axil_split.sv
