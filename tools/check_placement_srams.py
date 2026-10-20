#!/usr/bin/env python3
"""Exercise matched HighTide FakeRAM views through native and OpenROAD pre-CTS.

This is a technology integration fixture, separate from corpus performance rows.
It also compares original ns/pF and converted ps/fF models on the same placed DEF.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]
from lhdtrack.context import FlowContext
from lhdtrack.corpus import Config, discover
from lhdtrack.srams import prepare
from lhdtrack.toolchain import Toolchain
from lib.placement import run_native, run_openroad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    tc = Toolchain.load(ROOT)
    declaration = {"name": "fakeram_128x256_1r1w", "width": 128, "depth": 256,
                   "banks": 1, "no_wmask": "true", "ports": {"r": 1, "w": 1, "rw": 0}}
    config = root / "srams.json"
    config.write_text(json.dumps({"srams": [declaration]}, indent=2) + "\n")
    tc = prepare(tc, [config], root / "models")
    tc.write_snapshot(root)
    source = root / "source"
    (source / "verilog").mkdir(parents=True)
    (source / "constraints").mkdir()
    (source / "verilog/sram_smoke.sv").write_text('''
module sram_smoke(input clk,we,input [7:0] wa,ra,input [127:0] din,output [127:0] dout);
wire [127:0] data=din+128'd7;
fakeram_128x256_1r1w ram(.r0_clk(clk),.r0_ce_in(1'b1),.r0_addr_in(ra),.r0_rd_out(dout),
.w0_clk(clk),.w0_ce_in(1'b1),.w0_we_in(we),.w0_addr_in(wa),.w0_wd_in(data));
endmodule
''')
    (source / "verilog/filelist.f").write_text("sram_smoke.sv\n")
    (source / "constraints/asap7.sdc").write_text(
        "create_clock -name clk -period 1000 [get_ports clk]\n"
        "set_input_delay 0 -clock clk [all_inputs -no_clocks]\n"
        "set_output_delay 0 -clock clk [all_outputs]\n")
    test = replace(discover(ROOT, ["add"])[0], root=source, name="sram_model_check",
                   top="sram_smoke", configs=[Config("128x256")])
    results = {}
    variants = (("native_synth", run_native, {}), ("native_place", run_native, {"place": True}),
                ("orfs_synth", run_openroad, {}), ("orfs_place", run_openroad, {"place": True}))
    for name, function, options in variants:
        work = root / name
        work.mkdir()
        ctx = FlowContext(test, Config("128x256"), tc.tech("asap7"), tc, work, work / "logs")
        print(f"running {name}", flush=True)
        try:
            data = function(ctx, **options)["qor"]["placement_comparison"]
            if data["score"]["macros"] != 1:
                raise ValueError("expected one retained SRAM macro")
            results[name] = {key: data[key] for key in ("score", "measurement", "artifacts")}
        except Exception as error:
            results[name] = {"error": str(error)}
        (root / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
    if any("error" in result for result in results.values()):
        print(root / "validation.json")
        return 1
    # Original BSG and converted models must have identical physical timing.
    work = root / "native_place"
    out = work / "score-original-ns"
    out.mkdir()
    env = {"SCORE_INPUT": str(work / "physical/out.def"), "SCORE_STAGE": "place",
           "SCORE_TOP": test.top, "SCORE_OUT": str(out),
           "LIB_FILES": " ".join([str(Toolchain.load(ROOT).tech("asap7").liberty[0]),
               str(root / "models/raw/fakeram_128x256_1r1w/fakeram_128x256_1r1w.lib")]),
           "TECH_LEF": tc.physical["lefs"][0], "SC_LEF": tc.physical["lefs"][1],
           "ADDITIONAL_LEFS": " ".join(tc.physical["lefs"][2:]),
           "SDC_FILE": str(test.sdc_for("asap7")), "SET_RC_TCL": tc.physical["tech_rc"]}
    with (out / "run.log").open("w") as log:
        subprocess.run([str(tc.bin("openroad")), "-exit", tc.physical["score_script"]],
                       env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=120)
    original = json.loads((out / "score.json").read_text())
    converted = results["native_place"]["score"]
    fields = ("setup_wns_ps", "setup_tns_ps", "hold_wns_ps", "hold_tns_ps",
              "instance_area_um2", "macro_area_um2")
    comparison = {key: {"original": original[key], "converted": converted[key],
                        "difference": original[key] - converted[key]} for key in fields}
    (root / "unit-validation.json").write_text(json.dumps(comparison, indent=2) + "\n")
    if any(abs(value["difference"]) > 1e-3 for value in comparison.values()):
        raise ValueError("FakeRAM unit normalization changed timing or area")
    print(f"four flows passed; unit conversion agrees: {root / 'validation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
