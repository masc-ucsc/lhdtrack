"""A fresh evaluation must not inherit old LiveHD measurements or proofs."""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lhdtrack.report.verilog_eval import (
    bounded_yosys_evidence, geomean_ratios, lec_time_geomean, proof_covers_digest,
    logic_gate_section, metric_values, proof_ok, satopt_lec_effect, select_rows,
    simulation_section, synth_proof, _synth_row, retained_usyn_simulation_section,
    verify_transparent_instance_renaming,
    mapper_chart_data, write_evaluation,
)


class VerilogEvaluation(unittest.TestCase):
    def test_retained_simulation_compares_same_bytes_without_pyrope_columns(self):
        spec = {"host": "satsuma", "tech": "asap7", "run_id": "synthesis"}
        common = {"host": "satsuma", "host_class": "cpu", "tech": "asap7", "kind": "sim",
                  "run_id": "timing", "source_usyn_run": "synthesis", "suite": "comb",
                  "test": "dut", "config": "one", "status": "ok", "pyrope_status": "auto",
                  "versions": {"lhd": "frozen", "cxx": "same"},
                  "time_ms": {"setup": 100, "cc": 200}}
        simulation = {"source_run": "synthesis", "netlist_sha256": "netlist",
                      "liberty_sha256": "liberty", "cycles": 100, "checksum": "123",
                      "build_jobs": 32, "measurement_jobs": 1, "exec_repetitions": 3}
        rows = [{**common, "flow": "sim_retained_usyn_netlist_" + name,
                 "sim": {**simulation, "backend": backend, "exec_ms": ms,
                         "cycles_per_s": 100000 // ms}}
                for name, backend, ms in (("verilator", "verilator", 40),
                                          ("lhd_verilog", "slop", 20),
                                          ("lhd_verilog_llvm", "llvm", 10))]
        html = retained_usyn_simulation_section(rows, spec)
        self.assertIn("LHD Verilog", html)
        self.assertNotIn("Pyrope", html)
        self.assertNotIn("pyrope:", html)
        self.assertIn('id="chart-usyn-sim"', html)
        self.assertIn('id="chart-usyn-sim-llvm"', html)
        self.assertIn("Setup + compile", html)
        self.assertIn("2.00×", html)
        # An unrelated artifact remains visible but must not produce a ratio.
        wrong = {**rows[1], "sim": {**rows[1]["sim"], "netlist_sha256": "different"}}
        html = retained_usyn_simulation_section([rows[0], wrong, rows[2]], spec)
        self.assertNotIn('id="chart-usyn-sim"', html)
        self.assertNotIn('id="chart-usyn-sim-llvm"', html)
        # A historical single execution is a correctness check, not a baseline.
        validation = {**rows[0], "sim": {**rows[0]["sim"], "validation_only": True}}
        html = retained_usyn_simulation_section([validation, *rows[1:]], spec)
        self.assertNotIn('id="chart-usyn-sim"', html)
        self.assertIn("correctness-only", html)

    def test_retained_simulation_does_not_pair_runs_or_hide_latest_failure(self):
        from lhdtrack.report.html import _same_netlist_measurement
        row = {"run_id": "new", "host": "satsuma", "host_class": "cpu",
               "versions": {"lhd": "current", "cxx": "same"}, "sim": {
                   "source_run": "synthesis", "netlist_sha256": "netlist",
                   "liberty_sha256": "liberty", "cycles": 100, "checksum": "123",
                   "build_jobs": 32, "measurement_jobs": 1, "exec_repetitions": 3}}
        self.assertTrue(_same_netlist_measurement(row, row))
        for field in ("source_run", "netlist_sha256", "liberty_sha256", "cycles", "checksum",
                      "build_jobs", "measurement_jobs", "exec_repetitions"):
            with self.subTest(field=field):
                self.assertFalse(_same_netlist_measurement(
                    row, {**row, "sim": {**row["sim"], field: "different"}}))
        self.assertFalse(_same_netlist_measurement(row, {**row, "run_id": "old"}))
        unknown = {**row, "versions": {}}
        self.assertFalse(_same_netlist_measurement(unknown, unknown))
        spec = {"host": "satsuma", "tech": "asap7", "run_id": "synthesis"}
        base = {**row, "tech": "asap7", "kind": "sim", "suite": "comb", "test": "dut",
                "config": "one", "source_usyn_run": "synthesis",
                "flow": "sim_retained_usyn_netlist_lhd_verilog", "status": "failed",
                "note": "recorded checksum differs"}
        old = {**base, "run_id": "earlier", "status": "ok"}
        html = retained_usyn_simulation_section([base, old], spec)
        self.assertIn("failed", html)
        self.assertIn("recorded checksum differs", html)

    def test_split_evaluation_keeps_selected_proofs_and_source_simulation_separate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            spec = {"host": "test-host", "run_id": "fresh", "tech": "asap7",
                    "versions": {"lhd": "current"}, "liberty_sha256": "same", "time_unit": "ps",
                    "slots": [{"test": "dut", "config": "one"},
                              {"test": "missing", "config": "one"}],
                    "baselines": [{"test": "dut", "config": "one", "run_id": "base"}],
                    "satopt_profiles": [False], "auxiliary_report": "report-test-host-full.html"}
            path = root / "data/verilog-eval-test-host.json"
            path.write_text(json.dumps(spec))
            common = {"host": "test-host", "host_class": "cpu", "suite": "comb",
                      "test": "dut", "config": "one", "tech": "asap7", "run_id": "fresh",
                      "date": "2026-10-06", "versions": spec["versions"], "status": "ok"}
            rows = [{**common, "kind": "synth", "run_id": "base", "flow": "syn_yosys_abc",
                     "qor": {"area_um2": 10}},
                    {**common, "kind": "synth", "flow": "syn_lhd_verilog_no_satopt",
                     "qor": {"area_um2": 5, "netlist_sha256": "mapped"}},
                    {**common, "kind": "lec", "flow": "lec_netlist_verilog_no_satopt",
                     "lec_verilog_result": {"verdict": "proven", "bounded": True,
                                            "bound": 6, "netlist_sha256": "mapped", "ms": 10},
                     "lec_aux_result": {"verdict": "timeout", "ms": 1000}},
                    {**common, "kind": "sim", "flow": "sim_verilator", "tech": None,
                     "sim": {"cycles": 100, "exec_ms": 5}}]
            ledger = root / "data/ledger-test-host.jsonl"
            original = "".join(json.dumps(row) + "\n" for row in rows)
            ledger.write_text(original)
            from lhdtrack.report.html import write_all
            outputs = write_all(root, only="test-host")
            synth = (root / "target/results-syn-test-host.html").read_text()
            lec = (root / "target/results-lec-test-host.html").read_text()
            sim = (root / "target/results-sim-test-host.html").read_text()
            self.assertIn('id="chart-eval-syn-false"', synth)
            self.assertNotIn("Verdict coverage", synth)
            self.assertNotIn("<h2>Simulation", synth)
            self.assertIn("2.000×", synth)
            self.assertIn("Verdict coverage", lec)
            self.assertIn("bounded(6)", lec)
            self.assertIn("not-measured 1/2", lec)
            self.assertNotIn('id="chart-eval-syn', lec)
            self.assertIn("<h2>Simulation", sim)
            self.assertNotIn("Verdict coverage", sim)
            self.assertFalse((root / "target/report-test-host.html").exists())
            self.assertFalse((root / "target/report-test-host-full.html").exists())
            self.assertIn(root / "target/results-syn-test-host-full.html", outputs)
            self.assertEqual(ledger.read_text(), original)
            # Explicitly named historical snapshots still render their selected evaluation.
            out = root / "target/eval-snapshot.html"
            snapshot = write_evaluation(root, path, out=out).read_text()
            self.assertIn("<h2>Synthesis", snapshot)
            self.assertIn("Verdict coverage", snapshot)
            # A direct evaluation call must honor its supplied spec even when
            # it has not been installed as the public host specification.
            alternative = root / "alternate-evaluation.json"
            path.rename(alternative)
            primary = write_evaluation(root, alternative)
            self.assertEqual(primary.name, "results-syn-test-host.html")
            self.assertIn('id="chart-eval-syn-false"', primary.read_text())

    def test_mapper_plot_uses_full_metrics_and_excludes_refuted_pairs(self):
        spec = {"slots": [{"test": "dut", "config": "one"}], "time_unit": "ps"}
        rows = {("dut", "one", "syn_yosys_abc"): {"status": "ok", "qor": {"area_um2": 10}},
                ("dut", "one", "syn_lhd_verilog"): {"status": "ok", "qor": {"area_um2": 5}},
                ("dut", "one", "syn_lhd_verilog_usyn"):
                    {"status": "ok", "qor": {"lhd_area_um2": 2}}}
        data = mapper_chart_data(rows, spec, True)
        self.assertEqual(data["groups"][0]["values"], {"area": {"syn_lhd_verilog": 2}})
        self.assertFalse(data["groups"][0]["solid"])
        rows["dut", "one", "lec_netlist_verilog"] = {"lec_aux_result": {"verdict": "refuted"}}
        self.assertIsNone(mapper_chart_data(rows, spec, True))

    def test_native_state_displays_partial_metrics_without_changing_full_metrics(self):
        flow = "syn_lhd_verilog_usyn_no_satopt"
        row = dict(status="ok", qor=dict(native_state=True, lhd_area_um2=12.5,
                   lhd_cells=42, abc_max_delay_ns=93.75), sta={},
                   time_ms=dict(total=1000), peak_rss_kb=dict(max=2048))
        rows = {("memory", "one", flow): row}
        rendered = _synth_row(rows, "memory", "one", '<td>memory</td>', False, [])
        for value in ("12.50", "42", "93.75", ">logic</span>", ">region</span>", "LEC unverified"):
            self.assertIn(value, rendered)
        self.assertNotIn("native state</span>", rendered)
        self.assertEqual(metric_values(row), (None, None, None, None, 1, 2))
        self.assertNotIn("area_um2", row["qor"])
        row["status"] = "failed"
        self.assertIn("12.50", _synth_row(rows, "memory", "one", '<td>memory</td>', False, []))
        rows["memory", "one", "lec_netlist_verilog_usyn_no_satopt"] = {
            "lec_verilog_result": dict(verdict="refuted")}
        rendered = _synth_row(rows, "memory", "one", '<td>memory</td>', False, [])
        self.assertIn("results excluded", rendered)
        self.assertNotIn("12.50", rendered)

    def test_gate_all_results_include_timeouts_but_exclude_refutations(self):
        spec = dict(host="host", tech="asap7", run_id="run", satopt_profiles=[False],
                    slots=[dict(test="memory", config="one")])
        base = dict(status="ok", host_class="host", liberty_sha256="lib")
        rows = {("memory", "one", "syn_lhd_verilog_no_satopt"):
                dict(base, qor=dict(lhd_cells=10)),
                ("memory", "one", "syn_lhd_verilog_usyn_no_satopt"):
                dict(base, qor=dict(lhd_cells=20)),
                ("memory", "one", "lec_netlist_verilog_usyn_no_satopt"):
                dict(lec_verilog_result=dict(verdict="timeout"))}
        rendered = logic_gate_section(rows, spec)
        self.assertIn("unverified results: 0.500× (n=1)", rendered)
        self.assertIn("proof coverage incomplete", rendered)
        rows["memory", "one", "lec_netlist_verilog_usyn_no_satopt"]["lec_verilog_result"]["verdict"] = "refuted"
        rendered = logic_gate_section(rows, spec)
        self.assertIn("unverified results: no eligible pairs", rendered)
        self.assertIn("results excluded", rendered)
        self.assertNotIn(">20</td>", rendered)

    def test_focused_proof_override_keeps_exact_scope_and_never_falls_back(self):
        flow = "lec_netlist_verilog_no_satopt"
        versions = dict(lhd="fixed")
        spec = dict(host="satsuma", tech="asap7", run_id="proof", synth_run_id="synth",
                    baselines=[], satopt_profiles=[False], versions=versions,
                    proof_overrides=[dict(test="cpu", config="one", flow=flow, run_id="fix")])
        base = dict(host="satsuma", tech="asap7", config="one", versions=versions)
        rows = [dict(base, test="cpu", flow=flow, run_id="proof"),
                dict(base, test="other", flow=flow, run_id="proof"),
                dict(base, test="cpu", flow="syn_lhd_verilog_no_satopt", run_id="synth"),
                dict(base, test="cpu", flow=flow, run_id="fix", versions=dict(lhd="wrong"))]
        selected = select_rows(rows, spec)
        self.assertNotIn(("cpu", "one", flow), selected)
        self.assertIn(("other", "one", flow), selected)
        self.assertIn(("cpu", "one", "syn_lhd_verilog_no_satopt"), selected)
        correction = dict(base, test="cpu", flow=flow, run_id="fix")
        rows.insert(0, correction)
        self.assertIs(select_rows(rows, spec)["cpu", "one", flow], correction)

    def test_simulation_section_keeps_latest_failure_and_excludes_other_hosts(self):
        base = dict(host="satsuma", kind="sim", suite="comb", test="add", config="one",
                    pyrope_status="idiomatic", lec="proven", status="ok", time_ms={},
                    sim=dict(cycles=10, exec_ms=1, cycles_per_s=10000))
        rows = [dict(base, run_id="20261002T000000", flow="sim_lhd_verilog"),
                dict(base, run_id="20261003T000000", flow="sim_verilator"),
                dict(base, run_id="20261003T000000", flow="sim_lhd_verilog", status="failed"),
                dict(base, run_id="20261004T000000", flow="sim_verilator",
                     host="other", test="foreign")]
        html = simulation_section(rows, "satsuma", {})
        self.assertIn("2 observations", html)
        self.assertIn('checksum</span>', html)
        self.assertNotIn("foreign", html)
        self.assertEqual(simulation_section(rows, "unmeasured", {}), "")

    def test_reemit_accepts_only_transparent_instance_renaming(self):
        old = "module top(input d, output q);\nregion u_region(.d(d), .q(q));\nchild u_real(.d(d));\nendmodule\n"
        new = old.replace("u_region", "__flat___region")
        self.assertEqual(verify_transparent_instance_renaming(old, new), 1)
        for bad in (new.replace(".d(d)", ".d(~d)"), new.replace(".q(q)", ".q(d)"), new + "// extra"):
            with self.assertRaises(ValueError):
                verify_transparent_instance_renaming(old, bad)
        self.assertEqual(verify_transparent_instance_renaming(new, new), 0)

    def test_reemission_proof_requires_both_hashes(self):
        block = dict(netlist_sha256="new", emission=dict(kind="transparent_instance_renaming",
                     source_netlist_sha256="old", netlist_sha256="new"))
        self.assertTrue(proof_covers_digest(block, "old"))
        self.assertFalse(proof_covers_digest(block, "different"))
        block["netlist_sha256"] = "changed"
        self.assertFalse(proof_covers_digest(block, "old"))

    def test_lec_time_geomean_pairs_mapper_times_and_keeps_timeouts(self):
        slots = [dict(test=t, config="one") for t in ("a", "b", "bad", "missing", "zero", "nan")]
        rows = {}
        for t, native, yosys, verdict in (
                ("a", 100, 400, "proven"), ("b", 400, 100, "timeout"),
                ("bad", 1, 1000, "refuted"), ("missing", 10, None, "proven"),
                ("zero", 0, 100, "proven"), ("nan", 10, float("nan"), "proven")):
            rows[t, "one", "lec_netlist_verilog"] = {
                "lec_verilog_result": {"ms": native, "verdict": verdict},
                "lec_aux_result": {"ms": yosys, "verdict": "proven"}}
        ratio, count = lec_time_geomean(rows, slots, "abc")
        self.assertAlmostEqual(ratio, 1.0)
        self.assertEqual(count, 2)
        self.assertEqual(lec_time_geomean(rows, slots, "usyn"), (None, 0))
        rows["b", "one", "lec_netlist_verilog"]["lec_aux_result"]["verdict"] = "refuted"
        ratio, count = lec_time_geomean(rows, slots, "abc")
        self.assertAlmostEqual(ratio, 4.0)
        self.assertEqual(count, 1)

    def test_lec_time_geomean_excludes_either_timeout_but_keeps_bounded(self):
        slots = [dict(test=t, config="one") for t in ("bounded", "native_timeout", "yosys_timeout")]
        rows = {}
        for t, native, yosys in (("bounded", "proven", "proven"),
                                ("native_timeout", "timeout", "proven"),
                                ("yosys_timeout", "proven", "timeout")):
            rows[t, "one", "lec_netlist_verilog"] = {
                "lec_verilog_result": {"ms": 100, "verdict": native, "bounded": True, "bound": 6},
                "lec_aux_result": {"ms": 400, "verdict": yosys}}
        self.assertEqual(lec_time_geomean(rows, slots, "abc")[1], 3)
        ratio, count = lec_time_geomean(rows, slots, "abc", exclude_timeouts=True)
        self.assertAlmostEqual(ratio, 4.0)
        self.assertEqual(count, 1)
        del rows["bounded", "one", "lec_netlist_verilog"]
        self.assertEqual(lec_time_geomean(rows, slots, "abc", exclude_timeouts=True), (None, 0))

    def test_bounded_oracle_requires_every_success_and_no_refutation(self):
        block = {"verdict": "inconclusive", "ms": 12}
        summary = "BMC: found no counterexample within 6 steps"
        sat = "no model found: SUCCESS!\n" * 6
        result = bounded_yosys_evidence(block, summary, sat, "")
        self.assertEqual((result["verdict"], result["bounded"], result["bound"]),
                         ("proven", True, 6))
        for log, err in ((sat[:-30], ""), (sat + "model found: FAIL", ""), (sat, "ERROR: bad")):
            self.assertEqual(bounded_yosys_evidence(block, summary, log, err), block)
        refuted = {"verdict": "refuted"}
        self.assertEqual(bounded_yosys_evidence(refuted, summary, sat, ""), refuted)

    def test_oracle_timeout_requires_explicit_budget_evidence(self):
        block = {"verdict": "inconclusive"}
        message = "TIMEOUT: bounded miter exhausted the shared equivalence budget (300s)."
        result = bounded_yosys_evidence(block, message, "", "")
        self.assertEqual(result["verdict"], "timeout")
        for log, err in (("model found: FAIL", ""), ("", "ERROR: setup")):
            self.assertEqual(bounded_yosys_evidence(block, message, log, err), block)

    def test_only_named_baseline_and_current_mapper_rows(self):
        base = dict(host="mada4", test="add", config="one", tech="asap7")
        spec = dict(host="mada4", tech="asap7", run_id="new", baselines=[
            dict(test="add", config="one", run_id="baseline"),
        ])
        rows = [
            dict(base, run_id="baseline", flow="syn_yosys_abc"),
            dict(base, run_id="old", flow="syn_lhd_verilog"),
            dict(base, run_id="old", flow="lec_netlist_verilog"),
            dict(base, run_id="new", flow="syn_lhd_verilog_usyn"),
            dict(base, run_id="new", flow="syn_lhd_pyrope"),
            dict(base, run_id="new", flow="syn_lhd_verilog", tech="sky130"),
            dict(base, run_id="new", flow="syn_lhd_verilog", host="another"),
        ]
        selected = select_rows(rows, spec)
        self.assertEqual({k[2] for k in selected}, {"syn_yosys_abc", "syn_lhd_verilog_usyn"})

    def test_proof_rerun_retains_named_synthesis(self):
        base = dict(host="mada4", test="add", config="one", tech="asap7")
        spec = dict(host="mada4", tech="asap7", run_id="proof", synth_run_id="synth", baselines=[])
        rows = [dict(base, run_id=run, flow=flow) for run, flow in (
            ("synth", "syn_lhd_verilog"), ("synth", "lec_netlist_verilog"),
            ("proof", "lec_netlist_verilog"), ("old", "syn_lhd_verilog"))]
        selected = select_rows(rows, spec)
        self.assertEqual(selected["add", "one", "syn_lhd_verilog"]["run_id"], "synth")
        self.assertEqual(selected["add", "one", "lec_netlist_verilog"]["run_id"], "proof")

    def test_cross_host_recheck_excludes_source_timings(self):
        base = dict(test="add", config="one", tech="asap7")
        spec = dict(host="satsuma", synth_host="mada4", tech="asap7",
                    run_id="proof", synth_run_id="synth", baselines=[])
        rows = [
            dict(base, host="mada4", run_id="synth", flow="syn_lhd_verilog"),
            dict(base, host="mada4", run_id="synth", flow="syn_yosys_abc"),
            dict(base, host="mada4", run_id="proof", flow="lec_netlist_verilog"),
            dict(base, host="satsuma", run_id="proof", flow="lec_netlist_verilog"),
        ]
        selected = select_rows(rows, spec)
        self.assertEqual(list(selected), [("add", "one", "lec_netlist_verilog")])
        self.assertEqual(selected["add", "one", "lec_netlist_verilog"]["host"], "satsuma")

    def test_satopt_profiles_keep_separate_rows_and_timings(self):
        base = dict(host="satsuma", test="add", config="one", tech="asap7", run_id="proof")
        spec = dict(host="satsuma", tech="asap7", run_id="proof", baselines=[], satopt_profiles=[True, False])
        rows = [dict(base, flow="lec_netlist_verilog", lec_verilog_result={"ms": 100},
                     lec_aux_result={"ms": 200}),
                dict(base, flow="lec_netlist_verilog_no_satopt", lec_verilog_result={"ms": 50},
                     lec_aux_result={"ms": 200})]
        selected = select_rows(rows, spec)
        self.assertEqual(len(selected), 2)
        slots = [dict(test="add", config="one")]
        ratio, count = lec_time_geomean(selected, slots, "abc")
        self.assertAlmostEqual(ratio, 2.0)
        self.assertEqual(count, 1)
        ratio, count = lec_time_geomean(selected, slots, "abc", satopt=False)
        self.assertAlmostEqual(ratio, 4.0)
        self.assertEqual(count, 1)

    def test_synth_satopt_profiles_pair_without_borrowing_the_proof(self):
        base = dict(host="satsuma", test="add", config="one", tech="asap7", run_id="run", status="ok")
        spec = dict(host="satsuma", tech="asap7", run_id="run", baselines=[], satopt_profiles=[True, False])
        rows = select_rows([dict(base, flow="syn_lhd_verilog", qor=dict(area_um2=8)),
                            dict(base, flow="syn_lhd_verilog_no_satopt", qor=dict(area_um2=16)),
                            dict(base, flow="lec_netlist_verilog",
                                 lec_verilog_result={"verdict": "refuted"})], spec)
        self.assertEqual(len(rows), 3)
        slots = [dict(test="add", config="one")]
        # The refutation covers the satopt=true emission only.
        self.assertEqual(geomean_ratios(rows, slots, "abc", True, baseline="syn_lhd_verilog_no_satopt")[1], (None, 0))
        rows["add", "one", "lec_netlist_verilog"] = {}
        ratio, count = geomean_ratios(rows, slots, "abc", True, baseline="syn_lhd_verilog_no_satopt")[1]
        self.assertAlmostEqual(ratio, 2.0)
        self.assertEqual(count, 1)

    def test_disabled_profile_uses_its_own_proof(self):
        rows = {
            ("add", "one", "lec_netlist_verilog_usyn"):
                {"lec_verilog_result": {"verdict": "proven", "netlist_sha256": "enabled"}},
            ("add", "one", "lec_netlist_verilog_usyn_no_satopt"):
                {"lec_verilog_result": {"verdict": "refuted", "netlist_sha256": "disabled"}},
        }
        proof = synth_proof(rows, ("add", "one"), "usyn", False)
        self.assertEqual(proof["lec_verilog_result"]["netlist_sha256"], "disabled")
        self.assertEqual(proof["lec_verilog_result"]["verdict"], "refuted")

    def test_satopt_lec_effect_pairs_only_double_proofs(self):
        slots = [dict(test=t, config="one") for t in ("a", "b")]
        rows = {("a", "one", "lec_netlist_verilog"): {"lec_verilog_result": {"verdict": "proven", "ms": 10}},
                ("a", "one", "lec_netlist_verilog_no_satopt"): {"lec_verilog_result": {"verdict": "proven", "ms": 40}},
                ("b", "one", "lec_netlist_verilog"): {"lec_verilog_result": {"verdict": "proven", "ms": 10}},
                ("b", "one", "lec_netlist_verilog_no_satopt"): {"lec_verilog_result": {"verdict": "timeout", "ms": 999}}}
        ratio, count, proven = satopt_lec_effect(rows, slots, "abc")
        self.assertAlmostEqual(ratio, 4.0)
        self.assertEqual(count, 1)
        self.assertEqual(proven, {True: 2, False: 1})

    def test_geomean_retains_unverified_and_excludes_refuted(self):
        slots = [dict(test=t, config="one") for t in ("a", "b", "bad")]
        rows = {}
        for t, area in (("a", 5), ("b", 20), ("bad", 1000)):
            rows[t, "one", "syn_yosys_abc"] = dict(status="ok", qor=dict(area_um2=10))
            rows[t, "one", "syn_lhd_verilog"] = dict(status="ok", qor=dict(area_um2=area))
        rows["bad", "one", "lec_netlist_verilog"] = {
            "lec_aux_result": {"verdict": "refuted"}}
        ratios = geomean_ratios(rows, slots, "abc")
        self.assertAlmostEqual(ratios[1][0], 1.0)
        self.assertEqual(ratios[1][1], 2)
        self.assertEqual(ratios[0], (None, 0))
        rows["a", "one", "syn_lhd_verilog"]["qor"]["area_um2"] = 0
        self.assertAlmostEqual(geomean_ratios(rows, slots, "abc")[1][0], 0.5)
        self.assertEqual(geomean_ratios(rows, slots, "abc")[1][1], 1)

    def test_proof_must_cover_exact_netlist_including_bounded(self):
        synth = {"qor": {"netlist_sha256": "mapped"}}
        proven = {"verdict": "proven", "netlist_sha256": "mapped"}
        proof = {"lec_verilog_result": proven, "lec_aux_result": {"verdict": "timeout"}}
        self.assertTrue(proof_ok(synth, proof))
        self.assertTrue(proof_ok(synth, {"lec_verilog_result": {**proven, "bounded": True, "bound": 6}}))
        for update in ({"netlist_sha256": "different"},
                       {"verdict": "timeout"}):
            self.assertFalse(proof_ok(synth, {"lec_verilog_result": {**proven, **update}}))
        self.assertFalse(proof_ok(synth, {
            "lec_verilog_result": proven, "lec_aux_result": {"verdict": "refuted"},
        }))


if __name__ == "__main__":
    unittest.main()
