"""Liberty latch discovery: plain latches by polarity; isolation/dont_use cells never chosen."""
import tempfile
import unittest
from pathlib import Path

from lib.liberty_latch import latch_cells, techmap_verilog

LIB = """library (t) {
  cell ("big_hi") { area : 9.0;
    pin ("D") { direction : "input"; } pin ("G") { direction : "input"; }
    pin ("Q") { direction : "output"; function : "IQ"; }
    latch ("IQ","IQN") { data_in : "D"; enable : "G"; } }
  cell ("small_hi") { area : 2.0;
    pin ("D") { direction : "input"; } pin ("G") { direction : "input"; }
    pin ("Q") { direction : "output"; function : "IQ"; }
    latch ("IQ","IQN") { data_in : "D"; enable : "G"; } }
  cell ("iso_hi") { area : 1.0; is_isolation_cell : "true";
    pin ("D") { direction : "input"; } pin ("S") { direction : "input"; }
    pin ("Q") { direction : "output"; function : "IQ"; }
    latch ("IQ","IQN") { data_in : "D"; enable : "S"; } }
  cell ("reset_lo") { area : 1.0;
    pin ("D") { direction : "input"; } pin ("GN") { direction : "input"; } pin ("R") { direction : "input"; }
    pin ("Q") { direction : "output"; function : "IQ"; }
    latch ("IQ","IQN") { data_in : "D"; enable : "!GN"; clear : "R"; } }
  cell ("lo") { area : 3.0;
    pin ("D") { direction : "input"; } pin ("GN") { direction : "input"; }
    pin ("Q") { direction : "output"; function : "IQ"; }
    latch ("IQ","IQN") { data_in : "D"; enable : "!GN"; } }
}
"""


class LibertyLatch(unittest.TestCase):
    def test_smallest_plain_latch_per_polarity(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "t.lib"
            lib.write_text(LIB)
            self.assertEqual(latch_cells(lib), {True: ("small_hi", "D", "G", "Q"),
                                                False: ("lo", "D", "GN", "Q")})
            text = techmap_verilog(lib)
            self.assertIn("\\small_hi _TECHMAP_REPLACE_ (.G(E), .D(D), .Q(Q));", text)
            self.assertIn("module \\$_DLATCH_N_", text)


if __name__ == "__main__":
    unittest.main()
