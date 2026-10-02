"""The recorded simulation checksum: gate, write-back, and cache-key exclusion."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from lhdtrack.cache import Cache
from lhdtrack.cli import record_sim_checksum
from lhdtrack.corpus import CorpusError, load_test
from lhdtrack.keys import file_digest, manifest_digest
from lhdtrack.run import Row, Runner
from lhdtrack.toolchain import Toolchain

MANIFEST = """\
[design]
name  = "dut"
top   = "dut"
kind  = "combinational"
suite = "comb"

# One elaborated parameter set per entry.
[[config]]
id     = "bw8"
params = {}

[sim]
flows  = ["sim_verilator"]
cycles = 100
"""


def _sim_row(flow: str, checksum: str, cycles: int = 100) -> Row:
    row = Row("dut", "bw8", "comb", None, flow, "sim", True, False, "2026-09-27")
    row.sim = {"cycles": cycles, "checksum": checksum}
    return row


class RecordedChecksum(TestCase):
    def test_behavioral_simulation_changes_key_without_changing_formal_default(self):
        with TemporaryDirectory() as directory:
            test = Path(directory) / "dut"
            test.mkdir()
            manifest = test / "design.toml"
            manifest.write_text(MANIFEST)
            before = manifest_digest(manifest)
            manifest.write_text(MANIFEST + 'verilog_profile = "behavioral"\n')
            self.assertNotEqual(manifest_digest(manifest), before)
            fixture = load_test(test)
            self.assertNotIn("-DSYNTHESIS", fixture.sim_verilog_args())
            self.assertIn("-DSYNTHESIS", fixture.verilog_args("lec"))

    def test_invalid_profile_and_empty_capability_reason_are_rejected(self):
        with TemporaryDirectory() as directory:
            test = Path(directory) / "dut"
            test.mkdir()
            manifest = test / "design.toml"
            for extra in ('verilog_profile = "behavoural"\n', 'skip_reason = ""\n'):
                with self.subTest(extra=extra):
                    manifest.write_text(MANIFEST + extra)
                    with self.assertRaises(CorpusError):
                        load_test(test)

    def _runner(self, root: Path) -> Runner:
        tc = Toolchain(root, "test", "", {}, {}, {}, {})
        return Runner(root, tc, Cache(root), "test", {})

    def test_mismatch_against_the_reference_fails_a_lone_simulator(self):
        with TemporaryDirectory() as directory:
            row = _sim_row("sim_lhd_pyrope", "7")
            self._runner(Path(directory)).gate([row], {("dut", "bw8"): (100, "9")})
            self.assertEqual(row.status, "failed")
            self.assertIn("!= recorded 9", row.note)

    def test_matching_reference_passes(self):
        with TemporaryDirectory() as directory:
            row = _sim_row("sim_lhd_pyrope", "9")
            self._runner(Path(directory)).gate([row], {("dut", "bw8"): (100, "9")})
            self.assertEqual(row.status, "ok")

    def test_reference_at_another_length_does_not_bind(self):
        with TemporaryDirectory() as directory:
            row = _sim_row("sim_lhd_pyrope", "7", cycles=200)
            self._runner(Path(directory)).gate([row], {("dut", "bw8"): (100, "9")})
            self.assertEqual(row.status, "ok")

    def test_record_round_trips_and_leaves_the_cache_key_alone(self):
        with TemporaryDirectory() as directory:
            test = Path(directory) / "dut"
            test.mkdir()
            manifest = test / "design.toml"
            manifest.write_text(MANIFEST)
            before = file_digest(manifest)

            record_sim_checksum(manifest, "bw8", 100, "123")
            self.assertEqual(load_test(test).configs[0].sim_checksum, (100, "123"))
            self.assertEqual(manifest_digest(manifest), before)

            record_sim_checksum(manifest, "bw8", 100, "456")  # replaced, not appended
            self.assertEqual(manifest.read_text().count("sim_checksum"), 1)
            self.assertEqual(load_test(test).configs[0].sim_checksum, (100, "456"))
            self.assertIn("# One elaborated parameter set", manifest.read_text())
