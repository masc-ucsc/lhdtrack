"""Each checker must consume the measured emission for its own profile."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main

from lhdtrack.context import FlowError
from lec_netlist_verilog import run_mapper


class ModelsReached(Exception):
    pass


class RetainedProfile(TestCase):
    def test_disabled_checker_does_not_read_enabled_artifact(self):
        for mapper in ("abc", "usyn"):
            with self.subTest(mapper=mapper), TemporaryDirectory() as directory:
                root = Path(directory)
                stem = "syn_lhd_verilog" + ("_usyn" if mapper == "usyn" else "")
                for disabled in (False, True):
                    work = root / (stem + ("_no_satopt" if disabled else ""))
                    work.mkdir()
                    netlist = work / "netlist.v"
                    netlist.write_text("module top; endmodule\n")
                    digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
                    (work / "synth-artifacts.json").write_text(json.dumps({
                        "mapper": mapper, "netlist": str(netlist),
                        "sha256": digest if disabled else "corrupted-enabled-artifact",
                    }))

                def run(label, argv, **kwargs):
                    self.assertEqual(label, "models")
                    raise ModelsReached

                ctx = SimpleNamespace(work=root / "checker", require_sdc=lambda: None,
                                      tool=lambda name: name, liberty=["cells.lib"],
                                      lec_timeout_s=1, run=run)
                with self.assertRaises(ModelsReached):
                    run_mapper(ctx, mapper, satopt=False)
                with self.assertRaisesRegex(FlowError, "retained netlist differs"):
                    run_mapper(ctx, mapper, satopt=True)


if __name__ == "__main__":
    main()
