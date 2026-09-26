"""Check the retained USYN Verilog emission against its original RTL graph."""
from lec_netlist_verilog import KIND, NEEDS, USES_TECH, run_mapper

NAME = "lec_netlist_verilog_usyn"


def run(ctx):
    return run_mapper(ctx, "usyn")
