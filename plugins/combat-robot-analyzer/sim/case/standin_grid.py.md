# standin_grid.py

## Function

Structured (transfinite) grids for stand-in boxes, so a stand-in can never
sliver. `v1/docs/history/mesh-sliver-anatomy.md` attributes 11 of 35 case minima
to stand-ins — plain boxes producing 0.01 mm elements through two
unstructured-meshing artefacts (slab-spanning crossing-edge slivers, 1D nodes
duplicated microns from a corner). A lattice admits neither. The measured
ceiling of removing the box class entirely is a corpus mean of ×0.82
element-cycles, with six cases between ×0.03 and ×0.30.

Copied from v1 (`cra.standin_grid`); imports rewritten to `case.`.

## Interface

- `structure_standins(gmsh, volumes, cell_mm) -> GridResult` — the only
  caller-facing function. Declares transfinite grids on every volume in
  `volumes` that still has box topology; returns the rest as
  `GridResult.fallback` (`{tag: thinnest bbox extent}`) for the caller to
  size-cap. Declarations only; meshing stays in `simgeom.generate_mesh`.
- `GridResult` — `structured: tuple[int, ...]`, `fallback: Mapping[int, float]`.
- `read_topology(gmsh, volume) -> BoxTopology | None` — `None` when the
  fragment broke box topology (≠6 faces, a face without exactly 4 curves and
  4 corners, a curve without 2 distinct endpoints, ≠8 corners in total).
- `plan_grid(boxes, cell_mm, *, aspect_cap=ASPECT_CAP) -> GridPlan` — pure;
  per-curve transfinite node counts consistent across shared curves and
  opposite face pairs. Raises `ValueError` on a non-positive cell.
- `BoxTopology`, `GridPlan` — the data between the reader and the planner.
- `ASPECT_CAP` (4.0) — in-plane cell pitch may exceed the box's thin extent
  by at most this factor.

Consumed by `loadcase.mesh_geometry` behind the `structured_standins` payload
flag, off by default (`build_case(structured_standins=False)`; `case_cli.py
build` does not pass it, so the agent's tools never enable it). In v1
`cra.calibrate.mesh_algo` reached it via the `-tf` variant suffix.

## Implementation

**Pure planning, then gmsh, in that order.** `plan_grid` computes the entire
declaration set before `structure_standins` makes its first gmsh call, so a
volume that disqualifies can never leave half a grid behind — a half-declared
transfinite volume fails the whole mesh, not one box. Same split as v1's
geometry simplifier (`cra.simplifygeom`, not copied): the policy is
unit-testable without a kernel.

**Disqualification is the expected path, not an error** — measured at
roughly three boxes in four on the box-owned subset. A box whose face the
target partially covers comes out of `loadcase._add_standins`' fragment
with that face split; it stops being a hexahedron and cannot be transfinite.
The caller's fallback (mesh size capped at half the thickness, never below
the target size) kills the slab-spanning mechanism for those, unstructured;
boxes thinner than twice the target keep the risk, because meshing them
finer than the struck part was measured at ×21–×91 element-cycles.

**Opposite curves of a transfinite face must agree exactly**, which couples
counts across faces and, through shared curves, across neighbouring boxes.
`plan_grid` resolves the coupling by raising each opposite pair to its
maximum until a full pass changes nothing; counts only increase and are
bounded by the largest initial count, so it terminates.

**The aspect cap is what keeps the grid from re-creating the disease.** A
1.5 mm slab gridded at the 6 mm stand-in size would get 1.5 × 6 × 6 cells
whose tets fall below the 0.3 mm floor the grid exists to protect;
`ASPECT_CAP` forces the in-plane pitch toward the slab's own scale. The
value is a starting point for a mesh-algorithm comparison to judge (v1's
`cra.calibrate.mesh_algo` harness, not copied), not a measured optimum.

**Curve lengths are bounding-box diagonals** — exact for the straight edges
a box has, and free of any OCC dependency, which is what lets the tests use
a plain entity-graph fake. A curved 4-sided face would measure slightly
short and shift a node count by one, which the consistency pass absorbs.

## Assertions

- A volume is structured only if *all* of its declarations are in the plan:
  never a curve or face without its volume, never a volume with a missing
  face.
- Opposite curves of every planned face carry equal node counts.
- Every curve count is at least 2 (one interval), and in-plane pitch never
  exceeds `ASPECT_CAP ×` the owning box's thin extent by more than rounding.
- A face fully shared with the target is legal to structure — the target's
  unstructured mesh conforms to it; a *partially* covered face must
  disqualify instead, because the fragment split it.
- `structure_standins` never meshes and never synchronizes; it only
  declares.
