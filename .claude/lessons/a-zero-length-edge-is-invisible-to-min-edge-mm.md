# A zero-length edge is invisible to `min_edge_mm` — and usually harmless

Two separate facts, and conflating them wasted a corpus sweep.

## It is invisible

`occshapes.min_edge_length` skips every edge at or below `ignore_below=1e-9`
**by design** — a degenerate edge constrains no element and would otherwise
report every model as pathological. So `step_report` publishes `None` or
something strictly greater, and a body carrying one reports a perfectly
healthy shortest edge. The consequence for `simcheck`'s `degenerate` branch is
recorded in the code and in `simcheck.py.md` — see commit `77417d0`.

## It is usually not a defect

This is the part that was got wrong. OpenCASCADE marks the singular edge at a
cone apex or a sphere pole as `Degenerated`: required topology for closing a
periodic surface, and present in healthy geometry. `ShapeFix` will never
remove one, correctly.

A first simplifier counted all of them as defects and the corpus refuted it in
one run — **326 zero-length edges in, 326 out**, 57 solids flagged, none
repaired, 50 reported as "the repair changed nothing measurable". Probing
`inertial-v6`: 16 zero-length edges, 16 marked `Degenerated`, **0 real
artefacts**. Correcting the detector moved the measured defect rate from 26%
of solids to 15.8%.

## How to act next time

`BRep_Tool.Degenerated(edge)` is the discriminator, and it costs nothing.
Before treating any zero-length edge as damage, ask the kernel whether it put
it there on purpose — `cra.simplifygeom.measure` splits them into
`degenerate_edges` (artefacts) and `seam_edges` (normal).

## The general form

A count is a hypothesis. This one said "zero length means broken", looked
entirely plausible, produced a confident number on real data, and was wrong —
and the thing that caught it was that the repair it justified changed nothing.
When a fix reliably achieves nothing, suspect the detector before the fix.
