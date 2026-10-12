"""Check the exact retained single-color SN-profile LiveHD netlist."""
from lec_netlist_verilog import KIND, NEEDS, USES_TECH, run_mapper

NAME = "lec_netlist_verilog_abc_sn_flat"
AFTER = ("syn_lhd_verilog_abc_sn_flat",)


def run(ctx):
    return run_mapper(ctx, "abc", satopt=False,
                      synthesis_flow=AFTER[0])
