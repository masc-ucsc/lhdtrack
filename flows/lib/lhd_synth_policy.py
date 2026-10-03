"""Measured synthesis profiles shared by Verilog, Pyrope and netlist LEC."""
from __future__ import annotations

import os
import shlex


def color_settings(tech: str) -> list[str]:
    """Let the compiler select its default coloring for either technology."""
    return []


def abc_settings(tech: str) -> list[str]:
    """The optional comparison changes only ABC's SAT optimization switch.

    LHDTRACK_LHD_SET adds explicit `key=value` lhd settings (space separated)
    for an experiment run, e.g. `pass.abc.memory=true`; they are recorded with
    each measurement through recorded_settings."""
    out = []
    for item in os.environ.get("LHDTRACK_LHD_SET", "").split():
        if "=" not in item:
            raise ValueError(f"LHDTRACK_LHD_SET entries must be key=value, got {item!r}")
        out += ["--set", item]
    satopt = os.environ.get("LHDTRACK_ABC_SATOPT")
    if satopt is None or satopt == "true":
        return out
    if satopt not in {"true", "false"}:
        raise ValueError("LHDTRACK_ABC_SATOPT must be true or false")
    return out + ["--set", f"pass.abc.satopt={satopt}"]


def mapping_settings(tech: str, mapper: str, delay: str) -> list[str]:
    """Apply an explicit mapping-delay experiment without duplicate CLI keys."""
    key = f"pass.{mapper}.delay"
    settings = abc_settings(tech)
    other = []
    override = None
    for i in range(0, len(settings), 2):
        name, value = settings[i + 1].split("=", 1)
        if name == key:
            if override is not None and override != value:
                raise ValueError(f"conflicting {key} overrides")
            override = value
        else:
            other.extend(settings[i:i + 2])
    return ["--set", f"{key}={override if override is not None else delay}", *other]


def recorded_settings(commands: list[str]) -> dict[str, str]:
    """Store actual command settings with each measurement; never infer history."""
    selected = {"pass.color.synth_alg", "pass.color.ctrl_cones", "pass.color.forward",
                "pass.color.stop_arith", "pass.color.stop_mux", "pass.color.max_gate",
                "pass.abc.area_relax", "pass.abc.area_flow", "pass.abc.satopt",
                "pass.abc.memory", "pass.satopt"}
    settings = {"base": "compiler defaults"}
    for command in commands:
        argv = shlex.split(command)
        for i, token in enumerate(argv[:-1]):
            if token != "--set":
                continue
            key, value = argv[i + 1].split("=", 1)
            if key.startswith("abc."):
                key = "pass." + key
            if (key in selected or key.startswith("pass.usyn.")
                    or key in {"pass.abc.delay", "synth.mapper"}):
                settings[key] = value
    return settings
