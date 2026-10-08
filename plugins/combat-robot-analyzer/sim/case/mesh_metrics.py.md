# mesh_metrics.py

## Function

Load-case admission arithmetic: how long will this mesh take, and is the
result valid? Two numbers decide whether a job runs — in v1 at gate B4, before
a paid job; in v2 `build_case` computes them and `case_cli.py build` returns
them as `metrics` for the agent and service to judge.

Copied from v1 (`cra.mesh_metrics`); imports rewritten to `case.`.

## Interface

- `analyse(parts, materials, *, end_time, timestep_min, timestep_scale=0.9,
  mesh_size_min=0.0)` — the whole answer. `mesh_size_min` is the floor the
  mesher was told to respect; it is counted against, never priced on, and 0
  skips the count.
- `MeshMetrics` — fields elements, cycles, element_cycles, dt_physical,
  dt_effective, mass_total, mass_added, unpriced_parts, per_part,
  char_p001_mm, char_p01_mm, char_p50_mm, below_mesh_min; properties
  `added_mass_fraction` (mass_added / mass_total) and `mass_scaling_active`;
  `worst_added_mass(limit=3)` ranks the parts for the refusal text.
- `PartMetrics` — part_id, elements, min_char_mm, min_edge_mm, wave_speed,
  mass, added_mass, char_p001_mm, char_p01_mm, char_p50_mm, below_mesh_min,
  and the `dt_physical` property. The four distribution fields default to
  `inf` / `0` so a test fixture can build one by hand.
- `tet_geometry(...)`, `wave_speed(...)`, `dilatational_wave_speed(...)`,
  `poisson_ratio(...)`.
- `K_TET = 3.170831`, `CHAR_QUANTILES = (0.001, 0.01, 0.5)`, `DEFAULT_POISSON`,
  `DEFAULT_TIMESTEP_SCALE = 0.9` — the default for `analyse`'s
  `timestep_scale`.

## Implementation

**The characteristic length is `volume / largest face area`, not the shortest
edge.** That correction is the single most consequential fix in this file's
history. The shortest edge collapses for a sliver while staying finite, so
predicted cost ran a median 2.53× under what runs went on to spend, worst 21.8×
— and the docstring claimed the opposite as a guarantee ("can only
under-predict the timestep, never over-predict it").

**It was identified, not fitted.** The starter prints a `SOLID ELEMENTS TIME
STEP` table naming the element controlling each timestep, and the deck beside it
carries every node, tet and material — so the candidate lengths could be tested
per element. Only `volume / largest face area` holds `dt / L_c` constant within
a part, and it does so exactly.

**The wave speed is dilatational, not the bar speed `sqrt(E/rho)`.** With the
bar speed the constant still moved with Poisson's ratio (2.7699 at 0.29, 2.7329
at 0.30); with the dilatational speed it collapses to one value for every
material. A solid element is laterally constrained and does not ring like a rod.

`dt = scale × (K_TET × V / A_max) / c_dilatational`, verified over 44 cases and
179,669 elements, worst deviation 8.44e-08.

**Pure stdlib, no numpy.** In v1 numpy's manylinux wheel would not load on the
NixOS system python, and the gate that decided whether a paid job ran had to be
unit-testable with no wheels at all. v2's sim Python (`v2/nix/sim-env.nix`)
does carry numpy; the module was copied stdlib and stays so. The mesh comes
straight out of the live gmsh session (`loadcase._tets_by_group`) rather than
via meshio.

**The minimum is kept, and so is the distribution around it.** `dt_physical`
goes with the smallest characteristic length anywhere in the mesh, which means
one element in a hundred thousand can set what a user is charged — and the
minimum on its own cannot say whether that element is an artefact or a part
that genuinely resolves that fine. Measured 2026-09-19 over 34 freshly meshed
cases: median p50 = 1.28 mm against a 1.5 mm request, which is the request
correctly met, since a regular tet's characteristic length is 0.86× its edge;
median p1 = 0.62 mm; median *minimum* = 0.127 mm, and 0.00105 mm is the
smallest element anywhere. 33 of 34 go below the `min_size` gmsh was handed —
documented behaviour, not a defect, since the option clamps the target size
field and the Frontal path never sees it. So the tail is roughly 1 element in
2,000, and because cost is the reciprocal of the minimum it sets the price by
a median 2.4×.

An earlier version of this section said six cases reached 6e-7 mm. It was
measured on meshes retained from 2026-08-21 and was stale before it was
written — `8dfea71` had removed those bounding-box-noise slivers the day
before. The shape of the finding held; the extreme tail did not. What the
tail is actually made of is in `v1/docs/history/mesh-sliver-anatomy.md`.

**Nearest-rank, no interpolation, one sort.** `statistics.quantiles`
interpolates, and a value between two real elements is not an element. The
per-part characteristic lengths are sorted once and reused for the minimum,
the quantiles and the below-floor count; the whole-mesh view is `heapq.merge`
over those already-sorted lists rather than a second sort of the
concatenation. Below 1,000 elements p001 *is* the minimum and carries no
information.

**Added mass is a necessary condition, not a sufficient one.** It is checkable
before the solve; `meowtybrain` lc2 shows what it cannot catch — a hyperelastic
target whose timestep collapses *during* the run, which no property of the
undeformed mesh predicts.

## Assertions

- **`dt` matches the solver's own table** within 8.44e-08 across 44 cases.
  v1's `cra.calibrate.timestep_fit` (not copied to v2) re-derived it, and it
  was the only calibration check needing neither gmsh nor a solver — a deck is
  text and so is the starter's output.
- **`unpriced_parts` being non-empty is a refusal**, never a warning: an
  added-mass fraction over part of the model reads as reassurance. (v1 refused
  at B4; in v2 `build_case` passes it through in `metrics` and the caller must
  refuse.)
- **No numpy import may be added.** (v1: `tests/test_mesh_metrics.py` ran in a
  venv that did not have it.)
- **`mesh_size_min` never reaches the price.** It is counted against and
  nothing else; `dt_physical`, `cycles` and `element_cycles` must be identical
  with and without it. `test_the_floor_does_not_touch_the_price` pins this.
- **`below_mesh_min == 0` is not a clean bill of health** — it is also what a
  caller that passed no floor gets. Read it together with the argument.
- **Unmeasured quantiles are `inf`, never 0.** A zero reads as infinitely fine
  and would pass any threshold put on it.
