# One source per line, in compile order. Read by slang, verilator and yosys.
# Macro headers travel with the source: slang resolves an include relative
# to the including file, verilator only searches its -I list, and both front
# ends must see the same files.
br_amba_pkg.sv
br_misc_unused.sv
br_counter_incr.sv
br_math_pkg.sv
br_amba_axi_resizer_beat_tracker.sv
br_enc_bin2onehot.sv
br_counter.sv
br_delay_shift_reg.sv
br_delay_valid.sv
br_flow_checks_valid_data_impl.sv
br_flow_checks_valid_data_intg.sv
br_flow_reg_fwd.sv
br_flow_reg_rev.sv
br_misc_tieoff_one.sv
br_misc_tieoff_zero.sv
br_mux_onehot.sv
br_fifo_staging_buffer.sv
br_fifo_pop_ctrl_core.sv
br_fifo_pop_ctrl.sv
br_fifo_push_ctrl_core.sv
br_fifo_push_ctrl.sv
br_fifo_ctrl_1r1w.sv
br_demux_bin.sv
br_ram_addr_decoder.sv
br_ram_data_rd_pipe.sv
br_ram_flops_tile.sv
br_ram_flops.sv
br_fifo_flops.sv
br_flow_fork_select_multihot.sv
br_flow_fork.sv
br_flow_join_select_multihot.sv
br_flow_join.sv
br_flow_reg_none.sv
br_mux_bin.sv
br_shift_left.sv
br_amba_axi_shrinker.sv
