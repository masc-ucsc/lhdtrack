#!/usr/bin/env python3
"""Render synthesis profiling comparisons from the append-only host ledger."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parents[1]


def render(host: str, runs: list[str]) -> Path:
    rows = []
    proofs = {}
    for line in (ROOT / "data" / f"ledger-{host}.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("run_id") not in runs:
            continue
        if row.get("kind") == "synth":
            rows.append(row)
        elif row.get("flow", "").startswith("lec_pyrope_tune_"):
            proof_key = (row["run_id"], row["test"], row["config"], row["host_class"],
                         row["versions"].get("lhd"), row["flow"].removeprefix("lec_pyrope_tune_"))
            proofs[proof_key] = row.get("lec_result", {}).get("verdict", row["status"])
    groups = {}
    for row in rows:
        row.setdefault("qor", {})
        row.setdefault("sta", {})
        row.setdefault("time_ms", {})
        key = (row["run_id"], row["test"], row["config"], row["host_class"],
               row["qor"].get("liberty_sha256"), row["qor"].get("sdc_sha256"),
               row["versions"].get("lhd"))
        groups.setdefault(key, {})[row["flow"].removeprefix("syn_lhd_pyrope_tune_")] = row
    tables = []
    for key, variants in sorted(groups.items()):
        baseline = variants.get("baseline")
        for variant, row in variants.items():
            qos, sta, times = row["qor"], row["sta"], row["time_ms"]
            cells, delay, elapsed = qos.get("cells"), sta.get("opensta_ns"), times.get("map")
            ratios = []
            for current, source in ((cells, (baseline or {}).get("qor", {}).get("cells")),
                                    (delay, (baseline or {}).get("sta", {}).get("opensta_ns")),
                                    (elapsed, (baseline or {}).get("time_ms", {}).get("map"))):
                ratios.append(source / current if current and source else None)
            replay = variants.get("replay")
            equality = None
            if variant == "profile" and replay and row["status"] == replay["status"] == "ok":
                equality = cells == replay["qor"].get("cells") and delay == replay["sta"].get("opensta_ns")
            tables.append({"run": key[0], "test": key[1], "config": key[2], "variant": variant,
                           "status": row["status"], "cells": cells, "critical_path_ps": delay,
                           "map_ms": elapsed, "compile_ms": times.get("compile"),
                           "color_ms": times.get("color"), "trials": qos.get("arithmetic_trials"),
                           "baseline_over_measured": dict(zip(("cells", "delay", "map"), ratios)),
                           "profile_replay_qor_equal": equality, "compiler": key[6],
                           "mapped_proof": proofs.get((key[0], key[1], key[2], key[3], key[6], variant)),
                           "liberty_sha256": key[4], "source_sha256": qos.get("source_sha256")})
    target = ROOT / "target" / f"results-syn-attributes-{host}"
    target.parent.mkdir(exist_ok=True)
    target.with_suffix(".json").write_text(json.dumps({"host": host, "runs": runs,
                                                       "rows": tables}, indent=2) + "\n")
    esc = lambda value: html.escape(str(value))
    cells = []
    for row in tables:
        ratio = row["baseline_over_measured"]
        values = [row[k] for k in ("test", "run", "compiler", "config", "variant", "status", "cells",
                                   "critical_path_ps", "compile_ms", "color_ms", "map_ms", "trials")]
        values += [f"{ratio[k]:.3f}" if ratio[k] is not None else "—"
                   for k in ("cells", "delay", "map")]
        values += [row["profile_replay_qor_equal"], row["mapped_proof"]]
        cells.append("<tr>" + "".join(f"<td>{esc(v) if v is not None else '—'}</td>"
                                      for v in values) + "</tr>")
    headers = ("Test", "Run", "Compiler", "Config", "Variant", "Status", "Cells", "Critical path (ps)",
               "Compile ms", "Color ms", "Map ms", "Trials", "Cells ratio", "Delay ratio",
               "Map ratio", "Replay matches", "Exact mapped proof")
    compilers = sorted({row["compiler"] for row in tables})
    target.with_suffix(".html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Synthesis attributes</title>"
        "<style>body{font:15px system-ui;margin:2em}table{border-collapse:collapse}"
        "th,td{padding:.4em;border:1px solid #bbb;text-align:right}th:first-child,"
        "td:first-child{text-align:left}</style><h1>ASAP7 synthesis attributes</h1>"
        f"<p>Host: {esc(host)}. Runs: {esc(', '.join(runs))}.</p>"
        f"<p>Frozen compilers: {esc('; '.join(compilers))}.</p>"
        "<p>Ratios are baseline / measured; higher is better. Each ratio uses the same "
        "run, host class, compiler, Liberty and SDC. Profile includes search cost; replay "
        "starts no arithmetic trials. Timing uses OpenSTA on the measured netlist. "
        "Source equivalence and mapped-netlist proof are separate obligations.</p>"
        "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers)
        + "</tr></thead><tbody>" + "".join(cells) + "</tbody></table>\n")
    return target.with_suffix(".html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--run", action="append", required=True)
    args = parser.parse_args()
    print(render(args.host, args.run))
