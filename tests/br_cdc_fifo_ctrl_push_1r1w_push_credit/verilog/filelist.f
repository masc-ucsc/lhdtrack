# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_gate_mock.sv
br_cdc_pkg.sv
br_cdc_bit_toggle.sv
br_cdc_fifo_gray_count_sync.sv
br_misc_unused.sv
br_cdc_fifo_reset_overlap_checks.sv
br_counter_incr.sv
br_delay_nr.sv
br_enc_bin2gray.sv
br_enc_gray2bin.sv
br_math_pkg.sv
br_cdc_fifo_push_flag_mgr.sv
br_credit_counter.sv
br_credit_receiver.sv
br_flow_checks_valid_data_intg.sv
br_misc_tieoff_one.sv
br_misc_tieoff_zero.sv
br_fifo_push_ctrl_core.sv
br_cdc_fifo_push_ctrl_credit.sv
br_cdc_fifo_ctrl_push_1r1w_push_credit.sv
