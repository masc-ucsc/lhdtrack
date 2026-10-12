"""Partition-policy diagnostic with fixed arithmetic and the full ABC recipe."""
from syn_lhd_verilog import KIND, NEEDS, OPTIONAL, USES_TECH, run_mapper

NAME = "syn_lhd_verilog_abc_sn_partition"


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False, abc_sn=True)
