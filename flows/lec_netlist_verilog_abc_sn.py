"""Prove the exact emitted netlist of the fixed Brent-Kung comparison."""
from lec_netlist_verilog import KIND, NEEDS, USES_TECH, run_mapper

NAME = "lec_netlist_verilog_abc_sn"
AFTER = ("syn_lhd_verilog_abc_sn",)


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False, synthesis_flow=AFTER[0])
