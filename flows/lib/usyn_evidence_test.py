"""Definition cost/work totals must preserve stage evidence without physical claims."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from lib.usyn_evidence import native_evidence


class NativeEvidence(TestCase):
    def test_additive_work_cost_and_region_outcomes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "qor.json"
            path.write_text(json.dumps(dict(schema_version=5, kind="usyn",
                arithmetic=dict(adder="auto", adder_block=0, multiplier="csa"), regions=[
                dict(before=dict(total=20), after_residual=dict(total=15),
                     work=dict(selection=10, residual=30, total=40),
                     residual=dict(accepted=True, skipped=False, rewrite_wins=2)),
                dict(before=dict(total=8), after_residual=dict(total=8),
                     work=dict(selection=7, residual=0, total=7),
                     residual=dict(accepted=False, skipped=True, rewrite_wins=0))])))
            result = native_evidence(path)
            self.assertEqual(result["arithmetic"],
                             dict(adder="auto", adder_block=0, multiplier="csa"))
            self.assertEqual(result["cost_stages"]["before"]["total"], 28)
            self.assertEqual(result["cost_stages"]["after_residual"]["total"], 23)
            self.assertEqual(result["work"], dict(selection=17, residual=30, total=47))
            self.assertEqual(result["residual"], dict(rewrite_wins=2))
            self.assertEqual(result["residual_accepted_regions"], 1)
            self.assertEqual(result["residual_skipped_regions"], 1)
            self.assertIn("not mapped cell area", result["cost_scope"])
            self.assertEqual(len(result["report_sha256"]), 64)
