"""Technology-map reports must retain their mapped metrics rather than zeros."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from qor_endpoint import _lhd_qor


class MappingReport(TestCase):
    def test_native_tmap_definition_regions_have_real_totals(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "qor.json"
            path.write_text(json.dumps(dict(kind="technology-map", regions=[
                dict(gates=3, area=1.5, delay=100), dict(gates=2, area=2.0, delay=80)])))
            result = _lhd_qor(path)
            self.assertEqual(result["lhd_cells"], 5)
            self.assertEqual(result["lhd_area_um2"], 3.5)
            self.assertEqual(result["abc_max_delay_ns"], 100)
            self.assertEqual(result["regions"], 2)

    def test_missing_mapped_totals_do_not_invent_zero_area(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "qor.json"
            for text in ("{", json.dumps(dict(kind="usyn", regions=[]))):
                path.write_text(text)
                result = _lhd_qor(path)
                self.assertNotIn("area_um2", result)
                self.assertNotIn("lhd_area_um2", result)
                self.assertIn("lhd_qor_note", result)
