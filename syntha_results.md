# Synthesis attributes on the Pyrope corpus: what we found (ASAP7, `lhd` ABC)

Measured 2026-10-10/11 on satsuma, livehd `263b85de4`..`fd89c17a1`, ASAP7 merged
Liberty, each test's own SDC period, whole-design OpenTimer STA, every result LEC-checked
against the unannotated source (0 refutations; "unknown" = proof timeout).

## Headline

1. **The multiplier default was wrong, not the attribute surface.** Pinning
   `synth.multiplier` to the three-greedy column compressor (`tdm`, formerly spelled `sn`)
   on the designs with a real data multiply was 13 wins / 0 losses (geomean -41 % area,
   -62 % delay; `sqr_uns` 6279 -> 2059 cells, 3754 -> 646 ps). `auto` now resolves to
   `tdm` on both mappers, so this is a default change and shows up with no annotation.
2. **A squarer** (`x*x`) places each symmetric partial product once: 16-bit signed
   1618 -> 1158 cells, 2905 -> 925 ps against the old default.
3. **After those, 36 of ~190 tests still gain > 3 % from a source attribute** (see
   `annotated/README.md`). `synth.color` on the top module is 23 of them; it is a per-design
   choice (about as many designs lose), with no size/region-count rule that predicts it.
4. **Adder attributes rarely help on their own** (geomean +3-5 % area when pinned
   alone); the default `rca` plus ABC restructuring is hard to beat. `cla` helps
   three-operand adds.
5. **Shifters** like `barrel="reverse"` together with a color (`br_shift_left/right`
   -44 % / -48 % area, -13 % / -14 % delay).

## livehd defects found and fixed along the way (all in shared code)

- Call-site specialization dropped the compact-loop descriptor, so an explicit color
  around loops failed with `LT/GT requires an operand in each bank` (arbiters).
- Shift-amount wiring (`select << 5`, an add of a constant) was split from its cut
  shifter, losing the known-zero low bits: a 512-bit mux went 913 -> 5627 cells as soon
  as any attribute was present.
- A module/block default was stamped on every cell and on every sub-instance, so an
  unrelated pin made cells look source-anchored (no ware extraction, no hierarchy
  absorption, per-call copies). Stamping and specialization are now operation-aware.
- Constant multiplies now use the non-adjacent form, identical for every architecture.
- `get_driver_pin()` on a compact loop's two-driver carry-in asserted in dbg builds.
- The arithmetic vocabulary (names, encoding, candidate families) is one shared header.

## Not fixed / open

- A module-level attribute on a 1-bit mux gate instantiated ~2000 times
  (`br_gate_mux2` inside `br_cdc_fifo_flops`) still degrades that design (539 -> 4075 ps).
  Compile output is structurally identical, so it is a color/partition interaction
  with a module that carries a scope marker; not an arithmetic effect.
- Auto-exploration pin-hints print only to the ABC pass log (no `file:line`, none for
  USYN); `synth.tune.file` import and the soft min/max bounds were not probed.
- No Booth radix-4 multiplier: the three-greedy compressor already closes the gap
  to the yosys baseline on every data multiply measured, so it was not worth its
  per-bit encoding cost without a design that still trails.
- USYN was checked on the multiplier family only (8/8 wins for `tdm` over its old
  default); its color/adder/barrel behavior is the same shared code path but was not
  swept corpus-wide.
