"""A physical report must not merge incompatible experiments or hide failures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .prects import render


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
            self.assertIn('colspan="11">—</td>', output.read_text())
            self.assertIn('failed', output.read_text())
            self.assertNotIn('OpenSTA WNS 0.00', output.read_text())

    def test_cross_host_and_cross_input_rows_are_rejected(self):
        for mismatch in ('host', 'source'):
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as directory:
                root = Path(directory)
                a = self.row(root)
                b = self.row(root, host='two' if mismatch == 'host' else 'one',
                             source='other' if mismatch == 'source' else 'src')
                b['flow'] = 'syn_hightide'
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
