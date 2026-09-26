"""LiveHD Verilog synthesis through USYN with compile SAT optimization disabled."""
from syn_lhd_verilog import KIND, NEEDS, OPTIONAL, USES_TECH, run_mapper

NAME = "syn_lhd_verilog_usyn_no_satopt"


def run(ctx):
    return run_mapper(ctx, "usyn", satopt=False)
