"""An explicit delay experiment must be executable and recorded faithfully."""
import os
from unittest import TestCase
from unittest.mock import patch

from lib.lhd_synth_policy import mapping_settings, recorded_settings


class MappingSettings(TestCase):
    def test_default_and_explicit_target_have_one_delay_setting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mapping_settings("asap7", "usyn", "400"),
                             ["--set", "pass.usyn.delay=400"])
        with patch.dict(os.environ, {"LHDTRACK_LHD_SET":
                                     "pass.usyn.adder=auto pass.usyn.delay=50"}, clear=True):
            args = mapping_settings("asap7", "usyn", "400")
            self.assertEqual(args, ["--set", "pass.usyn.delay=50",
                                    "--set", "pass.usyn.adder=auto"])
            self.assertEqual(recorded_settings(["synth: lhd synth " + " ".join(args)])
                             ["pass.usyn.delay"], "50")

    def test_conflicting_user_targets_are_rejected(self):
        with patch.dict(os.environ, {"LHDTRACK_LHD_SET":
                                     "pass.usyn.delay=50 pass.usyn.delay=100"}, clear=True):
            with self.assertRaisesRegex(ValueError, "conflicting"):
                mapping_settings("asap7", "usyn", "400")

    def test_other_mapper_settings_do_not_override_this_target(self):
        with patch.dict(os.environ, {"LHDTRACK_LHD_SET": "pass.abc.delay=50"}, clear=True):
            self.assertEqual(mapping_settings("asap7", "usyn", "400"),
                             ["--set", "pass.usyn.delay=400", "--set", "pass.abc.delay=50"])
