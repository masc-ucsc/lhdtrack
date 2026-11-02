"""Check statistics eligibility and presentation for retained mapped checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "attribute_report", Path(__file__).with_name("report_synthesis_attributes.py"))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class AttributeCoverage(unittest.TestCase):
    def test_tested_matches_proven_and_refuted_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report.ROOT = root
            (root / "data").mkdir()
            common = dict(run_id="new", test="mul", config="default", host_class="host",
                          versions={"lhd": "compiler"}, status="ok")
            rows = []
            for variant, scale, verdict in [
                ("baseline", 1, "proven"), ("profile", 2, "timeout-time"),
                ("replay", 2, "refuted")]:
                rows.append({**common, "kind": "synth",
                             "flow": "syn_lhd_pyrope_tune_" + variant,
                             "qor": {"cells": 100 / scale, "liberty_sha256": "lib",
                                     "sdc_sha256": "sdc"},
                             "sta": {"opensta_ns": 10 / scale},
                             "time_ms": {"map": 1000 / scale}})
                rows.append({**common, "kind": "lec", "flow": "lec_pyrope_tune_" + variant,
                             "lec_result": {"verdict": verdict}})
            ledger = root / "data/ledger-host.jsonl"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
            output = report.render("host", ["new"])
            data = json.loads(output.with_suffix(".json").read_text())
            by = {row["variant"]: row for row in data["rows"]}
            self.assertEqual(by["profile"]["mapped_proof"], "tested")
            self.assertEqual(by["profile"]["mapped_proof_raw"], "timeout-time")
            self.assertTrue(by["profile"]["accepted_for_statistics"])
            self.assertEqual(set(by["profile"]["baseline_over_measured"].values()), {2})
            self.assertFalse(by["replay"]["accepted_for_statistics"])
            self.assertEqual(set(by["replay"]["baseline_over_measured"].values()), {None})
            self.assertIsNone(by["profile"]["profile_replay_qor_equal"])
            self.assertIn("tested", output.read_text())
            next(row for row in rows if row.get("flow") == "lec_pyrope_tune_profile")[
                "lec_result"]["verdict"] = "proven"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
            report.render("host", ["new"])
            proven = json.loads(output.with_suffix(".json").read_text())
            profile = next(row for row in proven["rows"] if row["variant"] == "profile")
            self.assertEqual(profile["baseline_over_measured"],
                             by["profile"]["baseline_over_measured"])


if __name__ == "__main__":
    unittest.main()
