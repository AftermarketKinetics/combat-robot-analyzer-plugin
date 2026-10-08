# simgeom.py

## Function

Geometry preparation for the load case (v1: Tier 1): one gmsh session, opaque
part ids. Imports the user's solids, builds the parametric tooth, scopes the
model, tags the physical groups and meshes the result. Driven by
`loadcase.mesh_geometry`, inside `case_cli.py build`.

Copied from v1 (`cra.simgeom`); imports rewritten to `case.`. v2 changes
(2026-10-08 impact-physics rework): `build_tooth` takes an optional
`width_axis` so a spinning weapon's tooth lies across its spin axis, and
`generate_mesh` takes `netgen=` so a graded mesh can skip the Netgen optimiser.
In v2 the tooth built here is the rigid head only (`loadcase.build_case` zeroes
the backing), aimed along the tooth's arc tangent rather than the surface
normal.

It deliberately does **not** use the plugin's `mesh_step.py` (copied beside
this package as `sim/mesh_step.py`).

## Interface

- `gmsh_session(*, verbose=False)` — the single session context manager.
- `import_solids(...)`, `map_solids(...)`, `SolidMap` — user geometry in.
- `build_tooth(gmsh, geom, approach, width_axis=None) -> {"head": [...],
  "back": [...]}` — the impactor. `approach.direction` is the direction the
  tooth's body extends from its striking edge (the tooth travels along
  `-direction`); `width_axis=None` aims as v1 did (one rotation of local +Z
  onto `direction`), otherwise `_orient` maps local +Z onto `direction` and
  local +X (the head's width) onto `width_axis` projected perpendicular to it,
  in one affine transform. `back` is empty when `geom.back_section` is 0, which
  is always the case in v2. `head_solids(...)` builds the head alone.
- `apply_part_groups(...)`, `name_group(...)` — opaque physical-group names.
- `generate_mesh(gmsh, out_path, *, size, min_size=0.0, solid_map=None,
  algo3d=ALGO3D_FRONTAL, optimize_threshold=None, netgen=True)` — the mesh
  itself. `algo3d` / `optimize_threshold` existed for v1's
  `cra.calibrate.mesh_algo`; `build_case` passes its own through, and their
  defaults are what v1 production ran. `optimize_threshold=None` leaves gmsh's
  own 0.3 unset rather than setting it explicitly. `netgen` sets
  `Mesh.OptimizeNetgen`; `loadcase.mesh_geometry` passes `netgen=not grade`,
  so a graded mesh is built without it.
- `ALGO3D` — `{"frontal": 4, "delaunay": 1, "hxt": 10}`, so nothing readable
  has to carry a bare integer. `ALGO3D_FRONTAL` is the default;
  `ALGO3D_DELAUNAY`, `ALGO3D_HXT` name the alternatives.
- `VOLUME_TOLERANCE` (1e-3), `NEGLIGIBLE_VOLUME_MM3` (1e-6) — `map_solids`'
  thresholds (module-level, not in `__all__`).
- `verify(step_path) -> dict`, `main(argv=None) -> int` — the `--verify` CLI
  (`python -m case.simgeom --verify STEP`): prove the ordinal mapping holds
  for one STEP file. `verify` runs `case.cadstep.run_report`, so it needs the
  sim Python with pythonocc. Neither is in `__all__`.
- `GeometryError`.

## Implementation

**Three things must happen in one gmsh session and `mesh_step.py` can do none
of them:** the tooth has to be inserted (it is not in the user's file), the
model has to be scoped (a whole-bot mesh does not fit the budget), and part
identity has to survive.

**Identity was the security one in v1.** `mesh_step.py` names each physical
group from the STEP product name, which is user-authored text — in v1 that
reached `agent/` and the Radioss starter's stderr, breaking v1's PLAN.md §5
"the string never enters the context window". (v2 differs: `case_cli.py
report` hands truncated part names to the agent on purpose.) The names are
also not unique: on `meowtybrain` 415 groups collapse to 306 distinct names,
and `build_deck.load_parts` keys parts by name.

**`gmsh.initialize()` raises `RLIMIT_STACK` to unlimited process-wide and never
restores it**, and glibc reads an unlimited limit as a **2 MB** default pthread
stack where an 8 MB limit gives 8 MB. So Radioss's OpenMP workers got smaller
stacks and died at startup. This was blamed on `-nt 12` for a long time and is
not thread-related: any "mesh in-process, then solve" pipeline hits it at any
thread count. Handled here (v1: `-nt 16` on Fargate then needed no cap).

**The tooth's striking edge sits on the axis it is aimed by.** It did not, once,
and that single defect had five symptoms: contact went from 14% to 93%, solver
failures from 8 to 1, K's 11× spread to 1.93×, and the `000-PBK` outlier back
into the pack. `tooth.py`'s ask — today the `land_width` flat that `build_tooth` reads and
passes to `head_solids` — had been computed and ignored.

**Netgen's optimiser is off for graded meshes.** It segfaulted gmsh in
`generate(3)` on a graded mesh of inertial-v6 (2026-10-08, twice). Without it
the graded mesh is valid and its stable timestep held: 124,753 elements,
stable dt 3.5e-8 s. `Mesh.Optimize` (gmsh's own) stays on either way.

**A spinning weapon's tooth lies across its spin axis** (`width_axis`): a bar
or drum tooth is as wide as the weapon along the axle, so the head's width
(local X) goes on the axis and its body (local +Z) on the approach direction.
`_orient` falls back to `_aim` when the two are parallel. `_orient` imports
numpy lazily.

At 693 lines it exceeds the guidance; every function here shares the one gmsh
session and its global state, which is exactly what must not be split across
files. Not split.

## Assertions

- **Exactly one gmsh session per build**, and it is closed even on failure.
  gmsh's state is global; two sessions corrupt each other.
- **No physical group name derives from user text** (v1: PLAN.md §5).
  `tests/test_simgeom.py`.
- **`map_solids` compares a union against a union**, not a union against a sum —
  it did the latter once, which is on v1's
  `v1/docs/history/STATE-OF-THE-CODEBASE.md` copies-drift list.
- **The `RLIMIT_STACK` handling must not be removed.** `tests/test_simgeom.py`
  covers it; without it every solve after an in-process mesh dies at startup.
- **The shipped tooth construction is measured, not a copy of it.** In v1
  `cra.calibrate.tooth_shapes` measured it and exited non-zero on disagreement
  with what `tooth.py` assumes; that tool was not copied, so in v2 nothing
  checks `build_tooth`'s geometry against `tooth.py`.
