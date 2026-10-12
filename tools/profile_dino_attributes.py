#!/usr/bin/env python3
"""Measure source-guided Dino through a snapshotted lhdsuite bench/synth.sh.

Sources come from the maintained dino/pyrope2 tree. The adapter selects ASAP7
and applies the same fixed arithmetic options to both variants. Flat and guided
consume identical annotated source bytes after an unbounded source-equivalence
gate. Flat deliberately ignores colors; neither variant explores arithmetic.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]
from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import Test, discover
from lhdtrack.context import FlowError, FlowSkip
from lhdtrack.ledger import Ledger
from lhdtrack.run import Job, Runner
from lhdtrack.toolchain import Toolchain
from qor_endpoint import emitted_verilog, evaluate


class MaintainedDino(Test):
    @property
    def pyrope_dir(self) -> Path:
        return self.root / "pyrope2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-root", type=Path, required=True)
    parser.add_argument("--suite", type=Path, default=ROOT.parent / "lhdsuite")
    parser.add_argument("--groups", choices=("execute-fetch", "stages", "pipeline", "lanes"),
                        default="execute-fetch")
    parser.add_argument("--mode", action="append",
                        choices=("flat", "guided", "warm", "alu_edit", "control_edit", "shared_edit"))
    args = parser.parse_args()
    tc = Toolchain.load(args.toolchain_root)
    if not tc.has("bash"):
        parser.error("stage the benchmark shell as bin.bash in the isolated toolchain registry")
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-dino-attributes"
    archive = ROOT / "var/runs" / run
    sources = archive / "source/dino"
    plain_sources = archive / "source-plain/dino/pyrope2"
    shutil.copytree(args.suite / "dino/pyrope2", plain_sources)
    shutil.copytree(plain_sources, sources / "pyrope2")
    (sources / "constraints").mkdir()
    (sources / "constraints/asap7.sdc").write_text(
        "create_clock -name clk -period 400 [get_ports clock]\n"
        "set_input_delay -clock clk 0 [all_inputs -no_clocks]\n"
        "set_output_delay -clock clk 0 [all_outputs]\n")
    top = sources / "pyrope2/PipelinedDualIssueCPU.prp"
    plain = top.read_text()
    groups = {name: "execute" for name in (
        "pipeA_alu", "pipeB_alu", "pipeA_ex_mem", "pipeB_ex_mem",
        "pipeA_ex_mem_ctrl", "pipeB_ex_mem_ctrl")}
    groups.update({name: "fetch" for name in ("pipeA_if_id", "pipeB_if_id")})
    if args.groups == "stages":
        groups.update({name: name for name in re.findall(
            r"const (pipe[AB]_[a-zA-Z0-9_]+)\s*= StageReg<", plain)})
    elif args.groups == "lanes":
        groups.update({name: "lane" + name[4] for name in re.findall(
            r"const (pipe[AB]_[a-zA-Z0-9_]+)\s*=", plain)})
    elif args.groups == "pipeline":
        groups = dict.fromkeys(groups, "execute")
    annotated = plain
    for name, group in groups.items():
        annotated, count = re.subn(r"(const " + re.escape(name) + r")(?=\s*=)",
                                  lambda m: m[1] + '::[synth.color="' + group + '"]', annotated)
        assert count == 1, (name, count)
    pc_group = "execute" if args.groups == "pipeline" else "fetch"
    annotated = annotated.replace("reg pc:U64:[reset_pin=rst]",
                                  'reg pc:U64:[synth.color="' + pc_group + '",reset_pin=rst]')
    assert annotated != plain
    top.write_text(annotated)
    (archive / "annotation.diff.txt").write_text("\n".join(
        line for line in annotated.splitlines() if "synth.color" in line) + "\n")
    bench = archive / "runfiles/_main/bench"
    bench.mkdir(parents=True)
    for name in ("common.sh", "synth.sh"):
        shutil.copy2(args.suite / "bench" / name, bench / name)
    script = (bench / "synth.sh").read_text()
    script = script.replace("\nrequire_tech_dir\n", '\n: "${BENCH_LIBERTY:?}" "${PDK_VERSION:?}"\n')
    script = script.replace('LIB="$HAGENT_TECH_DIR/sky130_fd_sc_hd__tt_025C_1v80.lib"',
                            'LIB="$BENCH_LIBERTY"')
    script = script.replace("ABC_ARGS=()",
                            'ABC_ARGS=(--set "synth.liberty=$LIB" '
                            '--set synth.multiplier=sn --set synth.tune.profile=off)')
    script = script.replace('"$q_delay" ns', '"$q_delay" ps').replace('"$STA_DELAY" ns', '"$STA_DELAY" ps')
    (bench / "synth.sh").write_text(script)
    (archive / "adapter.json").write_text(json.dumps({
        "original_synth_sha256": hashlib.sha256((args.suite / "bench/synth.sh").read_bytes()).hexdigest(),
        "adapted_synth_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (sources / "pyrope2").glob("*.prp")},
        "source_origin": str((args.suite / "dino/pyrope2").resolve()),
        "flat_ignores_grouping": True, "period_ps": 400, "group_policy": args.groups,
    }, indent=2) + "\n")
    template = discover(ROOT, ["add"])[0]
    test = replace(template, root=sources, name="dino_pyrope2", top="PipelinedDualIssueCPU",
                   kind="sequential", suite="external", pyrope_status="idiomatic", lec_status="none",
                   lec_gate=False, configs=[replace(template.configs[0], id="core")])
    test = MaintainedDino(**vars(test))
    cfg = load_config(ROOT)
    cfg["run"]["lec_timeout_s"] = 900
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    identity = {**runner.identity(), "dino_synthesis_attributes": True, "group_policy": args.groups, "measurement_jobs": 1,
                "toolchain_snapshot": str(tc.write_snapshot(archive / "toolchain-snapshot"))}
    ledger, captured = Ledger(ROOT), []
    guided_work = None
    annotation_proven = False
    original_sources = {p: p.read_bytes() for p in (sources / "pyrope2").glob("*.prp")}
    suite_spec = importlib.util.spec_from_file_location("suite_ledger", args.suite / "bench/ledger.py")
    suite_ledger = importlib.util.module_from_spec(suite_spec)
    suite_spec.loader.exec_module(suite_ledger)

    def prove_annotation(ctx):
        nonlocal annotation_proven
        ctx.run("source_lec", [ctx.tool("lhd"), "lec", "--impl", top,
                "--ref", plain_sources / top.name, "--top", test.top,
                "--workdir", "Wlec", "--set", "formal.timeout=30",
                "--result-json", "source-lec.json"], timeout=90)
        proof = json.loads((ctx.work / "source-lec.json").read_text()).get("lec", {})
        annotation_proven = proof.get("verdict") == "proven" and not proof.get("bounded", True)
        if not annotation_proven:
            raise FlowError("annotation equivalence did not produce an unbounded proof: " + str(proof))
        return {"lec": {**proof, "obligation": "annotated-pyrope2-vs-plain-pyrope2"}}

    def measure(ctx, variant):
        nonlocal guided_work
        if not annotation_proven:
            raise FlowSkip("source annotation equivalence was not proven")
        alg = "flat" if variant == "flat" else "synth"
        for path, contents in original_sources.items():
            path.write_bytes(contents)
        edits = {
            "alu_edit": ("ALU.prp", "io_inputx + io_inputy", "io_inputx ^ io_inputy"),
            "control_edit": ("Control.prp", "io_opcode == enc.op_register or", "io_opcode == enc.op_immediate or"),
            "shared_edit": ("StageReg.prp", "reg reg_0:T:[reset_pin=clear] = 0", "reg reg_0:T:[reset_pin=clear] = 1"),
        }
        if variant in edits:
            filename, old, new = edits[variant]
            path = sources / "pyrope2" / filename
            text = path.read_text()
            assert old in text
            path.write_text(text.replace(old, new, 1))
        if variant not in ("flat", "guided"):
            assert guided_work
            (ctx.work / "W_synth").symlink_to(guided_work / "W_synth", target_is_directory=True)
        if variant == "guided":
            guided_work = ctx.work
        tc._env["bash"] = {**tc._env.get("lhd", {}),
            "TEST_SRCDIR": str(archive / "runfiles"), "TEST_TMPDIR": str(ctx.work),
            "TEST_UNDECLARED_OUTPUTS_DIR": str(ctx.work), "LHD": str(tc.bin("lhd")),
            "CORE": "dino", "CORE_TOP": test.top, "CORE_UNIT": "ALU",
            "CORE_P_TOP": str(top), "CORE_V_FLIST": str(args.suite.resolve() / "dino/verilog/filelist.f"),
            "CORE_COLOR_ALGS": alg, "MODE": "cold", "BENCH_LIBERTY": str(ctx.liberty[0]),
            "PDK_VERSION": "asap7-" + ctx.tech.sha256, "BENCH_PROGRESS": "0",
            "BENCH_ABC_ADDER": "brent", "BENCH_ABC_DELAY": "400",

        }
        ctx.run("bench", [ctx.tool("bash"), bench / "synth.sh"], timeout=1800)
        ctx.run("emit", [ctx.tool("lhd"), "compile", "lg:net_" + alg,
                         "--top", test.top + "." + test.top,
                         "--emit-dir", "verilog:netv", "--workdir", "Wemit"])
        netlist = emitted_verilog(ctx, ctx.work / "netv")
        result = {"netlist": netlist, **evaluate(ctx, netlist, lgraph=ctx.work / ("net_" + alg),
                                                qor_json=ctx.work / (alg + "_qor.json"))}
        result["qor"].update({"liberty_sha256": ctx.tech.sha256,
                              "sdc_sha256": hashlib.sha256(ctx.sdc.read_bytes()).hexdigest(),
                              "flat_ignores_grouping": variant == "flat",
                              "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in original_sources}})
        metric_file = ctx.work / "metrics.jsonl"
        if metric_file.exists():
            result["qor"]["suite_metrics"] = [json.loads(line) for line in metric_file.read_text().splitlines()]
        return result

    def finished(row):
        runner.gate([row])
        ledger.append(identity, [row])
        captured.append(row.to_dict())
        (archive / "results.json").write_text(json.dumps({"identity": identity, "rows": captured}, indent=2) + "\n")
        print(row.flow, row.status, row.qor.get("cells"), row.sta.get("opensta_ns"), row.time_ms, flush=True)
        if row.kind == "lec":
            return
        variant = row.flow.removeprefix("syn_dino_attributes_")
        metrics = {m["name"]: m["value"] for m in row.qor.get("suite_metrics", [])}
        alg = "flat" if variant == "flat" else "synth"
        suite_row = {"target": "dino_pyrope2", "phase": "synth",
                     "mode": "incremental" if variant == "warm" else "edit" if variant.endswith("_edit") else "cold",
                     "config_id": run + "-" + variant, "wall_ms": row.time_ms.get("bench", 0),
                     "passed": row.status == "ok", "regions": row.qor.get("regions"),
                     "gates": row.qor.get("cells"), "area": row.qor.get("area_um2"),
                     "abc_ms": metrics.get(alg + "_abc_ms"),
                     "compile_ms": metrics.get("compile_" + alg + "_ms"),
                     "color_ms": metrics.get(alg + "_color_ms"),
                     "cache_hits": metrics.get(alg + "_cache_hits"),
                     "cache_misses": metrics.get(alg + "_cache_misses"),
                     "lhd_binary_version": tc.version("lhd"), "delay_target_ps": 400,
                     "flat_ignores_grouping": variant == "flat",
                     "correctness": ("base source annotation equivalence proven; deliberate functional edit; mapped netlist not proved"
                                     if variant.endswith("_edit") else
                                     "source annotation equivalence proven; mapped netlist not proved"),
                     "source_sha256": row.qor.get("source_sha256")}
        # The suite scoreboard uses ns; lhdtrack keeps the ASAP7-native ps.
        if row.sta.get("opensta_ns") is not None:
            suite_row["sta_delay"] = row.sta["opensta_ns"] / 1000
        if row.qor.get("abc_max_delay_ns") is not None:
            suite_row["max_delay"] = row.qor["abc_max_delay_ns"] / 1000
        path = archive / (variant + "-suite-row.json")
        path.write_text(json.dumps(suite_row) + "\n")
        os.environ["PDK_VERSION"] = "asap7-" + tc.tech("asap7").sha256
        suite_ledger.cmd_add(argparse.Namespace(path=str(path), config_id="", flow="synth",
                                              merge=False, dry_run=False), args.suite.resolve())
        if not any(r["flow"] == "syn_dino_attributes_guided" for r in captured):
            return
        suite_ledger.cmd_render(argparse.Namespace(
            flow="synth", out=str(ROOT.parent / "livehd/current_opt_loop_synth.html"),
            title="Dino Pyrope2 ASAP7 synthesis attributes", baseline=run + "-flat", current=run + "-guided",
            also_current=([run + "-warm"] if any(
                r["flow"] == "syn_dino_attributes_warm" for r in captured) else []),
            previous="", host=identity["host"],
            epsilon=3.0, fail_on_regression=False), args.suite.resolve())

    proof_module = ModuleType("lec_dino_attributes_source")
    proof_module.KIND, proof_module.NEEDS, proof_module.USES_TECH = "lec", ("lhd",), False
    proof_module.run = prove_annotation
    jobs = [Job(test, test.configs[0], None, proof_module.__name__, proof_module, Path(__file__))]
    for variant in args.mode or ("flat", "guided", "warm", "alu_edit", "control_edit", "shared_edit"):
        module = ModuleType("syn_dino_attributes_" + variant)
        module.KIND, module.NEEDS, module.OPTIONAL, module.USES_TECH = "synth", ("lhd", "yosys", "bash"), ("sta",), True
        module.run = lambda ctx, selected=variant: measure(ctx, selected)
        jobs.append(Job(test, test.configs[0], "asap7", module.__name__, module, Path(__file__)))
    print(run, "Dino Pyrope2: matched flat, guided and warm", flush=True)
    runner.execute(jobs, jobs_parallel=1, on_done=finished)
    print(archive / "results.json", flush=True)
    return int(any(row["status"] == "failed" for row in captured))


if __name__ == "__main__":
    raise SystemExit(main())
