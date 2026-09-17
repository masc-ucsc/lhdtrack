"""Named synthesis snapshots must not inherit later focused observations."""
import json
from pathlib import Path
import tempfile
import unittest

from lhdtrack.report.html import (
    _eligible, _gate_snapshot_lec, _lec_section, _netlist_lec_section, _problem_note, write_report,
)


class ReportSnapshot(unittest.TestCase):
    def test_lec_speedup_requires_matching_definitive_answers(self):
        key = ("rtl", "delay", "default", None)
        for left, right, comparable in (
            ({"verdict": "proven"}, {"verdict": "proven"}, True),
            ({"verdict": "refuted"}, {"verdict": "refuted"}, True),
            ({"verdict": "proven"},
             {"verdict": "proven", "bounded": True, "bound": 6}, False),
            ({"verdict": "timeout"}, {"verdict": "timeout"}, False),
            ({"verdict": "error"}, {"verdict": "error"}, False),
            ({"verdict": "proven"}, {"verdict": "refuted"}, False),
        ):
            with self.subTest(left=left, right=right):
                index = {key: {
                    "lec_lgyosys": {"lec_result": {**left, "ms": 4000}},
                    "lec_lhd": {"lec_result": {**right, "ms": 1000}},
                }}
                rendered = _lec_section([key], index)
                self.assertEqual("4.00×" in rendered, comparable)
                self.assertIn("4.00</td>", rendered)
                self.assertIn("1.00</td>", rendered)
                if right.get("bounded"):
                    self.assertIn("bounded(6)", rendered)
                netlist = _netlist_lec_section([{
                    "flow": "lec_netlist", "test": "delay", "config": "default", "status": "ok",
                    "tech": "asap7", "lec_result": {"verdict": "proven", "ms": 1},
                    "lec_aux_result": {**left, "ms": 4000},
                    "lec_verilog_result": {**right, "ms": 1000},
                }])
                self.assertEqual("4.00×" in netlist, comparable)

    def test_snapshot_keeps_requested_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            row = {
                "host": "snapshot-host", "suite": "comb", "test": "tiny",
                "config": "default", "tech": "asap7", "flow": "syn_lhd_verilog",
                "kind": "synth", "run_id": "20260909T010000", "date": "2026-09-09",
                "status": "ok", "passed": True, "comparable": False,
                "pyrope_status": "auto", "lec": "proven", "versions": {},
                "qor": {"cells": 41, "area_um2": 1, "logic_depth": 1},
                "sta": {"opensta_ns": 1, "time_unit": "ps"},
                "time_ms": {"total": 1}, "peak_rss_kb": {"max": 1},
            }
            newer = {**row, "run_id": "20260909T020000",
                     "qor": {**row["qor"], "cells": 99991}}
            ledger = root / "data/ledger-snapshot-host.jsonl"
            baseline = {**row, "flow": "syn_yosys_abc", "run_id": "20260908T000000",
                        "qor": {**row["qor"], "cells": 77777}}
            original = "\n".join(json.dumps(r) for r in (row, newer, baseline)) + "\n"
            ledger.write_text(original)
            latest = write_report(root, out=root / "latest.html", host="snapshot-host").read_text()
            snapshot = write_report(root, out=root / "snapshot.html", host="snapshot-host",
                                    synthesis_run=row["run_id"]).read_text()
            self.assertTrue("99991" in latest or "99,991" in latest)
            self.assertNotIn("99991", snapshot)
            self.assertNotIn("99,991", snapshot)
            self.assertTrue("77777" in snapshot or "77,777" in snapshot)
            self.assertIn('data-synthesis-run="20260909T010000"', snapshot)
            self.assertEqual(ledger.read_text(), original)
            with self.assertRaisesRegex(ValueError, "no synthesis rows"):
                write_report(root, host="snapshot-host", synthesis_run="missing")

    def test_fresh_unknown_does_not_inherit_manifest_proof(self):
        base = {"test": "axi", "config": "default", "run_id": "fresh", "status": "ok"}
        synth = {**base, "kind": "synth", "tech": "asap7", "flow": "syn_lhd_pyrope",
                 "lec": "proven", "comparable": True, "pyrope_status": "idiomatic"}
        verilog = {**synth, "flow": "syn_lhd_verilog"}
        proven = {"verdict": "proven", "bounded": False}
        netlist = {**base, "kind": "lec", "tech": "asap7", "flow": "lec_netlist",
                   "lec_result": proven, "lec_verilog_result": proven}
        language = {**base, "kind": "lec", "tech": None, "flow": "lec_lhd",
                    "lec_result": {"verdict": "inconclusive"}}
        old = {**language, "run_id": "old", "lec_result": proven}
        rows = _gate_snapshot_lec([synth, verilog, netlist, language, old], "fresh")
        self.assertFalse(rows[0]["comparable"])
        self.assertEqual(rows[0]["lec"], "unverified")
        self.assertFalse(_eligible(rows[0], synth["flow"], base, {"idiomatic"}, synth["flow"]))
        self.assertTrue(rows[1]["measured_lec_verified"])
        self.assertTrue(synth["comparable"])
        language["lec_result"] = proven
        rows = _gate_snapshot_lec([synth, netlist, language], "fresh")
        self.assertTrue(rows[0]["measured_lec_verified"])
        netlist["lec_result"] = {"verdict": "proven", "bounded": True, "bound": 6}
        self.assertFalse(_gate_snapshot_lec([synth, netlist, language], "fresh")[0]["measured_lec_verified"])

    def test_legacy_note_uses_recorded_unit_without_changing_ledger_row(self):
        note = "OpenTimer 200ns vs OpenSTA 180ns = 11.1% apart (limit 10.0%)"
        row = {"note": note, "sta": {"time_unit": "ps"}}
        self.assertEqual(_problem_note(row),
                         "OpenTimer 200ps vs OpenSTA 180ps = 11.1% apart (limit 10.0%)")
        self.assertEqual(row["note"], note)
        self.assertEqual(_problem_note({"note": note, "sta": {"time_unit": "ns"}}), note)
        self.assertEqual(_problem_note({"note": None}), "")


if __name__ == "__main__":
    unittest.main()
