# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_gate_mock.sv
br_cdc_pkg.sv
br_cdc_bit_toggle.sv
br_delay_nr.sv
br_misc_unused.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_reg_fwd.sv
br_cdc_reg_pop.sv
br_cdc_reg_push.sv
br_cdc_reg.sv
br_cdc_stable_data.sv
