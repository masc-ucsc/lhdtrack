"""Measured synthesis profiles shared by Verilog, Pyrope and netlist LEC."""
from __future__ import annotations

import os
import shlex


def color_settings(tech: str) -> list[str]:
    """Let the compiler select its default coloring for either technology."""
    return []


def abc_settings(tech: str) -> list[str]:
    """The optional comparison changes only ABC's SAT optimization switch."""
    satopt = os.environ.get("LHDTRACK_ABC_SATOPT")
    if satopt is None or satopt == "true":
        return []
    if satopt not in {"true", "false"}:
        raise ValueError("LHDTRACK_ABC_SATOPT must be true or false")
    return ["--set", f"pass.abc.satopt={satopt}"]


def recorded_settings(commands: list[str]) -> dict[str, str]:
    """Store actual command settings with each measurement; never infer history."""
    selected = {"pass.color.synth_alg", "pass.color.ctrl_cones", "pass.color.forward",
                "pass.color.stop_arith", "pass.color.stop_mux", "pass.color.max_gate",
                "pass.abc.area_relax", "pass.abc.area_flow", "pass.abc.satopt"}
    settings = {"base": "compiler defaults"}
    for command in commands:
        argv = shlex.split(command)
        for i, token in enumerate(argv[:-1]):
            if token != "--set":
                continue
            key, value = argv[i + 1].split("=", 1)
            if key.startswith("abc."):
                key = "pass." + key
            if key in selected:
                settings[key] = value
    return settings
