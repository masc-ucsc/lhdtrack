import tempfile
import unittest
from pathlib import Path

from lib.logic_depth import liberty_cells, mapped_depth


def cell(kind, inputs, output):
    return {'type': kind, 'port_directions': {'A': 'input', 'Y': 'output'},
            'connections': {'A': inputs, 'Y': [output]}}


class LogicDepthTest(unittest.TestCase):
    def test_register_feedback_is_a_cut(self):
        module = {'cells': {'a': cell('AND', [1, 2], 3),
                            'b': cell('BUF', [3], 4),
                            'q': cell('DFF', [4], 2),
                            'c': cell('AND', [2, 1], 5)}}
        self.assertEqual(mapped_depth(module, {'AND', 'BUF', 'DFF'}, {'DFF'}), 2)

    def test_reconvergent_longest_path_and_aliases(self):
        module = {'cells': {'a': cell('G', [1], 2), 'b': cell('G', [2], 3),
                            'c': cell('G', [2, 3, 3], 4), 'd': cell('G', [4], 5)}}
        self.assertEqual(mapped_depth(module, {'G'}, set()), 4)

    def test_flatten_scope_metadata_is_not_logic(self):
        module = {'cells': {'scope': {'type': '$scopeinfo', 'connections': {}},
                            'gate': cell('G', [1], 2)}}
        self.assertEqual(mapped_depth(module, {'G'}, set()), 1)

    def test_constants_wires_and_state_only(self):
        self.assertEqual(mapped_depth({'cells': {}}, set(), set()), 0)
        self.assertEqual(mapped_depth({'cells': {'q': cell('DFF', ['0'], 2)}},
                                      {'DFF'}, {'DFF'}), 0)

    def test_invalid_netlists_are_not_zero_depth(self):
        for cells in ({'a': cell('G', [2], 1), 'b': cell('G', [1], 2)},
                      {'a': cell('G', [0], 1), 'b': cell('G', [0], 1)},
                      {'a': cell('unknown', [0], 1)}):
            with self.assertRaises(ValueError):
                mapped_depth({'cells': cells}, {'G'}, set())

    def test_liberty_state_classification_ignores_comments_and_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cells.lib'
            path.write_text('''library(test) {
              /* cell(fake) { ff(I, N) {} } */
              cell("logic") { note: "cell(fake) { ff(I, N) {} }"; pin(Y) { function: "A"; } }
              cell(flop) { ff(I, N) { next_state: "D"; } }
              cell(latch) { latch(I, N) { data_in: "D"; } }
            }''')
            known, sequential = liberty_cells([path])
            self.assertEqual(known, {'logic', 'flop', 'latch'})
            self.assertEqual(sequential, {'flop', 'latch'})


if __name__ == '__main__':
    unittest.main()
