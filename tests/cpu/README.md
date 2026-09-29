# CPU

A small five-stage RISC-V subset CPU from Veryl's simulator comparison. It has
32-bit datapaths, a 32-entry register file, separate 256-word instruction/data
memories, bypass forwarding, load-use stalls, and execute-stage branch flushes.
It is a benchmark core, not a complete RV32I implementation: preserve its
original permissive decode and word-only memory behavior.

The 19-word firmware exercises arithmetic, comparisons, memory operations and
branches. The harness repeats execution by resetting the CPU every 4096 clocks;
it does not spend most of a long run in the final halt loop. Its checksum folds
the public writeback output as `sum = sum * 131 + output` modulo 2^64, sampled
before each active edge, on every cycle including initial reset and periodic CPU
resets. The checksum has no reset and starts at zero under the simulation flow's
zero-initialization policy. Harness reset lasts four initial cycles; each CPU
restart lasts four clocks.

## Quick results

From the repository root:

```sh
make run RUN_ARGS="--test cpu --flow sim_verilator --flow sim_lhd_verilog --flow sim_lhd_pyrope --jobs 1"
make run RUN_ARGS="--test cpu --flow lec_lhd --jobs 1"
```

The default is 10,000,000 cycles for more stable timing, still shorter than
the corpus's usual two-second measurement target. Setup, host compilation and
execution are recorded separately in `data/ledger-<host>.jsonl`, with tool versions,
host identity, checksums and correctness gates. For longer measurement, change
`[sim].cycles` and re-record the reference with:

```sh
make run RUN_ARGS="--test cpu --flow sim_verilator --record-checksum --jobs 1"
```

All synthesis and equivalence flows are declared too; run only the selected
simulation flows for a quick comparison. A full `--test cpu` run also measures
synthesis and may take substantially longer.

## Verification

Run `20260928T171402` on 2026-09-28 uses actual file preload in both languages:
SystemVerilog `$readmemh` through native `inou.slang`, and Pyrope `std.readmemh`.
The shared image is [data/program.hex](data/program.hex). The tracker stages it
under each job's `data/` directory and hashes it into the cache key. The earlier
fixed-ROM adapter is removed. Reset and checksum harnesses are unchanged.

- All three simulator flows completed 10,000,000 cycles with the recorded
  checksum `11144181027426691521`, with no skips or cached measurements.
- Unbounded CVC5 equivalence is proven for the updated CPU (900 ms LEC stage),
  including the externally initialized instruction memory.
- A firmware-only edit (`00100093` to `00200093` in instruction zero) was run
  for 10M cycles using the same three executables from run `20260928T170945`.
  All produced `6312013518724208566`; staged images were then restored.
- The tracker regression verifies that a firmware-only edit invalidates its
  cached measurement and stages the changed bytes. Corpus checks, Python lint,
  and 11 runner/checksum unit tests pass.
- The local LiveHD readmem regression covers both formats, reset and writes,
  IR round trips, unsupported Slang forms, synthesis preservation, and formal
  checks. CPU synthesis was not rerun for this update.

| Flow | Frontend/setup | Host compile | Execution (best of 3) |
| --- | ---: | ---: | ---: |
| Verilator | 24 ms | 3297 ms | 416 ms |
| LiveHD + Verilog (Slang) | 1274 ms | 3625 ms | 770 ms |
| LiveHD + Pyrope | 72 ms | 3933 ms | 841 ms |

These are cold tracker runs with normal settings; frontend/setup includes Slang
elaboration for the Verilog leg. The toolchain manifest records the local
compiler hash and frozen HLOP/runtime-header hash. HLOP publication and the
LiveHD dependency-pin update remain pending; the compiler used a local override.

See [validation.jsonl](validation.jsonl) for sources, tool versions, individual
samples and proof metadata. Standard host-ledger records retain the earlier
failed importer trial as well as the successful runs.

[Origin and adaptations](origin.md) · [MIT license](LICENSE)

The simulation recipes now pass `+cycles=10000000` to both LiveHD and Verilator;
the Pyrope test signature retains its default. Legacy driver `--cycles N`
remains accepted. Plusarg migration was validated with all three simulation
flows at 10 million cycles (run `20260928T180052`), all retaining checksum
`11144181027426691521`; details are appended to `validation.jsonl`.
