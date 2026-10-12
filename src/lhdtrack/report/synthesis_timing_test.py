import unittest

from .synthesis_timing import lhd_steps, profile_pairs, timing_section


class SynthesisTimingTest(unittest.TestCase):
    def row(self, run, flow, **extra):
        return {"run_id": run, "flow": flow, "test": "add", "config": "bw8",
                "tech": "asap7", "status": "ok", "synthesis_timing_profile": True,
                "host_class": "machine", "qor": {"liberty_sha256": "lib"}, **extra}

    def test_latest_complete_pair_keeps_failures_and_ignores_partial_runs(self):
        lhd, sn = "syn_lhd_verilog_no_satopt", "syn_abc_sn"
        history = [self.row("a", lhd), self.row("a", sn), self.row("b", lhd),
                   self.row("b", sn, status="failed"), self.row("c", lhd)]
        pairs = profile_pairs(history)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]["run_id"], "b")
        self.assertEqual(pairs[0][1]["status"], "failed")
        self.assertIn("SN: failed", timing_section(history, "host"))

    def test_duplicate_phases_are_summed_without_subtracting_region_work(self):
        phases = [{"name": "pass.cprop", "ms": 1}, {"name": "pass.cprop", "ms": 2},
                  {"name": "pass.abc", "ms": 1000}]
        lhd = self.row("a", "syn_lhd_verilog_no_satopt", qor={
            "liberty_sha256": "lib", "timing_breakdown": {
                "phases": phases, "regions": [{"ms": 1500, "ware_trials": 2},
                                               {"ms": 1500, "ware_trials": 1}]}})
        sn = self.row("a", "syn_abc_sn")
        self.assertEqual(lhd_steps(lhd)["pass.cprop"], 3)
        rendered = timing_section([lhd, sn], "host")
        self.assertIn("<td>1.000</td>", rendered)
        self.assertIn("<td>3</td>", rendered)

    def test_mismatched_library_is_visible_without_comparing_values(self):
        lhd = self.row("a", "syn_lhd_verilog_no_satopt")
        sn = self.row("a", "syn_abc_sn", qor={"liberty_sha256": "other"})
        self.assertIn("Incompatible host class or Liberty", timing_section([lhd, sn], "host"))

    def test_trial_ablation_never_replaces_default_profiles(self):
        history = [self.row("a", "syn_lhd_verilog_no_satopt"),
                   self.row("a", "syn_abc_sn"),
                   self.row("b", "syn_lhd_verilog_no_satopt_ware_cutoff",
                            synthesis_timing_variant="ware-cutoff-1"),
                   self.row("b", "syn_abc_sn_ware_cutoff",
                            synthesis_timing_variant="ware-cutoff-1")]
        self.assertEqual(profile_pairs(history)[0][0]["run_id"], "a")
        self.assertEqual(profile_pairs(history, "ware-cutoff-1")[0][0]["run_id"], "b")

    def test_brent_profile_never_uses_default_auto_measurement(self):
        history = [self.row("a", "syn_lhd_verilog_no_satopt"), self.row("a", "syn_abc_sn"),
                   self.row("b", "syn_lhd_verilog_abc_sn", synthesis_timing_variant="brent"),
                   self.row("b", "syn_abc_sn", synthesis_timing_variant="brent")]
        self.assertEqual(profile_pairs(history)[0][0]["run_id"], "a")
        self.assertEqual(profile_pairs(history, "brent")[0][0]["run_id"], "b")
        self.assertIn("zero arithmetic trials", timing_section(history, "host", "brent"))


if __name__ == "__main__":
    unittest.main()
