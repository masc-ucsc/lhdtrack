# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_delay.sv
br_math_pkg.sv
br_enc_bin2onehot.sv
br_enc_countones.sv
br_enc_onehot2bin.sv
br_misc_unused.sv
br_enc_priority_encoder.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_reg_fwd.sv
br_multi_xfer_checks_sendable_data_impl.sv
br_multi_xfer_checks_sendable_data_intg.sv
br_shift_left.sv
br_shift_right.sv
br_multi_xfer_reg_fwd.sv
br_mux_onehot.sv
br_tracker_freelist.sv
