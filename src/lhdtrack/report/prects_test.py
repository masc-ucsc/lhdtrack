"""A physical report must not merge incompatible experiments or hide failures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .prects import INCREMENTAL_FLOW, native_breakdown, render


class PreCtsReport(unittest.TestCase):
    def row(self, root, host='one', source='src'):
        return {"run_id": "run", "host": host, "host_class": "cpu", "versions": {},
                "liberty_sha256": "lib", "placement_run": True, "test": "add", "config": "bw8",
                "flow": "syn_lhd_verilog_prects_aegraph", "status": "failed", "note": "failed",
                "placement_work": str(root), "placement_identity": str(root / 'identity.json'),
                "qor": {"placement_comparison": {"sdc_sha256": "sdc", "source_sha256": source,
                        "measurement": {"command_wall_s": 2, "cpu_s": 3}}}}

    def write(self, root, rows):
        (root / 'data').mkdir()
        (root / 'data/ledger-one.jsonl').write_text('\n'.join(map(json.dumps, rows)))

    def test_failures_and_unmeasured_slots_are_visible(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, [self.row(root)])
            output = root / 'target/result.html'
            render(root, output)
            self.assertIn('CPU s', output.read_text())
            self.assertIn('<td>3.00</td>', output.read_text())
            self.assertNotIn('IPC ', output.read_text())
            self.assertNotIn('baseline profile', output.read_text())
            self.assertNotIn('<a href=', output.read_text())
            self.assertIn('colspan="10">—</td>', output.read_text())
            self.assertIn('failed', output.read_text())
            self.assertNotIn('OpenSTA WNS 0.00', output.read_text())

    def test_cross_host_and_cross_input_rows_are_rejected(self):
        for mismatch in ('host', 'source', 'incremental_source'):
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as directory:
                root = Path(directory)
                a = self.row(root)
                b = self.row(root, host='two' if mismatch == 'host' else 'one',
                             source='src' if mismatch == 'host' else 'other')
                b['flow'] = INCREMENTAL_FLOW if mismatch == 'incremental_source' else 'syn_hightide'
                self.write(root, [a, b])
                with self.assertRaises(ValueError):
                    render(root, root / 'out.html')

    def test_newest_append_wins_over_lexically_larger_smoke_name(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = self.row(root), self.row(root)
            old['run_id'], new['run_id'] = 'z-smoke', 'a-final'
            self.write(root, [old, new])
            output = root / 'out.html'
            render(root, output)
            self.assertIn('a-final', output.read_text())
            self.assertNotIn('z-smoke', output.read_text())

    def test_nested_resynthesis_is_not_counted_as_initial_synthesis(self):
        values = native_breakdown({"phases": [
            {"name": "compile.cache", "ms": 20}, {"name": "pass.abc", "ms": 80},
            {"name": "pass.opentimer.incremental", "ms": 50},
            {"name": "pass.place.io_resynth.before", "ms": 30},
            {"name": "pass.place.io_resynth", "ms": 400},
            {"name": "pass.place", "ms": 1000},
            {"name": "pass.place.timing_driven", "ms": 500}],
            "incremental": {"abc": {"hits": 5, "misses": 1}},
            "io_resynth": {"fired": 2, "accepted": 1, "restarts": 1}})
        self.assertAlmostEqual(values["synthesis"], .1)
        self.assertAlmostEqual(values["resynth_stage"], .4)
        self.assertEqual(values["place"], 1)
        self.assertEqual(values["accepted"], 1)
        self.assertEqual(values["hits"], 5)
        self.assertIsNone(native_breakdown({})["resynth_pre"])
