"""Cone comparisons must count shared gates once and stop at state boundaries."""
from unittest import TestCase

from lib.logic_cones import compare_cones, index_cones


def gate(kind, inputs, output):
    return {"type": kind,
            "port_directions": {"A": "input", "Y": "output"},
            "connections": {"A": inputs, "Y": [output]}}


def flop(data, output):
    return {"type": "FF", "port_directions": {"D": "input", "Q": "output"},
            "connections": {"D": [data], "Q": [output]}}


def module(cells, outputs=None):
    ports = {"i": {"direction": "input", "bits": [2]}}
    ports.update({name: {"direction": "output", "bits": bits}
                  for name, bits in (outputs or {}).items()})
    return {"cells": cells, "ports": ports}


def index(doc):
    return index_cones(doc, {"G", "FF"}, {"FF"})


class LogicConesTest(TestCase):
    def test_reconvergent_fanin_and_register_group_union(self):
        doc = module({"shared": gate("G", [2], 3),
                      "left": gate("G", [3], 4), "right": gate("G", [3], 5),
                      "merge": gate("G", [4, 5], 6),
                      "pipe[0]": flop(6, 7), "pipe[1]": flop(4, 8)})
        result = compare_cones(index(doc), index(doc))
        self.assertEqual(result["matched_endpoints"], 2)
        self.assertEqual(result["groups"][0]["baseline"]["cells"], 4)
        self.assertEqual(result["groups"][0]["baseline"]["levels"], 3)
        self.assertEqual(result["groups"][0]["baseline"]["source_bits"], 1)
        self.assertEqual(sorted(row["baseline_cells"] for row in result["endpoints"]), [2, 4])

    def test_register_feedback_is_a_boundary_and_constants_have_no_cone(self):
        doc = module({"feedback": gate("G", [7], 3), "state[0]": flop(3, 7)},
                     {"constant": ["0"], "q": [7]})
        result = index(doc)
        cone = result.metrics([result.endpoints["register:state[0].D[0]"]])
        self.assertEqual((cone["cells"], cone["levels"], cone["source_bits"]), (1, 1, 1))
        self.assertEqual(cone["source_groups"], {"register:state": 1})
        self.assertEqual(result.endpoints["output:constant[0]"].cells, 0)
        self.assertEqual(result.endpoints["output:q[0]"].cells, 0)

    def test_unmatched_endpoints_are_reported_and_not_in_matched_unions(self):
        baseline = index(module({"pipe[0]": flop(2, 3), "pipe[1]": flop(2, 4)}))
        measured = index(module({"g": gate("G", [2], 5), "pipe[0]": flop(5, 3),
                                 "extra[0]": flop(5, 4)}))
        result = compare_cones(baseline, measured)
        self.assertEqual(result["baseline_only"], ["register:pipe[1].D[0]"])
        self.assertEqual(result["measured_only"], ["register:extra[0].D[0]"])
        self.assertEqual(result["groups"][0]["matched_endpoints"], 1)
        self.assertEqual(result["groups"][0]["cell_delta"], 1)

    def test_invalid_netlists_fail_instead_of_underreporting_cones(self):
        cases = [({"g": gate("UNKNOWN", [2], 3)}, "not a mapped"),
                 ({"a": gate("G", [2], 3), "b": gate("G", [2], 3)}, "multiple drivers"),
                 ({"g": gate("G", [99], 3)}, "undriven"),
                 ({"a": gate("G", [4], 3), "b": gate("G", [3], 4)}, "cycle")]
        for cells, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                index(module(cells))

    def test_disjoint_accounting_exposes_sharing_loss_between_equal_size_cones(self):
        baseline = index(module({"shared": gate("G", [2], 3),
                                 "a[0]": flop(3, 4), "b[0]": flop(3, 5)}))
        measured = index(module({"first": gate("G", [2], 3), "second": gate("G", [2], 6),
                                 "a[0]": flop(3, 4), "b[0]": flop(6, 5)}))
        result = compare_cones(baseline, measured)
        self.assertTrue(all(r["cell_delta"] == 0 for r in result["groups"]))
        self.assertTrue(all(r["same_source_bits"] for r in result["groups"]))
        members = result["disjoint_group_memberships"]
        self.assertEqual(sum(r["cell_delta"] for r in members), 1)
        shared = next(r for r in members if len(r["groups"]) == 2)
        self.assertEqual(shared["cell_delta"], -1)
