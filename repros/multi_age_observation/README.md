# Observing several pipeline ages in one output

Pyrope's logical-time checker rejects an RTL-style observation bus containing
the current `q` values of successive feed-forward registers. This is useful
for catching accidental cycle mixing in data paths, but surprising for status
or debug ports which deliberately expose every physical stage at once.

`refused.prp` is the small reproducer. It reports that the `set_mask` mixes
values at different cycles. Adding `::[timecheck=false]` to the module is the
current workaround; it generates the expected two flops and combinational
`{q1, q0, in}` output.

Wish: provide a local, explicit "physical current value" annotation (or allow
an observation-only pack) so a whole RTL-style module does not need to disable
logical-time checking.

