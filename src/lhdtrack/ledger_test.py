"""Report coverage regressions; run with python -m unittest lhdtrack.ledger_test."""

import json
from pathlib import Path
import tempfile
import unittest

from lhdtrack.ledger import Ledger


class LatestRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root)

    def add(self, run, tests, flow="syn_lhd_verilog", tech="asap7", host="host1",
            status="ok"):
        self.ledger.append({"host": host, "run_id": run}, [
            {"test": test, "config": "default", "flow": flow, "tech": tech,
             "status": status}
            for test in tests
        ])

    def test_larger_equivalence_run_preserves_synthesis(self):
        self.add("01", ["a", "b"])
        self.add("02", ["a", "b", "c"], flow="lec_netlist")
        rows = self.ledger.latest_rows("host1")
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["test"] for r in rows if r["flow"] == "syn_lhd_verilog"},
                         {"a", "b"})

    def test_other_technology_preserves_asap7(self):
        self.add("01", ["a", "b"])
        self.add("02", ["a", "b", "c"], tech="sky130")
        self.assertEqual(len(self.ledger.latest_rows("host1")), 5)

    def test_focused_failure_overlays_without_hiding_other_tests(self):
        self.add("01", ["a", "b", "c"])
        self.add("02", ["b"], status="failed")
        rows = {r["test"]: r for r in self.ledger.latest_rows("host1")}
        self.assertEqual(set(rows), {"a", "b", "c"})
        self.assertEqual(rows["b"]["status"], "failed")
        self.assertEqual(rows["a"]["run_id"], "01")

    def test_new_equal_width_matrix_drops_removed_config(self):
        self.add("01", ["a", "removed"])
        self.add("02", ["a", "replacement"])
        self.assertEqual({r["test"] for r in self.ledger.latest_rows("host1")},
                         {"a", "replacement"})

    def test_repeated_rows_do_not_make_a_focused_run_full(self):
        self.add("01", ["a", "b", "c"])
        self.add("02", ["b"] * 4, status="failed")
        rows = self.ledger.latest_rows("host1")
        self.assertEqual({r["test"] for r in rows}, {"a", "b", "c"})
        self.assertEqual(next(r for r in rows if r["test"] == "b")["status"], "failed")

    def test_hosts_remain_independent_when_loading_all(self):
        self.add("01", ["a", "b"], host="host1")
        self.add("02", ["a", "b", "c"], host="host2")
        rows = self.ledger.latest_rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(self.ledger.latest_rows("host1")), 2)

    def test_same_run_last_append_wins_and_ledger_is_unchanged(self):
        self.add("01", ["a", "b"])
        self.add("01", ["a"], status="failed")
        path = self.ledger.path_for("host1")
        before = path.read_bytes()
        rows = self.ledger.latest_rows("host1")
        self.assertEqual(next(r for r in rows if r["test"] == "a")["status"], "failed")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len([json.loads(line) for line in before.splitlines()]), 3)


if __name__ == "__main__":
    unittest.main()
