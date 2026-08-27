# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_amba_pkg.sv
br_misc_unused.sv
br_counter_incr.sv
br_delay_valid.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_math_pkg.sv
br_flow_demux_select_unstable.sv
br_flow_join_select_multihot.sv
br_flow_join.sv
br_rr_state_internal.sv
br_arb_rr_internal.sv
br_flow_arb_core.sv
br_mux_onehot.sv
br_flow_mux_core.sv
br_flow_mux_rr.sv
br_flow_reg_fwd.sv
br_misc_tieoff_one.sv
br_misc_tieoff_zero.sv
br_csr_axil_widget.sv
