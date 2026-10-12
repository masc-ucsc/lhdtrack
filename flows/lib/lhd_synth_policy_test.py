"""An explicit delay experiment must be executable and recorded faithfully."""
import os
from unittest import TestCase
from unittest.mock import patch

from lib.lhd_synth_policy import abc_sn_settings, mapping_settings, recorded_settings


class MappingSettings(TestCase):
    def test_fixed_sn_profile_preserves_defaults_and_records_explicit_choices(self):
        with patch.dict(os.environ, {}, clear=True):
            defaults = mapping_settings("asap7", "abc", "400")
            fixed = abc_sn_settings(defaults)
            self.assertEqual(defaults, ["--set", "pass.abc.delay=400"])
        policy = recorded_settings(["lhd synth " + " ".join(fixed)])
        self.assertEqual(policy["pass.abc.adder"], "brent")
        self.assertEqual(policy["pass.abc.multiplier"], "sn")
        self.assertEqual(policy["pass.abc.barrel"], "log")
        self.assertEqual(policy["pass.abc.region_opts"], "{}")
        self.assertEqual(policy["pass.abc.delay"], "400")

    def test_fixed_sn_profile_replaces_auto_and_region_overrides_without_duplicate_keys(self):
        settings = ["--set", "pass.abc.adder=auto", "--set", "pass.abc.adder=rca",
                    "--set", "pass.abc.region_opts={region:adder=cla}",
                    "--set", "pass.abc.multiplier=auto", "--set", "pass.abc.barrel=auto",
                    "--set", "pass.abc.area_relax=1.1"]
        result = abc_sn_settings(settings)
        self.assertEqual(result[1::2], ["pass.abc.area_relax=1.1", "pass.abc.adder=brent",
                         "pass.abc.multiplier=sn", "pass.abc.barrel=log", "pass.abc.region_opts={}"])

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
