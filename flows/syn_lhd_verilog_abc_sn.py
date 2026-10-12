"""LiveHD ABC versus SN: fixed Brent-Kung adder, without arithmetic trials."""
from syn_lhd_verilog import KIND, NEEDS, OPTIONAL, USES_TECH, run_mapper

NAME = "syn_lhd_verilog_abc_sn"


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False, abc_sn=True)
