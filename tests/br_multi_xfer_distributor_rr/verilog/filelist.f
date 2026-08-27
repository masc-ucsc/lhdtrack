# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_enc_countones.sv
br_misc_unused.sv
br_enc_priority_encoder.sv
br_math_pkg.sv
br_enc_priority_dynamic.sv
br_mux_bin.sv
br_arb_multi_rr.sv
br_multi_xfer_checks_sendable_data_intg.sv
br_flow_checks_valid_data_impl.sv
br_misc_tieoff_one.sv
br_misc_tieoff_zero.sv
br_mux_onehot.sv
br_multi_xfer_distributor_core.sv
br_multi_xfer_distributor_rr.sv
