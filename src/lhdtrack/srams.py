"""HighTide FakeRAM views for explicit SRAM instances in placement tests.

The generator's 1 ns / 1 pF models are converted to ASAP7's 1 ps / 1 fF
before merging. Table axes, timing values, energy values and scalar pin
capacitances scale by 1000; leakage scales from uW to pW by 1e6.
OpenSTA derives internal-power energy units from capacitance * voltage**2.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .toolchain import Tech, Toolchain

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def normalize_bsg_lib(text: str, base: str) -> str:
    """Convert only the known BSG unit profile; reject other generator output."""
    expected = {"time_unit": "1ns", "voltage_unit": "1V", "current_unit": "1uA",
                "leakage_power_unit": "1uW", "pulling_resistance_unit": "1kohm"}
    target_units = {"time_unit": "1ps", "voltage_unit": "1V", "current_unit": "1mA",
                    "leakage_power_unit": "1pW", "pulling_resistance_unit": "1kohm"}
    for key, value in expected.items():
        match = re.search(rf'\b{key}\s*:\s*"([^"]+)"', text)
        if match is None or match[1] != value:
            raise ValueError(f"unsupported FakeRAM {key}")
        target = re.search(rf'\b{key}\s*:\s*"([^"]+)"', base)
        if target is None or target[1] != target_units[key]:
            raise ValueError(f"unsupported standard-cell {key}")
        text = text[:match.start(1)] + target[1] + text[match.end(1):]
    if not re.search(r'capacitive_load_unit\s*\(\s*1\s*,\s*pf\s*\)', text, re.I):
        raise ValueError("unsupported FakeRAM capacitance unit")
    if not re.search(r'capacitive_load_unit\s*\(\s*1\s*,\s*ff\s*\)', base, re.I):
        raise ValueError("unsupported standard-cell capacitance unit")
    text = re.sub(r'capacitive_load_unit\s*\(\s*1\s*,\s*pf\s*\)',
                  "capacitive_load_unit (1,ff)", text, flags=re.I)

    def scale_numbers(match: re.Match) -> str:
        return match[1] + re.sub(NUMBER, lambda n: f"{float(n[0]) * 1000:.12g}",
                                match[2]) + match[3]

    # BSG tables contain only quoted numeric rows and continuation characters.
    text = re.sub(r'(\b(?:index_[123]|values)\s*\()([^;{}]*)(\))', scale_numbers, text)
    scalars = ("capacitance", "rise_capacitance", "fall_capacitance", "max_capacitance",
               "min_capacitance", "min_period", "max_transition", "default_max_transition",
               "min_pulse_width_high", "min_pulse_width_low")
    text = re.sub(r'(\b(?:' + '|'.join(scalars) + r')\s*:\s*)(' + NUMBER + r')',
                  lambda m: m[1] + f"{float(m[2]) * 1000:.12g}", text)
    text = re.sub(r'(\b(?:cell_leakage_power|default_cell_leakage_power)\s*:\s*)('
                  + NUMBER + r')', lambda m: m[1] + f"{float(m[2]) * 1e6:.12g}", text)
    return text


def validate_lef(path: Path) -> dict:
    """Catch the historical HighTide write-data direction bug, including upper bits."""
    text = path.read_text()
    pins = re.findall(r'\bPIN\s+(\S+)\s+.*?\bDIRECTION\s+(\w+)\s*;', text, re.S)
    for name, direction in pins:
        expected = "OUTPUT" if "_rd_out[" in name else "INPUT"
        if any(field in name for field in ("_wd_in[", "_addr_in[", "_wmask_in[", "_rd_out[")):
            if direction != expected:
                raise ValueError(f"{path}: {name} must be {expected}, found {direction}")
    if not pins or not any("_rd_out[" in name for name, _ in pins):
        raise ValueError(f"{path}: no read-data pins")
    size = re.search(r'\bSIZE\s+([\d.]+)\s+BY\s+([\d.]+)', text)
    if size is None:
        raise ValueError(f"{path}: no macro dimensions")
    return {"pins": len(pins), "width_um": float(size[1]), "height_um": float(size[2])}


def prepare(tc: Toolchain, configs: list[Path], out: Path) -> Toolchain:
    """Generate declared views and use the same cells/LEFs for every producer.

    Config files follow HighTide's JSON `srams` schema. They describe explicit
    macro instances; this function never replaces inferred RTL memories or
    guesses their read latency, clocking, reset, masking or collision semantics.
    """
    if not configs:
        return tc
    if "fakeram_generator" not in tc.physical:
        raise ValueError("FakeRAM generator not staged; initialize HighTide/tools/bsg_fakeram")
    srams = {}
    for path in configs:
        for sram in json.loads(path.read_text())["srams"]:
            name = sram["name"]
            if name in srams and sram != srams[name]:
                raise ValueError(f"conflicting SRAM definitions: {name}")
            srams[name] = sram
    # Use ASAP7's actual M4/M5 pin stack and 54 nm x 270 nm site grid.
    config = {"tech_nm": 7, "voltage": 0.7, "metalPrefix": "M", "LRpinWidth_nm": 24,
              "LRpinPitch_nm": 48, "TBpinWidth_nm": 18, "TBpinPitch_nm": 36,
              "manufacturing_grid_nm": 1, "contacted_poly_pitch_nm": 54,
              "column_mux_factor": 3, "fin_pitch_nm": 27, "flipPins": False,
              "snapWidth_nm": 54, "snapHeight_nm": 270, "srams": list(srams.values())}
    out.mkdir(parents=True, exist_ok=False)
    cfg = out / "fakeram-asap7.json"
    cfg.write_text(json.dumps(config, indent=2) + "\n")
    with (out / "generation.log").open("w") as log:
        subprocess.run([str(tc.bin("python")), tc.physical["fakeram_generator"], str(cfg),
                        "--output_dir", str(out / "raw")], stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=120)
    base = tc.tech("asap7").liberty[0]
    library_text = base.read_text()
    libs, lefs, models = [], [], {}
    for name in srams:
        directory = out / "raw" / name
        normalized = out / (name + ".lib")
        normalized.write_text(normalize_bsg_lib((directory / (name + ".lib")).read_text(),
                                                library_text))
        lef = directory / (name + ".lef")
        models[name] = {**srams[name], **validate_lef(lef)}
        libs.append(str(normalized))
        lefs.append(str(lef))
    merged = out / "asap7-with-srams.lib"
    subprocess.run([str(tc.bin("python")), tc.physical["merge_script"], str(merged),
                    str(base), *libs], check=True, timeout=120)
    digest = hashlib.sha256(merged.read_bytes()).hexdigest()
    tech = Tech("asap7", [merged], hashlib.sha256(digest.encode()).hexdigest()[:16], "ps")
    physical = {**tc.physical, "lefs": [*tc.physical["lefs"], *lefs],
                "macro_liberty": libs, "sram_models": models}
    (out / "models.json").write_text(json.dumps({
        "generator_revision": tc.physical["fakeram_revision"],
        "models": models, "liberty_sha256": digest, "units": "ps / fF"}, indent=2) + "\n")
    return replace(tc, _tech={**tc._tech, "asap7": tech}, physical=physical)
