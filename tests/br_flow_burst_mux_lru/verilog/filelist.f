# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_arb_pairwise_core_internal.sv
br_misc_unused.sv
br_lru_state_internal.sv
br_arb_lru_internal.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_arb_core.sv
br_mux_onehot.sv
br_flow_mux_core.sv
br_arb_grant_hold.sv
br_flow_burst_mux_core.sv
br_flow_burst_mux_lru.sv
