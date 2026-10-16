"""Keep independent backend outcomes separate from the native proof."""
from types import SimpleNamespace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from lib.lec import check_elaboration_internal_error, classify, lhd_verdict, run_lec
from lec_netlist import _check
from lhdtrack.context import FlowError


class IndependentVerdicts(unittest.TestCase):
    def test_supervisor_memory_stop_is_distinct_from_checker_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            result = work / 'check.json'
            sidecar = result.with_suffix('.resource.json')
            event = {
                'schema_version': 1, 'origin': 'lhdtrack-run-supervisor',
                'kind': 'memory_limit', 'signal': 9, 'result_json': str(result.resolve()),
                'reason': 'Stopped by the shared 12 GiB memory limit',
                'limit_kb': 12582912, 'peak_kb': 12582913,
            }
            for rc, fresh_event, expected in (
                (-9, event, 'timeout'), (-11, event, 'error'),
                (-9, None, 'error'), (-9, {**event, 'result_json': '/other.json'}, 'error'),
            ):
                with self.subTest(rc=rc, event=fresh_event):
                    sidecar.write_text(json.dumps(event))  # stale event from an earlier attempt

                    def run(*args, **kwargs):
                        self.assertFalse(sidecar.exists())
                        if fresh_event is not None:
                            sidecar.write_text(json.dumps(fresh_event))
                        return SimpleNamespace(rc=rc, timed_out=False, ms=10)

                    ctx = SimpleNamespace(
                        tool=lambda name: Path('/staged/lhd'), work=work,
                        top='dut', lec_timeout_s=300, run=run,
                    )
                    block = _check(ctx, impl='lg:impl', ref='lg:ref', models='lg:models',
                                   solver='cvc5', obligation='verilog-vs-netlist', label='check')
                    self.assertEqual(block['verdict'], expected)
                    self.assertEqual('resource_limit' in block, expected == 'timeout')

    def test_lhd_timeout_verdict_is_a_timeout_not_unknown_or_a_failure(self):
        # `lhd lec` reports a reached formal.timeout itself (exit 0): record it as
        # a timeout -- not an error, not inconclusive, and never a proof.
        result = {"status": "pass", "lec": {"verdict": "timeout", "bounded": False}}
        self.assertEqual(classify(result, 0, solver='cvc5'), 'timeout')
        self.assertEqual(classify(result, 0, solver='cvc5', elapsed_ms=1, timeout_s=300), 'timeout')

    def test_modifier_verdicts_map_onto_the_ledger_vocabulary(self):
        # Current lhd: every verdict carries a modifier. Only refuted-cex is a
        # design refutation; the other REFUTED-<why> are failures with no answer.
        cases = {
            'proven-full': 'proven', 'proven-bounded': 'proven', 'tested-sampled': 'inconclusive',
            'timeout-time': 'timeout', 'timeout-memory': 'timeout', 'refuted-cex': 'refuted',
            'refuted-unsupported': 'unsupported', 'refuted-interface': 'error',
            'refuted-unchecked': 'error', 'refuted-vacuous': 'error', 'refuted-unexplained': 'error',
        }
        for word, ledger in cases.items():
            rc = 10 if word == 'refuted-cex' else (7 if word.startswith('refuted-') else 0)
            result = {'status': 'pass' if rc == 0 else 'fail', 'lec': {'verdict': word}}
            self.assertEqual(classify(result, rc, solver='cvc5', elapsed_ms=1, timeout_s=300), ledger, word)
            self.assertEqual(lhd_verdict(result), word)
            xresult = {'status': 'pass', 'lec': {'verdict': 'proven-full', 'crosscheck': {'verdict': word}}}
            self.assertEqual(classify(xresult, rc, solver='lgyosys'), ledger, word)

    def test_lgcheck_incomplete_is_undecided_not_unsupported(self):
        result = {'status': 'fail', 'lec': {'verdict': 'refuted-unchecked',
                  'crosscheck': {'verdict': 'incomplete', 'exit_code': 2}},
                  'error': {'class': 'unsupported', 'message': 'REFUTED-unchecked: lgcheck could not confirm'}}
        self.assertEqual(classify(result, 7, solver='lgyosys', elapsed_ms=1000, timeout_s=300), 'inconclusive')
        self.assertEqual(classify(result, 7, solver='lgyosys', elapsed_ms=600000, timeout_s=300), 'timeout')

    def test_unknown_correspondence_is_not_a_refutation(self):
        result = {
            'status': 'fail', 'lec': {'verdict': 'unknown', 'bounded': False},
            'error': {'class': 'equiv_fail',
                      'message': "lec compared NOTHING: correspondence incomplete",
                      'hint': 'flat retry also inconclusive: checkSat returned unknown'},
        }
        self.assertEqual(classify(result, 6, solver='cvc5', elapsed_ms=273336,
                                  timeout_s=300), 'inconclusive')
        self.assertEqual(classify(result, 6, solver='cvc5', elapsed_ms=315703,
                                  timeout_s=300), 'timeout')
        result['lec']['verdict'] = 'refuted'
        self.assertEqual(classify(result, 6, solver='cvc5'), 'refuted')

    def test_internal_elaboration_error_cannot_be_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text(json.dumps({'error': {
                'class': 'internal', 'message': 'Concat lane wider than declared window'}}))
            with self.assertRaisesRegex(FlowError, 'Concat lane'):
                check_elaboration_internal_error(path)
            path.write_text(json.dumps({'error': {'class': 'unsupported', 'message': 'no top'}}))
            check_elaboration_internal_error(path)

    def test_crosscheck_does_not_inherit_native_proof(self):
        for verdict, code, expected in [
            ('proven', 0, 'proven'), ('refuted', 1, 'refuted'),
            ('unknown', 2, 'inconclusive'), ('unknown', 99, 'error'),
        ]:
            with self.subTest(verdict=verdict, code=code):
                result = {
                    'status': 'pass' if code == 0 else 'fail',
                    'lec': {'verdict': 'proven', 'solver': 'cvc5',
                            'crosscheck': {'verdict': verdict, 'exit_code': code}},
                    'error': {'class': 'unsupported',
                              'message': 'lgcheck cross-check did not decide equivalence'},
                }
                self.assertEqual(classify(result, code, solver='lgyosys'), expected)
                self.assertEqual(classify(result, code, solver='cvc5'), 'proven')

    def test_older_crosscheck_json_keeps_unknown_and_timeout_distinct(self):
        result = {'status': 'fail', 'lec': {'verdict': 'proven'},
                  'error': {'class': 'unsupported',
                            'message': 'lgcheck cross-check did not decide equivalence'}}
        self.assertEqual(classify(result, 7, solver='lgyosys', elapsed_ms=1000,
                                  timeout_s=300), 'inconclusive')
        self.assertEqual(classify(result, 7, solver='lgyosys', elapsed_ms=600000,
                                  timeout_s=300), 'timeout')

    def test_emission_failure_is_not_a_yosys_proof(self):
        result = {'status': 'fail', 'lec': {'verdict': 'proven'},
                  'error': {'class': 'unsupported', 'message': 'cyclic expression'}}
        self.assertEqual(classify(result, 7, solver='lgyosys'), 'error')

    def test_elaboration_timeout_never_runs_solver(self):
        for failed_side in ('Verilog', 'Pyrope'):
            with self.subTest(side=failed_side):
                ok = SimpleNamespace(ok=True, timed_out=False, ms=1)
                timeout = SimpleNamespace(ok=False, timed_out=True, ms=80000)
                ctx = SimpleNamespace(
                    tool=Mock(return_value=Path('/staged/lhd')), top='dut', work=Path('.'),
                    chparams=lambda: {},
                    test=SimpleNamespace(pyrope_status='auto', pyrope_top=Path(__file__),
                                         filelist=Path('filelist.f'), lec_status='proven',
                                         verilog_args=lambda _: []),
                    run=Mock(side_effect=[timeout] if failed_side == 'Verilog' else [ok, timeout]),
                )
                result = run_lec(ctx, 'lgyosys', 10)
                self.assertEqual(result['lec']['verdict'], 'timeout')
                self.assertIn(failed_side, result['lec']['reason'])
                self.assertFalse(result['lec_drift'])
                for call in ctx.run.call_args_list:
                    self.assertEqual(call.kwargs['timeout'], 80)
                    self.assertNotEqual(call.args[0], 'lec')


if __name__ == '__main__':
    unittest.main()
