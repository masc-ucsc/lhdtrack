# Repository Guidelines

## Project Structure

`src/lhdtrack/` contains the CLI, runner, cache, ledger, and reporting code. `flows/`
contains measurement recipes and `flows/lib/` their helpers. Each `tests/<name>/` holds
`design.toml`, an upstream `LICENSE`, Verilog/Pyrope sources, harnesses, and constraints.
Importers and generators live in `tools/`; pinned technology definitions live in `tech/`.

Treat `data/ledger-<host>.jsonl` as committed, append-only truth. `target/` is a
gitignored rendering rebuilt from the ledger; `var/` is disposable machine-local state.

## Build, Test, and Development Commands

- `make toolchain` builds and stages pinned tools and Liberty files.
- `make check` validates corpus structure; problems fail, capability notes do not.
- `make lint` compile-checks Python in `src/`, `flows/`, and `tools/`.
- `make run RUN_ARGS="--test add"` runs one test end to end and regenerates reports.
- `make report` rebuilds `target/` without measuring; `make show` prints the latest result.
- `lhdtrack new my_block --top my_block --kind sequential` scaffolds a test;
  `lhdtrack import seed my_block` generates its paired harnesses, drivers, and SDCs.

Before submitting, run `make check`, `make lint`, and one representative end-to-end test.

## Coding Style and Naming

Use Python 3.11+, four-space indentation, type hints, and the configured 100-character
line limit. Use `snake_case` for modules/functions, `PascalCase` for classes, and uppercase
constants. Test names are flat, unique, and match their manifest. A flow exposes `NAME`,
`KIND`, `NEEDS`, `USES_TECH`, and `run(ctx)`; its contents are part of the cache key.

## Measurement and Testing Invariants

- Resolve every executable only through `var/toolchain/toolchain.json`; never use `PATH`,
  hardcoded tool paths, or `shutil.which`.
- Declare synthesis, simulation, and equivalence for every test. Missing capabilities or
  inputs produce explicit `skipped` rows carrying the tool's reason.
- Parse a tool's result JSON, not log keywords, and remove the echoed `$ argv` line before
  any necessary log scan.
- Keep gates in `Runner.gate`; flows measure and synthesis flows finish through
  `qor_endpoint.evaluate()`.
- Synthesis flows use LiveHD defaults, including native memory preservation.
  `lec_netlist` must use the same synthesis profile and SDC-derived `delay` as the
  synth flows, so its proof covers the measured mapping policy. The SAT comparison
  profile changes only `pass.abc.satopt=false`.
- Never compare across `host_class` or Liberty hashes. Report `baseline / measured` ratios,
  so higher is always better.
- Limit headline geomeans to idiomatic, LEC-proven Pyrope tests. Do not weaken coverage or
  delete bad ledger measurements.
- Harness checks must wire each DUT port once and exclude clocks/resets from random input;
  simulator agreement cannot detect a shared harness bug.
- A top module declares NO overridable parameter. `tools/monomorphize.py` pins the chosen
  point into `verilog/<top>.sv` as `localparam`, `[[config]].params` stays empty, and
  `make check` fails a top that regains a parameter port list. Intermediate modules stay
  parameterized. This is what lets yosys, slang, verilator, the generated harness and the
  `.prp` -- which cannot take a parameter at all -- elaborate the same circuit with no flags,
  so an LEC refutation means the two languages disagree rather than the two flag sets.

Imports preserve the upstream license verbatim, record the exact revision, and copy
included headers so every front end reads identical bytes.

## Commits and Pull Requests

Use concise, imperative commit subjects and keep changes focused. Explicitly call out
baseline recipe changes because they invalidate cached results and create a timeseries
step. Pull requests describe affected tests/flows, exact validation, skips or intentional
failures, and ledger changes. Include screenshots only for report presentation changes;
never commit `target/` or `var/`.
