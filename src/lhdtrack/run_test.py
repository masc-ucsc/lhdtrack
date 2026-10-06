"""Gate every independent equivalence result, including auxiliary checkers."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from lhdtrack.cache import Cache
from lhdtrack.run import Row, Runner
from lhdtrack.toolchain import Toolchain


class EquivalenceGates(TestCase):
    def test_timer_disagreement_does_not_gate_synthesis(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tc = Toolchain(root, "test", "", {}, {}, {}, {})
            runner = Runner(root, tc, Cache(root), "test", {"gates": {"sta_delta_pct_max": 10}})
            row = Row("dut", "default", "test", "asap7", "syn_lhd_pyrope", "synth",
                      True, False, "2026-09-15")
            row.qor = {"area_um2": 7}
            row.sta = {"delta_pct": 90, "opentimer_ns": 1, "opensta_ns": 10}
            runner.gate([row])
            self.assertEqual(row.status, "ok")
            self.assertTrue(row.passed)
            self.assertEqual(row.qor["area_um2"], 7)

    def test_checker_error_fails_every_result_slot(self):
        for field in ("lec_result", "lec_aux_result", "lec_verilog_result"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                setattr(row, field, {"verdict": "error", "solver": "lgyosys",
                                     "obligation": "verilog-vs-netlist"})
                runner.gate([row])
                self.assertEqual(row.status, "failed")
                self.assertFalse(row.passed)
                self.assertIn("lgyosys verilog-vs-netlist", row.note)

    def test_retained_netlist_refutation_fails_every_result_slot(self):
        for field in ("lec_result", "lec_aux_result", "lec_verilog_result"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                setattr(row, field, {"verdict": "refuted", "solver": "lgyosys",
                                     "obligation": "verilog-vs-netlist"})
                runner.gate([row])
                self.assertEqual(row.status, "failed")
                self.assertFalse(row.passed)
                self.assertIn("synthesized netlist refuted", row.note)

    def test_undecided_crosscheck_is_a_coverage_outcome(self):
        for verdict in ("timeout", "inconclusive", "unsupported"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as directory:
                root = Path(directory)
                tc = Toolchain(root, "test", "", {}, {}, {}, {})
                runner = Runner(root, tc, Cache(root), "test", {})
                row = Row("dut", "default", "test", "test", "lec_netlist", "lec",
                          True, False, "2026-09-15")
                row.lec_result = {"verdict": "proven", "obligation": "pyrope-vs-netlist"}
                row.lec_aux_result = {"verdict": verdict, "solver": "lgyosys",
                                      "obligation": "verilog-vs-netlist"}
                runner.gate([row])
                self.assertEqual(row.status, "ok")
                self.assertTrue(row.passed)

    def test_non_gating_formal_retains_refutations_as_information(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tc = Toolchain(root, "test", "", {}, {}, {}, {})
            runner = Runner(root, tc, Cache(root), "test", {})
            cvc5 = Row("dut", "default", "cdc", None, "lec_lhd", "lec",
                       True, False, "2026-09-18", lec_gate=False)
            cvc5.lec_result = {
                "verdict": "refuted", "solver": "cvc5",
                "obligation": "pyrope-vs-verilog",
            }
            lgyosys = Row("dut", "default", "cdc", None, "lec_lgyosys", "lec",
                          True, False, "2026-09-18", lec_gate=False)
            lgyosys.lec_result = {
                "verdict": "proven", "solver": "lgyosys",
                "obligation": "pyrope-vs-verilog",
            }
            netlist = Row("dut", "default", "cdc", "sky130", "lec_netlist", "lec",
                          True, False, "2026-09-18", lec_gate=False)
            netlist.lec_verilog_result = {
                "verdict": "refuted", "solver": "cvc5",
                "obligation": "verilog-vs-netlist",
            }

            runner.gate([cvc5, lgyosys, netlist])

            for row in (cvc5, lgyosys, netlist):
                self.assertEqual(row.status, "ok")
                self.assertTrue(row.passed)


class Waves(TestCase):
    def test_legacy_simulation_manifests_plan_both_backends_and_honor_filters(self):
        from types import SimpleNamespace
        from lhdtrack.run import plan

        config = SimpleNamespace(id="one")
        test = SimpleNamespace(synth_flows=[], sim_flows=["sim_lhd_verilog", "sim_lhd_pyrope"],
                               lec_flows=[], configs=[config])
        names = ["sim_lhd_verilog", "sim_lhd_pyrope",
                 "sim_lhd_verilog_llvm", "sim_lhd_pyrope_llvm"]
        flows = {name: (SimpleNamespace(USES_TECH=False), Path(name + ".py")) for name in names}
        jobs = plan(Path("."), [test], None, flows, [])
        self.assertEqual([job.flow for job in jobs], names)
        jobs = plan(Path("."), [test], None, flows, [], ["sim_lhd_pyrope_llvm"])
        self.assertEqual([job.flow for job in jobs], ["sim_lhd_pyrope_llvm"])

    def test_after_jobs_start_only_when_the_first_wave_finished(self):
        import threading
        import time
        from types import SimpleNamespace

        from lhdtrack.run import Job

        events, lock = [], threading.Lock()

        class Probe(Runner):
            def _one(self, job):
                with lock:
                    events.append(("start", job.flow))
                time.sleep(0.2 if job.flow == "slow_synth" else 0)
                with lock:
                    events.append(("end", job.flow))
                return Row("dut", "default", "test", None, job.flow, "synth", True, False, "d")

            def gate(self, rows):
                return rows

        synth = SimpleNamespace(KIND="synth")
        after = SimpleNamespace(KIND="lec", AFTER=("slow_synth",))
        test = SimpleNamespace(name="dut")
        jobs = [Job(test, None, None, "slow_synth", synth, Path("s.py")),
                Job(test, None, None, "lec_netlist", after, Path("l.py"))]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tc = Toolchain(root, "test", "", {}, {}, {}, {})
            rows = Probe(root, tc, Cache(root), "test", {}).execute(jobs, jobs_parallel=4)
        self.assertEqual([r.flow for r in rows], ["slow_synth", "lec_netlist"])
        self.assertLess(events.index(("end", "slow_synth")), events.index(("start", "lec_netlist")))


class RuntimeData(TestCase):
    def test_file_only_edit_invalidates_cache_and_restages_image(self):
        from types import SimpleNamespace

        from lhdtrack.corpus import load_test
        from lhdtrack.run import Job

        with TemporaryDirectory() as directory:
            root = Path(directory)
            test_dir = root / "tests" / "dut"
            (test_dir / "data").mkdir(parents=True)
            (test_dir / "design.toml").write_text(
                '[design]\nname="dut"\ntop="dut"\nkind="sequential"\nsuite="cpu"\n'
            )
            image = test_dir / "data" / "program.hex"
            image.write_text("12\n")
            flow_file = root / "probe.py"
            flow_file.write_text("# simulated flow recipe\n")
            seen = []

            def probe(ctx):
                seen.append((ctx.work / "data" / "program.hex").read_text())
                return {"sim": {"checksum": seen[-1].strip(), "cycles": 1}}

            test = load_test(test_dir)
            tc = Toolchain(root, "test", "", {}, {}, {}, {})
            runner = Runner(root, tc, Cache(root), "test",
                            {"cache": {"baseline_flows": ["probe"]}}, keep_work=True)
            module = SimpleNamespace(KIND="sim", NEEDS=(), run=probe)
            job = Job(test, test.configs[0], None, "probe", module, flow_file)
            first = runner._one(job)
            self.assertEqual(first.status, "ok")
            self.assertEqual(first.sim["checksum"], "12")
            self.assertTrue(runner._one(job).cached)
            image.write_text("56\n")
            changed = runner._one(job)
            self.assertFalse(changed.cached)
            self.assertEqual(changed.sim["checksum"], "56")
            self.assertEqual(seen, ["12\n", "56\n"])
