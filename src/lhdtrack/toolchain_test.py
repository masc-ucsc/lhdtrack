"""A running regression keeps the registry it loaded, even when staging changes."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .toolchain import TOOLCHAIN_REL, Toolchain


class ToolchainSnapshotTest(unittest.TestCase):
    def test_snapshot_preserves_loaded_registry_without_changing_active_registry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / TOOLCHAIN_REL
            registry.parent.mkdir(parents=True)
            original = {"host_class": "cpu", "bin": {"lhd": "/registered/old/lhd"},
                        "versions": {"lhd": "old"},
                        "env": {"lhd": {"RUNFILES_DIR": "/registered/old/runfiles"}},
                        "tech": {"asap7": {"liberty": ["/registered/liberty.lib"],
                                           "sha256": "lib", "time_unit": "ps"}}}
            registry.write_text(json.dumps(original))
            loaded = Toolchain.load(root)
            registry.write_text(json.dumps({**original, "versions": {"lhd": "new"}}))
            frozen_root = root / "run-snapshot"
            loaded.write_snapshot(frozen_root)
            frozen = Toolchain.load(frozen_root)
            self.assertEqual(frozen.version("lhd"), "old")
            self.assertEqual(frozen.bin("lhd"), loaded.bin("lhd"))
            self.assertEqual(frozen.env_for(frozen.bin("lhd")),
                             {"RUNFILES_DIR": "/registered/old/runfiles"})
            self.assertEqual(frozen.tech("asap7"), loaded.tech("asap7"))
            self.assertEqual(Toolchain.load(root).version("lhd"), "new")
