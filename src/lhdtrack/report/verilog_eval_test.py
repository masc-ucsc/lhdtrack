"""A fresh evaluation must not inherit old LiveHD measurements or proofs."""
import unittest

from lhdtrack.report.verilog_eval import (
    bounded_yosys_evidence, geomean_ratios, lec_time_geomean, proof_covers_digest,
    proof_ok, satopt_lec_effect, select_rows, simulation_section, synth_proof,
    verify_transparent_instance_renaming,
)


class VerilogEvaluation(unittest.TestCase):
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
