"""Gate every independent equivalence result, including auxiliary checkers."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from lhdtrack.cache import Cache
from lhdtrack.run import Row, Runner
from lhdtrack.toolchain import Toolchain


class EquivalenceGates(TestCase):
    def test_timer_disagreement_does_not_gate_synthesis(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tc = Toolchain(root, "test", "", {}, {}, {}, {})
            runner = Runner(root, tc, Cache(root), "test", {"gates": {"sta_delta_pct_max": 10}})
            row = Row("dut", "default", "test", "asap7", "syn_lhd_pyrope", "synth",
                      True, False, "2026-09-15")
            row.qor = {"area_um2": 7}
            row.sta = {"delta_pct": 90, "opentimer_ns": 1, "opensta_ns": 10}
            runner.gate([row])
            self.assertEqual(row.status, "ok")
            self.assertTrue(row.passed)
            self.assertEqual(row.qor["area_um2"], 7)

    def test_checker_error_fails_every_result_slot(self):
        for field in ("lec_result", "lec_aux_result", "lec_verilog_result"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                setattr(row, field, {"verdict": "error", "solver": "lgyosys",
                                     "obligation": "verilog-vs-netlist"})
                runner.gate([row])
                self.assertEqual(row.status, "failed")
                self.assertFalse(row.passed)
                self.assertIn("lgyosys verilog-vs-netlist", row.note)

    def test_retained_netlist_refutation_fails_every_result_slot(self):
        for field in ("lec_result", "lec_aux_result", "lec_verilog_result"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                setattr(row, field, {"verdict": "refuted", "solver": "lgyosys",
                                     "obligation": "verilog-vs-netlist"})
                runner.gate([row])
                self.assertEqual(row.status, "failed")
                self.assertFalse(row.passed)
                self.assertIn("synthesized netlist refuted", row.note)

    def test_undecided_crosscheck_is_a_coverage_outcome(self):
        for verdict in ("timeout", "inconclusive", "unsupported"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                row.lec_aux_result = {"verdict": verdict, "solver": "lgyosys",
                                      "obligation": "verilog-vs-netlist"}
                runner.gate([row])
                self.assertEqual(row.status, "ok")
                self.assertTrue(row.passed)
