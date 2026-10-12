"""Regression for ABC &undo retaining the names required by SN cloud insertion."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.toolchain import Toolchain


class UndoInterfaceTest(unittest.TestCase):
    def check_roundtrip(self, commands: str) -> None:
        tc = Toolchain.load(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            original = (
                ".model named_boundary\n.inputs operand_a operand_b select\n"
                ".outputs result flag\n.names operand_a operand_b result\n11 1\n"
                ".names select flag\n1 1\n.end\n")
            (work / "input.blif").write_text(original)
            binary = tc.bin("abc_sn")
            result = subprocess.run(
                [str(binary), "-c", "read_blif input.blif; &get -n; " + commands
                 + "; &put; write_blif output.blif"],
                cwd=work, env=tc.env_for(binary), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = work / "output.blif"
            self.assertTrue(output.is_file(), result.stdout + result.stderr)
            text = output.read_text().replace("\\\n", " ")
            for kind in ("inputs", "outputs"):
                expected = next(line.split()[1:] for line in original.splitlines()
                                if line.startswith("." + kind + " "))
                actual = next(line.split()[1:] for line in text.splitlines()
                              if line.startswith("." + kind + " "))
                self.assertEqual(actual, expected)

    def test_mapping_backup(self) -> None:
        self.check_roundtrip("&st; &undo")

    def test_repeated_backup(self) -> None:
        self.check_roundtrip("&st; &st; &undo")


if __name__ == "__main__":
    unittest.main()
