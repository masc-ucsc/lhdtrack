"""Guard FakeRAM physical pin correctness and dimensional unit conversions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from .srams import normalize_bsg_lib, validate_lef

HEADER = '''library(fake) {
time_unit : "1ns"; voltage_unit : "1V"; current_unit : "1uA";
leakage_power_unit : "1uW"; pulling_resistance_unit : "1kohm";
capacitive_load_unit(1,pf);
'''
BASE = HEADER.replace('1ns', '1ps').replace('1uA', '1mA').replace('1uW', '1pW').replace('1,pf', '1,ff')


class FakeRamViews(unittest.TestCase):
    def test_timing_energy_capacitance_and_leakage_keep_physical_units(self):
        text = HEADER + '''
cell(ram) {
 area : 12.34; cell_leakage_power : 0.2;
 pin(clk) { capacitance : .025; min_period : 1.2; }
 cell_rise(t) { index_1 ("0.001, 0.1"); index_2 ("0.005, 0.5");
 values ("0.25, 0.4", "0.5, 0.7"); }
 rise_power(t) { values ("0.003, 0.005"); }
}}
'''
        out = normalize_bsg_lib(text, BASE)
        for expected in ('area : 12.34', 'cell_leakage_power : 200000',
                         'capacitance : 25', 'min_period : 1200',
                         'index_1 ("1, 100")', 'index_2 ("5, 500")',
                         'values ("250, 400", "500, 700")', 'values ("3, 5")'):
            self.assertIn(expected, out)

    def test_unknown_unit_profiles_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_bsg_lib(HEADER.replace('1ns', '1ps'), BASE)
        with self.assertRaises(ValueError):
            normalize_bsg_lib(HEADER, BASE.replace('1ps', '1ns'))

    def test_upper_write_data_bits_cannot_be_output_pins(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'ram.lef'
            text = '''MACRO ram
SIZE 10.8 BY 8.1 ;
PIN w0_wd_in[63]
 DIRECTION INPUT ;
END w0_wd_in[63]
PIN r0_rd_out[63]
 DIRECTION OUTPUT ;
END r0_rd_out[63]
END ram
'''
            path.write_text(text)
            self.assertEqual(validate_lef(path)['pins'], 2)
            path.write_text(text.replace('DIRECTION INPUT', 'DIRECTION OUTPUT'))
            with self.assertRaises(ValueError):
                validate_lef(path)
