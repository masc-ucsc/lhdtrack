"""Frequency claims require compatible timing and unbounded exact-netlist proof."""
from copy import deepcopy
from unittest import TestCase

from lhdtrack.report.usyn_ablation import frequency_ratio, summarize


def mapping(delay, digest):
    return dict(status="ok", host="host", host_class="Linux", tech="asap7",
                liberty_sha256="lib", qor=dict(netlist_sha256=digest,
                sdc_sha256="sdc", source_sha256={"rtl": "source"}),
                sta=dict(opensta_ns=delay, time_unit="ps", sdc_period_ns=400,
                         opensta_metric="sdc-minimum-period"))


def proof(digest, bounded=False):
    return dict(lec_verilog_result=dict(verdict="proven", bounded=bounded,
                                       netlist_sha256=digest))


class FrequencyComparison(TestCase):
    def test_only_compatible_proven_emissions_form_a_ratio(self):
        base, measured = mapping(200, "abc"), mapping(100, "usyn")
        bp, mp = proof("abc"), proof("usyn")
        self.assertEqual(frequency_ratio(base, measured, bp, mp), 2)
        for change in (dict(host="other"), dict(liberty_sha256="other"), dict(tech="sky130")):
            self.assertIsNone(frequency_ratio(base, dict(measured, **change), bp, mp))
        for field in ("sdc_sha256", "source_sha256"):
            altered = deepcopy(measured)
            altered["qor"][field] = "changed"
            self.assertIsNone(frequency_ratio(base, altered, bp, mp))
        self.assertIsNone(frequency_ratio(base, measured, bp, proof("usyn", bounded=True)))
        self.assertIsNone(frequency_ratio(base, measured, bp, proof("different")))
        bad = dict(mp, lec_aux_result=dict(verdict="refuted"))
        self.assertIsNone(frequency_ratio(base, measured, bp, bad))
        measured["sta"]["sdc_period_ns"] = 800
        self.assertIsNone(frequency_ratio(base, measured, bp, mp))

    def test_missing_failed_and_skipped_slots_remain_in_matrix(self):
        slots = [dict(test="a", config="one"), dict(test="missing", config="one")]
        specs = {name: dict(host="host", tech="asap7", liberty_sha256="lib",
                            run_id=name, slots=slots) for name in ("abc", "selection")}
        history = [dict(run_id="abc", test="a", config="one",
                        flow="syn_lhd_verilog_no_satopt", status="failed"),
                   dict(run_id="selection", test="a", config="one",
                        flow="syn_lhd_verilog_usyn_no_satopt", status="skipped")]
        result = summarize(history, specs)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["counts"]["abc"]["failed"], 1)
        self.assertEqual(result["counts"]["selection"]["skipped"], 1)
        self.assertEqual(result["counts"]["selection"]["pending"], 1)
        self.assertEqual(result["headline"]["selection"]["count"], 0)
        specs["selection"]["host"] = "other"
        with self.assertRaisesRegex(ValueError, "same host"):
            summarize(history, specs)

    def test_common_population_and_native_iteration_require_paired_proofs(self):
        slots = [dict(test=t, config="one") for t in ("a", "b")]
        specs = {name: dict(host="host", tech="asap7", liberty_sha256="lib",
                            run_id=name, slots=slots)
                 for name in ("abc", "feedback", "improved")}
        history = []
        for name, factor in (("abc", 4), ("feedback", 2), ("improved", 1)):
            suffix = "" if name == "abc" else "_usyn"
            for index, test in enumerate(("a", "b"), start=1):
                identity = dict(run_id=name, test=test, config="one")
                digest = f"{name}-{test}"
                history.append(dict(mapping(50 * index * factor, digest), **identity,
                                    comparable=True, flow=f"syn_lhd_verilog{suffix}_no_satopt"))
                if name != "improved" or test != "b":
                    history.append(dict(proof(digest), **identity,
                                        flow=f"lec_netlist_verilog{suffix}_no_satopt"))
        result = summarize(history, specs)
        self.assertEqual(result["headline"]["feedback"]["count"], 2)
        self.assertEqual(result["headline"]["improved"]["count"], 1)
        self.assertEqual({m["count"] for m in result["common_headline"].values()}, {1})
        self.assertEqual(result["common_headline"]["improved"]["frequency_ratio"], 4)
        self.assertEqual(result["native_iteration"], dict(count=1, frequency_ratio=2))
