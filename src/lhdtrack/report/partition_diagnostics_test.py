"""Partition acceptance uses the exact flat reference and preserves adverse results."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from lhdtrack.ledger import Ledger
from lhdtrack.report.partition_diagnostics import (
    cell_budget_status, flat_reference, write_partition_diagnostics,
)


class PartitionDiagnostics(unittest.TestCase):
    def rows(self):
        row = dict(host="host", host_class="Linux-x86_64", test="dino", config="default",
                   tech="asap7", kind="synth", versions={"lhd": "frozen"}, status="ok",
                   run_id="partition", partition_diagnostic=True, partition_variant="data-cones",
                   partition_settings={"pass.color.synth.max_gate": "30000"},
                   flow="syn_lhd_verilog_abc_sn_partition",
                   time_ms={"compile": 100, "color": 3, "map": 2000},
                   sta={"opensta_ns": 1200, "time_unit": "ps"},
                   qor={"cells": 101, "source_sha256": {"file": "source"},
                        "liberty_sha256": "lib", "sdc_sha256": "sdc",
                        "timing_breakdown": {"regions": [{"gates": 0}, {"gates": 3000},
                                                          {"gates": 700}]} })
        ref = deepcopy(row)
        ref.update(run_id="flat", flow="syn_lhd_verilog_abc_sn_flat")
        ref["qor"]["cells"] = 100
        return row, ref

    def test_reference_rejects_every_provenance_mismatch(self):
        for key in ("compiler", "source_sha256", "liberty_sha256", "sdc_sha256", "host_class"):
            with self.subTest(key=key):
                row, ref = self.rows()
                if key == "compiler":
                    ref["versions"]["lhd"] = "other"
                elif key == "host_class":
                    ref[key] = "other"
                else:
                    ref["qor"][key] = "other"
                self.assertIsNone(flat_reference(row, [ref]))

    def test_summary_requires_an_actual_matched_cell_neutral_candidate(self):
        row, ref = self.rows()
        self.assertIn("pending", cell_budget_status([row], []))
        self.assertIn("No partitioned candidate", cell_budget_status([row], [ref]))
        row["qor"]["cells"] = 100
        self.assertIn("meets", cell_budget_status([row], [ref]))
        row["flow"] = "syn_lhd_verilog_abc_sn_flat"
        row["partition_variant"] = "flat warm cache"
        self.assertIn("No partitioned candidate", cell_budget_status([row], [ref]))
        row["status"] = "failed"
        self.assertNotIn("A partitioned candidate meets", cell_budget_status([row], [ref]))

    def test_area_regression_is_rejected_even_with_more_partitions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row, ref = self.rows()
            Ledger(root).append({"host": "host"}, [row, ref])
            output = write_partition_diagnostics(root, "host", "dino")
            self.assertIn('+1.00% · reject', output.read_text())
            self.assertIn('2 / 1 / 1', output.read_text())
            row["qor"]["cells"] = 100
            Ledger(root).append({"host": "host"}, [row])
            output = write_partition_diagnostics(root, "host", "dino")
            self.assertIn('+0.00% · within cell limit', output.read_text())

    def test_cleanup_diagnostic_does_not_promote_measured_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row, ref = self.rows()
            row["qor"]["partition_cut_analysis"] = {
                "finding": "A cut crosses combinational logic.",
                "observations": [{"label": "Wiring cleanup diagnostic", "cells": 99,
                                  "flops": 10, "critical_path_ps": 1200}],
                "scope": "Diagnostic only; original measurement is retained.",
                "sharing_finding": "Repeated logic is only a partial explanation.",
                "cone_comparison": {
                    "finding": "Matched cone traversal keeps the original cell limit.",
                    "scope": "Group cones overlap; their deltas cannot be summed.",
                    "groups": [{"group": "register:pipeA_ex_mem.reg_0",
                                "matched_endpoints": 2, "same_source_bits": True,
                                "cell_delta": 7,
                                "baseline": {"cells": 10, "levels": 3},
                                "measured": {"cells": 17, "levels": 5}}],
                },
            }
            Ledger(root).append({"host": "host"}, [row, ref])
            output = write_partition_diagnostics(root, "host", "dino").read_text()
            self.assertIn("Boundary investigation", output)
            self.assertIn("Wiring cleanup diagnostic", output)
            self.assertIn("Post-mapping cone traversal", output)
            self.assertIn("Group cones overlap", output)
            self.assertIn('+1.00% · reject', output)
            self.assertIn("No partitioned candidate meets", output)


if __name__ == "__main__":
    unittest.main()
