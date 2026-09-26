"""LiveHD Verilog synthesis through the USYN mapper."""
from syn_lhd_verilog import KIND, NEEDS, OPTIONAL, USES_TECH, run_mapper

NAME = "syn_lhd_verilog_usyn"


def run(ctx):
    return run_mapper(ctx, "usyn")
