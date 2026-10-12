"""Rank only the requested run and preserve counterexamples of identical bytes."""
import unittest

from .abc_sn import analyze, FLOWS, latest_full_run


class AbcSnOutliersTest(unittest.TestCase):
    def row(self, flow, **extra):
        return {"host": "host", "host_class": "cpu", "tech": "asap7", "run_id": "full",
                "test": "dut", "config": "one", "flow": flow, "status": "ok",
                "qor": {"netlist_sha256": flow, "liberty_sha256": "lib", "cells": 10,
                        "sdc_sha256": "sdc", "source_sha256": {"dut.sv": "source"}},
                "sta": {"opensta_ns": 100}, "time_ms": {"map": 1000}, **extra}

    def test_ratios_follow_baseline_over_measured_and_ignore_other_runs(self):
        lhd = self.row(FLOWS[0])
        sn = self.row(FLOWS[1], time_ms={"frontend": 100, "map": 1900})
        lhd["qor"]["source_sha256"]["harness.sv"] = "extra-corpus-input"
        report = analyze([lhd, sn, self.row(FLOWS[1], run_id="other", status="failed")],
                         "host", "full")
        self.assertEqual(report["slots"], 1)
        self.assertEqual(report["pairs"][0]["ratios"]["synthesis_s"], 2)
        self.assertEqual(report["statistics"]["synthesis_s"]["lhd_wins_5pct"], 1)

    def test_failed_and_incompatible_rows_are_visible_and_excluded(self):
        lhd, sn = self.row(FLOWS[0]), self.row(FLOWS[1])
        for changed in ({**sn, "status": "failed"}, {**sn, "host_class": "other"}):
            report = analyze([lhd, changed], "host", "full")
            self.assertEqual(report["pairs"][0]["ratios"], {})
            self.assertTrue(report["pairs"][0]["reason"])
        sn["qor"]["source_sha256"]["dut.sv"] = "different"
        self.assertEqual(analyze([lhd, sn], "host", "full")["pairs"][0]["ratios"], {})

    def test_known_counterexample_survives_fresh_identical_synthesis(self):
        lhd, sn = self.row(FLOWS[0]), self.row(FLOWS[1])
        proof = self.row(FLOWS[1], run_id="old-proof", lec_aux_result={
            "verdict": "refuted", "netlist_sha256": FLOWS[1], "liberty_sha256": "lib"})
        report = analyze([proof, lhd, sn], "host", "full")
        self.assertEqual(report["pairs"][0]["sn"]["proof"], "refuted")
        self.assertEqual(report["statistics"]["cells"]["n"], 0)
        proof["qor"]["netlist_sha256"] = "different-emission"
        self.assertEqual(analyze([proof, lhd, sn], "host", "full")["statistics"]["cells"]["n"], 1)

    def test_partial_or_focused_run_cannot_replace_complete_population(self):
        expected = {("dut", "one"), ("other", "one")}
        full = [self.row(flow, test=test) for test, _ in expected for flow in FLOWS]
        partial = [self.row(flow, run_id="newer") for flow in FLOWS]
        self.assertEqual(latest_full_run(full + partial, expected), "full")
        self.assertIsNone(latest_full_run(partial, expected))

    def test_bounded_proof_is_not_labeled_unbounded(self):
        lhd, sn = self.row(FLOWS[0]), self.row(FLOWS[1])
        sn["lec_aux_result"] = {"verdict": "proven", "bounded": True, "bound": 6,
                                "netlist_sha256": FLOWS[1], "liberty_sha256": "lib"}
        self.assertEqual(analyze([lhd, sn], "host", "full")["pairs"][0]["sn"]["proof"],
                         "bounded(6)")


if __name__ == "__main__":
    unittest.main()
