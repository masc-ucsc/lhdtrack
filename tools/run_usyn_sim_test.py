"""Calibrated netlist timing keeps independent checksum and full-length evidence."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from lhdtrack.cache import Cache
from lhdtrack.corpus import Config
from lhdtrack.run import Row, Runner, expected_checksums
from lhdtrack.toolchain import Toolchain
from tools.run_usyn_sim import timing_cycles


class TimingProfile(TestCase):
    def test_short_profile_and_no_extension(self):
        self.assertEqual(timing_cycles(100000, {"exec_ms": 100000}, 2), 2000)
        self.assertEqual(timing_cycles(100000, {"exec_ms": 1000}, 2), 100000)
        self.assertEqual(timing_cycles(10, {"exec_ms": 1000000}, 2), 1)

    def test_full_profile_or_missing_history(self):
        for history in ({}, {"exec_ms": None}, {"exec_ms": 0}):
            self.assertEqual(timing_cycles(100000, history, 2), 100000)
        self.assertEqual(timing_cycles(100000, {"exec_ms": 100000}, 0), 100000)

    def test_agreement_cannot_override_original_rtl_reference(self):
        original = Config("default", sim_checksum=(100000, "full-checksum"))
        shortened = replace(original, sim_checksum=(2000, "original-rtl-at-2000"))
        reference = expected_checksums([
            SimpleNamespace(test=SimpleNamespace(name="dut"), config=shortened)])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Runner(root, Toolchain(root, "test", "", {}, {}, {}, {}),
                            Cache(root), "timing", {})
            rows = [Row("dut", "default", "test", "asap7", flow, "sim", True,
                        False, "2026-10-07", sim={"cycles": 2000, "checksum": "wrong"})
                    for flow in ("verilator", "slop", "llvm")]
            runner.gate(rows, expected=reference)
            self.assertTrue(all(r.status == "failed" for r in rows))
        self.assertEqual(original.sim_checksum, (100000, "full-checksum"))
