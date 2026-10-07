"""Keep Slop/LLVM measurements on identical deterministic driver invocations."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from lhdtrack.context import FlowError
from lib.simgate import parse_result, run_lhd_sim


class SimulationBackends(unittest.TestCase):
    def test_long_netlist_execution_does_not_enter_direct_compile_time(self):
        with TemporaryDirectory() as directory:
            work = Path(directory)
            driver = work / "SW/sim/drv.bin"
            driver.parent.mkdir(parents=True)
            driver.touch()
            build_log = work / "build.log"
            build_log.write_text("simulator compiled; no testbench executed\n")
            execution_log = work / "exec.log"
            execution_log.write_text("LHDTRACK-DONE cycles=100 checksum=7\n")
            ctx = SimpleNamespace(
                work=work, test=SimpleNamespace(sim_cycles=100, sim_marker="LHDTRACK-DONE"),
                sim_build_jobs=32, tool=Mock(return_value=Path("/staged/lhd")),
                run=Mock(return_value=SimpleNamespace(ms=125, log=build_log)),
                run_best=Mock(return_value=(SimpleNamespace(ms=25000, log=execution_log),
                                            [25000, 25100, 25200])),
                stage=SimpleNamespace(time_ms={"run": 125}),
            )
            result = run_lhd_sim(ctx, "lg:dut", work / "tb.prp", backend="llvm",
                                 direct_compile_timing=True)
            self.assertEqual(ctx.stage.time_ms, {"cc": 125})
            self.assertEqual(result["sim"]["exec_ms"], 25000)
            self.assertEqual(result["sim"]["checksum"], "7")
            self.assertEqual(result["sim"]["compile_timing"], "direct")
            self.assertNotIn("sim.compile_only=true", ctx.run.call_args_list[0].args[1])
            self.assertIn("sim.compile_only=true", ctx.run.call_args_list[1].args[1])

    def test_backend_selection_reaches_both_phases_and_exec_is_timed_separately(self):
        for backend in ("slop", "llvm"):
            with self.subTest(backend=backend), TemporaryDirectory() as directory:
                work = Path(directory)
                driver = work / "SW/sim/drv.bin"
                driver.parent.mkdir(parents=True)
                driver.touch()
                log = work / "exec.log"
                log.write_text("LHDTRACK-DONE cycles=100 checksum=7\n")
                measured = SimpleNamespace(ms=25, log=log)
                ctx = SimpleNamespace(
                    work=work, test=SimpleNamespace(sim_cycles=100, sim_marker="LHDTRACK-DONE"),
                    sim_build_jobs=8,
                    tool=Mock(return_value=Path("/staged/lhd")),
                    run=Mock(return_value=SimpleNamespace(ms=125, log=log)),
                    run_best=Mock(return_value=(measured, [25, 30, 26])),
                    stage=SimpleNamespace(time_ms={"run": 125}),
                )
                result = run_lhd_sim(ctx, "dut.prp", work / "tb.prp", backend=backend)
                for call in ctx.run.call_args_list:
                    argv = call.args[1]
                    self.assertIn(f"sim.tune.backend={backend}", argv)
                    self.assertIn("sim.tune.profile=off", argv)
                    self.assertIn("sim.jobs=8", argv)
                    self.assertIn("sim.init_zero=true", argv)
                    self.assertIn("sim.unknown_zero=true", argv)
                self.assertIn("--init-zero", ctx.run_best.call_args.args[1])
                self.assertEqual(result["sim"]["backend"], backend)
                self.assertEqual(result["sim"]["build_jobs"], 8)
                self.assertEqual(result["sim"]["exec_ms"], 25)
                self.assertEqual(result["sim"]["exec_samples_ms"], [25, 30, 26])
                self.assertEqual(ctx.stage.time_ms, {"cc": 100})

    def test_echoed_argv_and_other_markers_cannot_satisfy_the_checksum_contract(self):
        with TemporaryDirectory() as directory:
            log = Path(directory) / "exec.log"
            log.write_text("$ drv LHDTRACK-DONE cycles=100 checksum=7\n"
                           "OTHER-DONE cycles=100 checksum=7\n")
            with self.assertRaises(FlowError):
                parse_result(log, "LHDTRACK-DONE")
            with log.open("a") as output:
                output.write("LHDTRACK-DONE cycles=100 checksum=8\n")
            self.assertEqual(parse_result(log, "LHDTRACK-DONE").checksum, "8")


if __name__ == "__main__":
    unittest.main()
