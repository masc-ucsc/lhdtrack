"""Named synthesis snapshots must not inherit later focused observations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lhdtrack.report.html import (
    _eligible, _gate_snapshot_lec, _lec_section, _netlist_lec_section, _problem_note,
    _synth_display_values, _synth_table,
    _llvm_gain, _llvm_chart_data, _sim_table, write_all, write_report,
)


class ReportSnapshot(unittest.TestCase):
    def test_native_logic_metrics_appear_in_full_table_and_zero_totals_take_precedence(self):
        key = ("memory", "ram", "one", "asap7")
        row = dict(status="ok", qor=dict(native_state=True, lhd_cells=42,
                   lhd_area_um2=12.5, abc_max_delay_ns=93.75), sta={},
                   time_ms=dict(total=1000), peak_rss_kb=dict(max=2048))
        rendered = _synth_table("Synthesis", [key], {key: {"syn_lhd_verilog_usyn": row}},
                                "syn_yosys_abc", {"idiomatic"}, "ps")
        for value in ("12.50", "42", "93.75", ">logic</span>", ">region</span>"):
            self.assertIn(value, rendered)
        self.assertNotIn(">native</span>", rendered)
        row["qor"].update(area_um2=0, cells=0)
        row["sta"]["opensta_ns"] = 0
        values, scopes = _synth_display_values(row)
        self.assertEqual(values[:3], [0, 0, 0])
        self.assertEqual(scopes[:3], ["", "", ""])

    def test_evaluation_regenerates_linked_full_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data/verilog-eval-snapshot-host.json").write_text(json.dumps({
                "auxiliary_report": "report-snapshot-host-full.html",
            }))
            main = root / "target/report-snapshot-host.html"
            with patch("lhdtrack.report.verilog_eval.write_evaluation", return_value=main):
                outputs = write_all(root, only="snapshot-host")
            full = root / "target/results-syn-snapshot-host-full.html"
            self.assertIn(full, outputs)
            self.assertIn("No runs recorded", full.read_text())

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


class SimulationWorkload(unittest.TestCase):
    def test_llvm_speed_pairs_each_language_with_its_own_slop_run(self):
        key = ("comb", "dut", "one", None)
        def row(backend, ms):
            return {"status": "ok", "host": "same", "host_class": "same", "run_id": "same",
                    "versions": {"lhd": "same"},
                    "sim": {"backend": backend, "exec_ms": ms, "cycles": 100, "checksum": "7"}}
        verilog, pyrope = row("slop", 100), row("slop", 20)
        llvm_v, llvm_p = row("llvm", 25), row("llvm", 40)
        index = {key: {"sim_lhd_verilog": verilog, "sim_lhd_pyrope": pyrope,
                       "sim_lhd_verilog_llvm": llvm_v, "sim_lhd_pyrope_llvm": llvm_p}}
        chart = _llvm_chart_data([key], index)
        self.assertEqual(chart["groups"][0]["values"]["exec"], {"verilog": 4, "pyrope": .5})
        table = _sim_table(None, [key], index, "sim_verilator", {"idiomatic"})
        self.assertIn("4.00×", table)
        self.assertIn("0.50×", table)
        self.assertIn("Slop 0.1000s / LLVM 0.0250s", table)
        self.assertIn("LLVM speed / Slop", table)
        for field, value in (("host", "other"), ("host_class", "other"),
                             ("run_id", "old"), ("status", "failed"),
                             ("versions", {"lhd": "old"})):
            with self.subTest(field=field):
                self.assertIsNone(_llvm_gain({**llvm_v, field: value}, verilog))
        for field, value in (("cycles", 99), ("checksum", "8"), ("backend", "slop"),
                             ("measurement_jobs", 32),
                             ("exec_ms", 0), ("exec_ms", float("inf"))):
            with self.subTest(field=field, value=value):
                changed = {**llvm_v, "sim": {**llvm_v["sim"], field: value}}
                self.assertIsNone(_llvm_gain(changed, verilog))

    def test_retuned_cycles_do_not_compare_to_an_old_short_run(self):
        from lhdtrack.report.html import _chart_data, _eligible, _sim_table
        key = ("comb", "add", "default", None)
        base = {"status": "ok", "sim": {"cycles": 50_000_000, "exec_ms": 1000}}
        measured = {"status": "ok", "sim": {"cycles": 1_000_000, "exec_ms": 10}}
        index = {key: {"sim_verilator": base, "sim_lhd_verilog": measured}}
        metrics = [("exec", "Simulation speed", "", lambda r: r["sim"]["exec_ms"])]
        self.assertIsNone(_chart_data([key], index, "sim_verilator",
                                      ["sim_lhd_verilog"], metrics))
        self.assertFalse(_eligible(measured, "sim_lhd_verilog", base,
                                   {"idiomatic"}, "sim_lhd_pyrope"))
        self.assertIn("50,000,000", _sim_table(None, [key], index,
                                               "sim_verilator", {"idiomatic"}))
        measured["sim"]["cycles"] = 50_000_000
        chart = _chart_data([key], index, "sim_verilator", ["sim_lhd_verilog"], metrics)
        self.assertEqual(chart["groups"][0]["values"]["exec"]["sim_lhd_verilog"], 100)


class SplitResults(unittest.TestCase):
    def test_normal_report_renders_all_domains_without_rewriting_the_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            common = {"host": "test-host", "host_class": "cpu", "suite": "comb", "test": "dut",
                      "config": "one", "run_id": "fresh", "date": "2026-10-06", "status": "ok",
                      "versions": {}, "tech": None}
            rows = [{**common, "kind": "synth", "flow": "syn_yosys_abc", "tech": "asap7",
                     "qor": {"area_um2": 1}},
                    {**common, "kind": "sim", "flow": "sim_verilator",
                     "sim": {"cycles": 100, "exec_ms": 2}},
                    {**common, "kind": "lec", "flow": "lec_netlist", "tech": "asap7",
                     "lec_result": {"verdict": "proven", "ms": 1}}]
            ledger = root / "data/ledger-test-host.jsonl"
            original = "".join(json.dumps(row) + "\n" for row in rows)
            ledger.write_text(original)
            outputs = write_all(root, only="test-host")
            for suffix in ("syn", "sim", "lec"):
                path = root / f"target/results-{suffix}-test-host.html"
                self.assertIn(path, outputs)
                self.assertIn(f'aria-current="page" href="results-{suffix}', path.read_text())
            synth = (root / "target/results-syn-test-host.html").read_text()
            sim = (root / "target/results-sim-test-host.html").read_text()
            lec = (root / "target/results-lec-test-host.html").read_text()
            self.assertIn("Synthesis · asap7", synth)
            self.assertNotIn("<h2>Simulation", synth)
            self.assertIn("<h2>Simulation", sim)
            self.assertNotIn("STA accuracy", sim)
            # A netlist-only LEC matrix must survive the split without a language LEC row.
            self.assertIn("Equivalence — sources vs synthesized netlist", lec)
            self.assertNotIn("<h2>Simulation", lec)
            self.assertNotIn("STA accuracy", lec)
            self.assertFalse((root / "target/report-test-host.html").exists())
            self.assertEqual(ledger.read_text(), original)
            index = (root / "target/index.html").read_text()
            self.assertIn('href="results-sim-test-host.html"', index)


if __name__ == "__main__":
    unittest.main()
