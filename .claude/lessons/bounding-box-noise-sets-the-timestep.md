# A 10-nanometre bounding-box artefact priced a real job at 3,699× its budget

A submission was refused with:

```
lc1 needs 7.50e+13 element-cycles, 3699.1x the 2.03e+10 this job can spend on
it (948,463 elements x 79,086,090 cycles at dt = 5.06e-13 s)
  - fill_p011: smallest edge 0.000 mm across 275,415 elements
```

`fill_p011` is a **stand-in box** — a simplified neighbour, not the builder's
geometry. A box should not have a zero-length edge.

## The mechanism

Tier 0 bounding boxes come from the CAD kernel with floating-point error. In
that model, `p011` spans

```
x ∈ [-146.75001083075134, 146.75001083074736]
```

— note the asymmetry in the tenth decimal — beside a target spanning exactly
±146.75. `loadcase._add_standins` fragments the boxes together, and OCC
faithfully reproduces the **10.7 nanometre overhang** as a 10.7 nanometre
sliver solid.

**The stable timestep goes with the shortest edge anywhere in the model**, so
one invisible artefact set the cycle count for all 948,463 elements.

Measured with two boxes and nothing else:

| overhang | solids | tets | shortest edge |
|---|---|---|---|
| 0 | 2 | 121,133 | **1.468 mm** |
| 1.073e-05 mm | 2 | 121,431 | **1.073e-05 mm** |
| 1e-3 mm | 2 | 121,476 | 1.000e-03 mm |

Same element count every time. The entire cost is in the timestep.

## The fix

Snap every stand-in corner to a micron grid (`STANDIN_SNAP_MM = 1e-3`) before
building the box. Faces meant to be coincident then are, and a box left thinner
than the grid collapses to zero and is dropped by the existing
`min(size) <= 0` guard rather than meshed.

A micron is far below what the construction claims to resolve — the code says a
stand-in "only has to be the right material in roughly the right place" — and
far above the noise.

## What to take from it

- **Geometry that is *nearly* coincident is worse than geometry that is far
  apart.** A boolean will reproduce a nanometre gap exactly, and an explicit
  solver will then pay for it on every cycle of every element.
- **Snap before you boolean.** Any coordinate that arrives from a CAD kernel,
  a bounding box or a file format should be quantised to a tolerance the
  physics does not care about, before it reaches an operation that can turn a
  rounding error into a feature.
- **I twice guessed the cause and was twice wrong** — first that clipping left
  a sliver, then that the overhang was against the target (it sits 22 mm clear
  of it in z). What settled it was a ten-line experiment fragmenting two boxes
  with the offset copied from the failing model. Reproduce the mechanism in
  isolation before changing the code it lives in.
