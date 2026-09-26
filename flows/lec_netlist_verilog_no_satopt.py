"""Check the retained ABC emission with compile SAT optimization disabled."""
from lec_netlist_verilog import run_mapper

NAME = "lec_netlist_verilog_no_satopt"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = True


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False)
