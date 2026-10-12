"""LiveHD Pyrope synthesis through the USYN mapper (the Pyrope twin of syn_lhd_verilog_usyn)."""
from lib.lhd_synth_policy import mapping_settings
from syn_lhd_pyrope import KIND, NEEDS, OPTIONAL, USES_TECH, run as _run

NAME = "syn_lhd_pyrope_usyn"


def run(ctx):
    return _run(ctx, settings=[
        "--set", "synth.mapper=usyn",
        *mapping_settings(ctx.tech.name, "usyn", str(ctx.abc_delay_ps())),
    ])
