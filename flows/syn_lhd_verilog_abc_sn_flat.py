"""Single core color, fixed SN arithmetic, preserving default memory implementations."""
from syn_lhd_verilog import KIND, NEEDS, OPTIONAL, USES_TECH, run_mapper

NAME = "syn_lhd_verilog_abc_sn_flat"


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False, abc_sn=True, flat=True)
