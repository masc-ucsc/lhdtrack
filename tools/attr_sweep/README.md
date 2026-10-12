# attr_sweep: find synthesis attributes that help

Experiment tooling used for `annotated/` (see `../../syntha_results.md`). Paths are
host-specific on purpose (scratch under /var/tmp/renau/exp, a locally built `lhd`
in LHD); treat it as a recipe, not a supported flow.

- `syn.py TEST [--prp FILE] [--tag T] [--set k=v] [--mapper abc|usyn] [--lec]`
  one `lhd synth` run, same arguments as `flows/syn_lhd_pyrope.py`, prints cells,
  area, OpenTimer STA delay and optionally the LEC verdict against the ORIGINAL source.
- `sweep.py SPEC_JSON OUT.json` (env JOBS TMO LEC TESTS) runs a spec
  `{tag: attrs}` over every Pyrope test. `attrs` goes on the top declaration;
  `ALL:` puts it on every module (`{name}` expands); `OPS:<mult|add|shift>:` puts it
  only on modules whose own source contains that operation.
- `fam2.py` summarizes attribute families against a baseline run.
