"""Color diagnostics must compare exact provenance and exclude failed QoR."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from lhdtrack.ledger import Ledger
from lhdtrack.report.external_abc_sn import write_external_diagnostics, write_external_color_run


class ColorReport(unittest.TestCase):
    def rows(self):
        baseline = {
            "host": "host", "host_class": "Linux-x86_64", "versions": {"lhd": "frozen"},
            "run_id": "baseline", "test": "dino", "config": "default", "status": "ok",
            "flow": "syn_lhd_verilog_abc_sn", "large_design_diagnostic": True,
            "time_ms": {"compile": 100, "color": 5, "map": 2000, "total": 3000},
            "sta": {"opensta_ns": 1934, "sdc_period_ns": 1000, "time_unit": "ps"},
            "qor": {"cells": 57926, "flops": 3577, "liberty_sha256": "lib",
                    "source_sha256": {"original.sv": "source"}, "sdc_sha256": "sdc",
                    "timing_breakdown": {"phases": [{"name": "pass.abc", "ms": 2000}],
                                         "regions": [{"module": "core"}],
                                         "synthesis_invocation": {"wall_ms": 2500}}},
        }
        flat = deepcopy(baseline)
        flat.update(run_id="flat", source_run="baseline", external_color_diagnostic=True,
                    flow="syn_lhd_verilog_abc_sn_flat")
        flat.pop("large_design_diagnostic")
        flat["qor"].update(cells=41000, synth_policy={"pass.abc.large_ge": "0"})
        return baseline, flat

    def test_report_and_rebuild_keep_baseline_and_variant_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = self.rows()
            Ledger(root).append({"host": "host"}, list(rows))
            output = write_external_color_run(root, "host", "flat", "dino")
            html = output.read_text()
            for text in ("1934.00", "57926", "41000", "3.395", "full ABC recipe"):
                self.assertIn(text, html)
            exported = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(exported["baseline_run"], "baseline")
            self.assertIn(output, write_external_diagnostics(root, "host"))

    def test_mismatch_rejected_for_library_source_constraints_and_compiler(self):
        for field in ("liberty_sha256", "source_sha256", "sdc_sha256", "compiler"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                baseline, flat = self.rows()
                if field == "compiler":
                    flat["versions"]["lhd"] = "other"
                else:
                    flat["qor"][field] = "other"
                Ledger(root).append({"host": "host"}, [baseline, flat])
                with self.assertRaises(ValueError):
                    write_external_color_run(root, "host", "flat", "dino")

    def test_failed_measurement_keeps_evidence_without_quality_in_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, flat = self.rows()
            flat["status"] = "failed"
            flat["qor"]["program_check"] = {"reference_rc": 0, "netlist_rc": 1}
            Ledger(root).append({"host": "host"}, [baseline, flat])
            output = write_external_color_run(root, "host", "flat", "dino")
            table = output.read_text().split('</table>')[0]
            self.assertNotIn('41000', table)
            self.assertIn('failed', table)
            self.assertEqual(json.loads(output.with_suffix('.json').read_text())
                             ['flat'][0]['qor']['cells'], 41000)


if __name__ == "__main__":
    unittest.main()
