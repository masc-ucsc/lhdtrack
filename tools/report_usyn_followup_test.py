"""Critical-path diagnostics must retain evidence and identify control logic correctly."""
from pathlib import Path
import tempfile
from unittest import TestCase

from report_usyn_followup import diagnose, path_summary


class FollowupDiagnostics(TestCase):
    def test_full_path_retains_loaded_cell_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "path.log"
            path.write_text("Startpoint: sel[0] (input port)\nEndpoint: ready (output port)\n"
                            " 1 0.3 0.0 0.0 0.0 ^ sel[0] (in)\n"
                            " 11 5.25 71.99 43.70 43.70 ^ g0/Y (HB1xp67_ASAP7_75t_R)\n"
                            " 4 2.05 55.59 46.58 90.28 v g3/Y (INVxp33_ASAP7_75t_R)\n")
            result = path_summary(path)
            self.assertEqual(result["cells"], 2)
            self.assertEqual(result["largest_delays"][0]["fanout"], 4)
            self.assertEqual(result["largest_delays"][0]["delay"], 46.58)
            self.assertEqual(result["gates"][0]["cap"], 5.25)
            path.write_text("Error: cannot open timing script\n")
            with self.assertRaisesRegex(ValueError, "complete critical path"):
                path_summary(path)

    def test_multihot_control_is_not_classified_as_multiplication(self):
        variant = dict(synthesis=dict(qor=dict(usyn_evidence=dict(
                       totals=dict(eligible_endpoints=0)))))
        control = dict(test="br_flow_fork_select_multihot", variants=dict(candidate=variant))
        result = diagnose(control, {})
        self.assertEqual(result["category"], "control/predicate logic")
        self.assertIn("no eligible endpoints", result["explanation"])
        arithmetic = dict(control, test="carry_save_compare")
        self.assertEqual(diagnose(arithmetic, {})["category"], "partial-product arithmetic")
