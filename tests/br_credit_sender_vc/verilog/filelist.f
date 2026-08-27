# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_credit_counter.sv
br_delay_nr.sv
br_delay_valid.sv
br_math_pkg.sv
br_enc_onehot2bin.sv
br_misc_unused.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_fork_select_multihot.sv
br_flow_fork.sv
br_flow_arb_core.sv
br_mux_onehot.sv
br_flow_mux_core.sv
br_credit_sender_vc.sv
