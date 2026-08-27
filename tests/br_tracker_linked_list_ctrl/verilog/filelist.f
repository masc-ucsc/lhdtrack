# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_misc_unused.sv
br_math_pkg.sv
br_counter.sv
br_delay_shift_reg.sv
br_delay_valid.sv
br_enc_countones.sv
br_enc_priority_encoder.sv
br_flow_checks_valid_data_impl.sv
br_mux_onehot.sv
br_shift_rotate.sv
br_tracker_linked_list_ctrl.sv
