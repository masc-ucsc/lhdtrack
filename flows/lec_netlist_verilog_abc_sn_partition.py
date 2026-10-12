"""Verify the exact netlist from the fixed-arithmetic partition diagnostic."""
from lec_netlist_verilog import KIND, NEEDS, USES_TECH, run_mapper

NAME = "lec_netlist_verilog_abc_sn_partition"
AFTER = ("syn_lhd_verilog_abc_sn_partition",)


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False,
                      synthesis_flow=AFTER[0])
