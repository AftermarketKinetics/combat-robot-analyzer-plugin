# loadcase.py

## Function

Copied from v1 (`cra.loadcase`); imports rewritten to `case.`. v2 changes:
the in-process single attempt (no mesh worker, no internal stand-in retry),
`include`/`exclude`, `suggest_scope`, and — from the 2026-10-08 impact-physics
rework — the spinning rigid weapon, the free struck robot, erosion, the
estimated contact time, the scope cap and the graded mesh.

Builds what actually gets solved: the scoped model for one load case — the
struck body's real geometry, the opponent's rigid tooth head, and stand-in
boxes replacing every neighbour — plus the simulation spec the deck is built
from.

The physics (v2), replacing v1's deformable S7 tooth fired along the struck
surface's normal at a clamped target for a fixed 40 µs:

- **The weapon is a rigid tooth head on a spinning rigid body.** No backing
  block. The head's nodes and an extra node `hub` on the spin axis form the
  `/RBODY` "weapon"; the hub carries the opponent robot's whole mass
  (`impactor.opponent_mass_kg`: the class's `weapon.OPPONENT_MASS_KG` unless
  the user overrode it) and the weapon's spin inertia
  minus the meshed head's own (`m_eff·R² − head_mass·R²`), and `/INIVEL/AXIS`
  starts it at `omega = v_tip / R`. `weapon.swing` places the axis so the
  tooth's arc passes through the strike point.
- **The struck robot is free.** v1's fixed "mount" boundary became the
  `/RBODY` "rest_of_robot" over the same far-end nodes, main node `chassis`
  at the centre of mass of every un-meshed part, carrying their mass and
  inertia (`rest_of_robot`).
- **Parts erode** at their material's `eps_max` (`erosion_from_eps_max`,
  which `build_deck.py` turns into `/FAIL/JOHNSON`).
- **Contact time is estimated** when `end_time` is None
  (`weapon.estimate_end_time`), and the scope radius is capped at
  `SCOPE_RADIUS_CAP_MM` (250 mm).
- **The target is meshed graded**: `mesh_size` near the strike, coarser with
  distance.

The whole robot is never simulated. `inertial-v6` meshes to 666,862 elements
whole and dies there: the timestep collapses on slivers in the interference
zones, mass scaling takes over, added mass reaches 100% and the run is killed.
Scoped to the struck panel plus the tooth it is 96,112 elements and terminates
normally at the physical timestep with −0.38% energy drift (v1 physics).

## Interface

- `build_case(step_path, parts, materials, library, *, case, target_id,
  impactor, workdir, end_time, mesh_size, exclude_span, aim=None,
  geometry=None, timestep_scale=0.9, with_standins=True, include=None,
  exclude=(), up_axis=None, graded=True, algo3d=ALGO3D_FRONTAL,
  optimize_threshold=None, structured_standins=False, mmg_pass=False)
  -> BuiltCase` — **one** attempt at tooth placement, scoping, meshing and
  costing, in this process. `end_time=None` estimates the contact time.
  `up_axis` (`None`/`"auto"`/`"x"`/`"-y"`/...) orients the weapon via
  `weapon.parse_up` over the bounding box of every part. `graded` (default on)
  grades the target's mesh. `include` replaces the radius scope with an
  explicit part list; `exclude` drops ids from whichever scope applies; the
  target is always kept. `with_standins=False` meshes the target alone (the
  fallback the agent asks for after a gmsh crash). `structured_standins` grids
  the stand-in boxes transfinite where the fragment left them box-shaped and
  caps the mesh size of the rest at half their thickness, never below
  `mesh_size` (v1's first subset sweep floored the cap at 0.3 mm instead and
  paid ×21–×27 element-cycles on thin plate-sized boxes) — see
  `standin_grid.py`. `mmg_pass` runs the guarded mmg3d post-pass after costing
  (`case.mmgpass`); on acceptance `mesh_path` and `metrics` are the
  replacement. Both default off. A face the weapon cannot reach
  (`weapon.swing`'s `ValueError`) is raised as `LoadCaseError`.
- `suggest_scope(parts, materials, library, *, target_id, impactor, end_time,
  exclude_span, aim=None, geometry=None, include=None, exclude=()) -> dict` —
  what `build_case` will carry, without gmsh: `radius_mm` (capped at
  `SCOPE_RADIUS_CAP_MM`), `strike_point`, `target_id`, `in_radius_ids` (every
  part the wave reaches), `standin_ids` (after adjacency, the cap, and the same
  clip-and-cull `mesh_geometry` runs) and `scoped_ids` (target + stand-ins).
  `end_time` must be a number here: `case_cli.py scope` resolves `"auto"`
  through `resolve_end_time`, so the preview uses the same duration, and so
  the same radius, as the build it previews. On inertial-v6 the
  radius holds 38 parts and 2 get blocks — the preview showed only the 38
  until 2026-10-08, which the agent then had to explain away after the build.
- `BuiltCase` — fields case, target_id, approach, tooth, radius, scoped_ids,
  mesh_path, spec_path, metrics, warnings, `standin_grid`
  (`{"structured": n, "fallback": n}` under `structured_standins`, `None`
  otherwise), `mmg` (the pass's `record()` when it ran, `None` otherwise), and
  v2's `end_time` (the contact time used, estimated or given), `swing`
  (`weapon.Swing`), `up` (the up axis used) and `stage_seconds`
  (`mesh_geometry`'s per-stage timing). `approach` is the surface strike (its
  `direction` is the outward normal); `surround_ids` is a property (scoped_ids
  minus the target), and `describe()` is the one-line summary.
- `deck_spec(*, case, target_id, standin_ids, materials, library, tooth,
  approach, impactor, end_time, swing, rest, timestep_scale=0.9) -> dict` —
  the spec YAML `build_deck.py` turns into a deck: `erosion_from_eps_max:
  true`; parts target, `fill_<id>` stand-ins and `tooth_head` (material
  `tooth_rigid`, an elastic card with the impactor material's density, E and
  ν); `extra_nodes` `hub` (at `swing.hub`) and `chassis` (at `rest["com"]`);
  sets `mount` (`rel_box` from `_clamp_box(approach.direction)`, `with_nodes:
  [chassis]`) and `weapon` (`part: "tooth*"`, `with_nodes: [hub]`);
  `rigid_bodies` `weapon` (main `hub`, opponent mass, `weapon_inertia`) and
  `rest_of_robot` (main `chassis`, `rest` mass and inertia);
  `initial_velocity` `spin` on set `weapon` with `axis {origin, direction,
  omega}`; the two tooth↔target TYPE7 contacts; `control` with `end_time`,
  `animation_dt = end_time/20`, `th_dt = end_time/200`. No
  `boundary_conditions`.
- `resolve_end_time(end_time, impactor, target_card) -> float` — `end_time`
  when given, else `weapon.estimate_end_time(KE, v_tip,
  flow_stress(target_card), head_width × head_thickness)`. The one function
  `build_case` and `case_cli.py scope` share, so scope and build agree. Not in
  `__all__`.
- `rest_of_robot(parts, materials, library, meshed_ids) -> {mass, com,
  inertia, unmeasured}` — every part not in `meshed_ids` as a point mass
  (`volume_mm3 × density`) at its report `com`; `inertia` is
  `[Jxx, Jyy, Jzz, Jxy, Jyz, Jxz]` about the combined centre (the products
  carry the tensor's sign, `−Σ m·rᵢ·rⱼ`). Parts lacking `com`, volume or a
  density are skipped and counted in `unmeasured`; with no mass at all it
  returns zeros. Not in `__all__`.
- `scope_radius(materials, end_time) -> float` — `c_max * end_time` (uncapped;
  the callers apply `SCOPE_RADIUS_CAP_MM`).
- `parts_in_scope(...)`, `body_bbox(...)` — the cut.
- `mesh_geometry(payload) -> dict` — the gmsh half of `build_case`, behind a
  JSON-shaped payload (a v1 process boundary, kept because the shape is
  tested). v2 payload keys `width_axis` (the spin axis, passed to
  `simgeom.build_tooth`) and `grade` (`{centre, r0, growth, far}` or None) are
  optional, so a payload without them meshes as v1 did. Not in `__all__`.
- Constants: `SCOPE_RADIUS_CAP_MM = 250`, `GRADE_R0_MM = 20`,
  `GRADE_GROWTH = 0.15`, `GRADE_FAR_FACTOR = 4`, `SLIVER_GAMMA = 0.02`,
  `MESH_ATTEMPTS = 4`, `STANDIN_REFINE = 0.7`, `CLAMP_FRACTION = 0.2`
  (now the depth of the rest-of-robot rigid body's node set, not a clamp).
- `LoadCaseError`.

## Implementation

**The weapon's energy is the spec's, split between the head and the hub.**
The head is meshed (for its contact shape) and is part of the weapon's rigid
body, so its own mass already contributes `head_mass·R²` of spin inertia. The
hub is given `max(0, m_eff − head_mass)·R²` about the axis
(`head_mass = head_volume × impactor-material density`), so `½·I·ω²` equals
the spec's KE (`impactor.ke_j`); giving the hub the whole `m_eff·R²` came out 10 % high on
inertial-v6. `TestDeckSpec::test_the_weapon_spins_about_its_hub_with_the_presets_energy`
checks it to 2 % against 12 lb horizontal typical's `ke_j` (about 2,000 J
since the 2026-10-08 RPM presets). The hub's translational mass is the whole
opponent robot's (`impactor.opponent_mass_kg`), so the weapon recoils with its
robot.

**The tooth is placed for its travel, not the surface normal.** `build_case`
places the head with `direction = −swing.tip_velocity` (its striking edge
leads along the arc's tangent at the strike point) and its width along the
spin axis (`width_axis`). `approach` itself — strike point, outward normal,
standoff — is kept on `BuiltCase` and is what `_clamp_box` reads to pick the
far end. The standoff is still `min(0.5 mm, 2 % of v_tip × end_time)`.

**The struck robot is free; its far end carries the rest of it.** v1 fixed
the nodes in the far `CLAMP_FRACTION` of the scoped model. v2 makes those same
nodes (`mount`, plus the extra node `chassis`) a rigid body whose main node
sits at the centre of mass of every part that is not meshed and carries their
mass and inertia (`rest_of_robot`, point masses from report `volume_mm3 ×`
material density at the report `com`). Stand-ins are meshed, so they are
excluded from it; `build_case` passes `[target, *standin_ids]` as meshed.

**Contact time and scope.** `end_time=None` → `resolve_end_time` →
`weapon.estimate_end_time(KE, v_tip, flow_stress(target card), head_width ×
head_thickness)`: a stopping
estimate × `HIT_LENGTH_FACTOR` 2, clamped to 40–500 µs. The scope radius is
`min(SCOPE_RADIUS_CAP_MM, c_max × end_time)`: a long hit would otherwise pull
the whole robot into the mesh (how v1's whole-bot runs died); past the cap the
free robot's rigid remainder carries the load.

**Graded mesh: a gmsh `Ball` field, unrestricted.** With `graded`, the
target's volumes get no `Constant` bucket of their own; a `Ball` field centred
on the strike gives `mesh_size` within `r0 = max(GRADE_R0_MM, 2 × head_width)`
and grows linearly at `GRADE_GROWTH` (0.15 mm per mm) to
`GRADE_FAR_FACTOR × mesh_size` (`Thickness = (far − near) / growth`). It is
combined by `Min` with each other volume's `Constant`, so it can only refine
near the strike, where the tooth already is fine. Two alternatives failed: a
`MathEval` formula field computing the same thing segfaulted gmsh on
inertial-v6 (formula fields are not safe under gmsh's multithreaded meshing),
and a `Restrict` wrapper limiting the ball to the target made the build take
>10 min instead of under 3. Netgen's optimiser segfaulted `generate(3)` on
graded meshes, so `mesh_geometry` passes `netgen=not grade`
(`simgeom.generate_mesh`). Measured on inertial-v6 (12 lb horizontal into a
4130 shell): 124,753 elements graded against 828,000 uniform; stable dt
3.5e-8 s; the 286 µs solve took 4 min 46 s on 6 threads locally (~10 min on
mint). `generate_mesh`'s `size` is the largest of the coarse stand-in size,
the backing size and the grade's `far`.

**No sliver reaches the solver.** After `generate_mesh`, `_slivers` counts
tets with gamma under `SLIVER_GAMMA` per physical group, and `mesh_geometry`
re-meshes, refining only what had them: the target's grade `far` halves
(floored at `mesh_size`), a stand-in's size shrinks by `STANDIN_REFINE`. After
`MESH_ATTEMPTS`, or when nothing can be refined further, it raises
`GeometryError` naming the counts. Each retry adds a warning. Why: a graded
far size larger than a thin wall leaves no node inside it, and HXT joins the
wall's faces with flat tets tens of mm long. On inertial-v6's 3.8 mm lip
(far 6 mm, a 12 lb disc on the user's live session, 2026-10-08) there were
203 with gamma 0.0003-0.001, 45-60 mm from the strike; they eroded at almost
no load, their neighbours inverted, and the solve went NaN at 8.5 µs. Neither
gmsh's default optimiser nor Netgen run after meshing removed them (no node
inside the wall to move). With the retry the same case meshes clean at
160k elements (far 3 mm), 2.8x the elements, same stable dt.

**`MIN_SIZE_FRACTION` exists because two functions need the same number.**
`mesh_geometry` hands gmsh `mesh_size / MIN_SIZE_FRACTION` as
`Mesh.MeshSizeMin`; `build_case` hands the same value to `mesh_metrics.analyse`
so `below_mesh_min` counts against the floor gmsh was actually given. Written
as a constant rather than twice as `/ 5.0` — a number that means "the floor"
in one function and "a fifth" in another is how the two drift apart. It is a
diagnostic only: 52 of the 54 retained scoped meshes come out with at least
one element below it, and none of them is priced any differently for it.

**Stand-in boxes are snapped to a micron grid, and that is a timestep fix not a
tidiness one.** cad-step report bounding boxes carry CAD-kernel noise: one corpus model
reports a part spanning x = ±146.75001083 beside a neighbour at exactly
±146.75. `fragment` faithfully turns that 10.7 nanometre overhang into a 10.7
nanometre sliver, and the stable timestep goes with the shortest edge in the
*whole model* — so an invisible artefact of a bounding box priced a real job at
79 million cycles and 3,699× its budget, naming `fill_p011` as the driver.

Measured rather than assumed: two boxes fragmented with that exact overhang mesh
to a 1.073e-05 mm shortest edge; with their faces exactly coincident, 1.468 mm —
the same element count either way, so the cost is entirely in the timestep.

`STANDIN_SNAP_MM` is 1e-3. A micron is far below anything this construction
claims to resolve — a stand-in only has to be the right material in roughly the
right place — and far above the noise. Snapping also collapses a box thinner
than the grid to zero, so the existing `min(size) <= 0` check drops it instead
of meshing the very sliver this exists to prevent.

**Snapping fixes box against box. The fragment is fuzzy because box against
real CAD is a different problem.** The grid puts two boxes on common corners,
but the target is the customer's geometry and is not on any grid: a snapped box
face lands up to half a micron either side of where the CAD actually is, and
about half the time that is *inside* it. An exact boolean then makes a body out
of the lens between them — and by the earliest-input rule below, that body is
handed to the *target*, where it becomes the shortest edge in the model and
sets the timestep for the whole job.

Measured on a cylinder snapped 0.4 µm inside its own bounding box:

| boolean | target | target volume | stand-in |
|---|---|---|---|
| exact | **2 bodies** | 3359.694190 | 701.947204 |
| `ToleranceBoolean` 1e-2 | 1 body | 3359.694190 | 701.952000 |

The target's volume is identical to twelve digits; the 4.8e-3 mm³ of difference
lands on the stand-in, which is the body that can afford it. `STANDIN_FUZZ_MM`
is 1e-2 — ten times the snap grid, and the gap is not slack: 1e-3 and 1e-4 both
left the lens intact. The snap decides where a box *is*; the fuzz decides how
close two faces must be before OCC stops believing the difference.

`gmsh.option` is global and the session outlives this one boolean, so the
tolerance is set and restored around the `fragment` call in a `finally`.

**`_add_standins` checks the target's volume survived.** A fuzzy boolean is OCC
being told to disbelieve small differences, and the one thing it must never
round away is the part the report is about. The caller already measures
`target_volume` before laying the boxes out; `MAX_TARGET_VOLUME_DRIFT` (1e-3)
is what that measurement may move across the fragment. Exceeding it raises
`LoadCaseError`, which `mesh_geometry`'s `except Exception` around
`_add_standins` turns into "no stand-ins" plus a warning rather than a lost
job. The warning says the panel's far end is then tied straight to the rest
of the robot's rigid mass and mount loads are not reportable; the same wording
is used when no neighbour was close enough to stand in.


**The cut is made at a physically derived radius**, not a chosen one. Whatever
is deleted must be replaced by a boundary condition, and that boundary is a lie
unless the stress wave never arrives at it — so the model keeps every part with
geometry within `c_max * end_time` of the strike point.

**That ties scope to `end_time`, which is the consequential coupling.** They are
the same decision: a long `end_time` pulls the whole bot back into the model,
which is the configuration that failed in v1's Phase 5. v2 breaks the coupling
at `SCOPE_RADIUS_CAP_MM` (250 mm): with the struck robot free, what lies beyond
the cap is carried by the rest-of-robot rigid body rather than by a clamp the
wave must not reach. The module docstring still describes v1's clamp.

**A user's aim re-decides which body is struck.** `choose_approach` runs
first whatever happens, because `exposure`, `reach` and `arbitrary` describe
how the part sits in the assembly and are not things a builder supplies.
`solid_index` used to be inherited from it on the same reasoning — and that
was wrong. The scorer ranks bodies by how exposed they are and never sees the
aim, so a builder who moves the strike onto a different body of the same part
gets a model of the body the scorer liked.

Job f1ca7b48 (inertial-v6) is what this cost. p035 is two bodies: a
400×422×73 mm shell and a 54×58×58 mm boss. The scorer chose the shell and
put its prefill at y = +347; the builder moved the strike to the boss at
y = −76. Only the shell was meshed, and the tooth was fired at a point
1.46 mm outside it, into the space the boss occupies. v1's aim gate graded
against the part's *whole* tessellated surface, so it passed; the first sign
was the budget gate refusing "no usable density or wave speed for p035" — about a part
whose material the builder had chosen by hand, and whose `min_edge_mm` and
volume were both fine. `_body_for_point` now resolves the body from the point
the builder actually placed, by the same `solid_bboxes` that `_struck_solid`
verifies the mesh against.

`solid_bboxes` may carry a `None` where the cad-step report could not box a body. The
entry is held open so the indices keep naming the same solids, so every
consumer here skips the gap without closing it: `_body_for_point` ranks only
the boxed bodies and returns their real indices, `body_bbox` falls back to the
part box, and `_struck_solid` skips its drift check rather than comparing
against nothing.

**An unconfirmed aim is snapped onto real material** before meshing (v1:
decision 47), silently, with the distance recorded. The disagreement is
sub-millimetre and nobody can act on it.

**No in-process retry, and no mesh worker.** v1 ran `mesh_geometry` in a
child process and retried without stand-ins when it failed, because gmsh
segfaults on some real robot CAD. In v2 every `case_cli.py build` is its own
sandbox exec: a crash ends that exec, and the agent retries with
`"standins": false` and tells the user mount loads are lost.

**Scoping is a suggestion.** The radius rule below is what `suggest_scope`
proposes and what `build_case` uses when `include` is `None`; the user may
override it, and the agent is responsible for saying what an override costs.

**At ~1,490 lines this is the largest module in v2.** It is one unit: the
scope radius, the stand-in boxes, the tooth insertion and the spec all depend
on the same derived geometry. v1 considered splitting it and declined; the v2
rework put the weapon's geometry in its own `weapon.py` instead of growing this.

## Assertions

- **With no `include`, every part within the wave-distance radius is carried**
  (as a stand-in); with an `include` or `exclude`, exactly the user's set is
  carried and the target always is. Changed deliberately on 2026-10-08 from
  v1's "every part within the radius survives" — scoping is now a suggestion.
  `tests/test_loadcase.py`.
- **The spec and the mesh describe the same model.** `build_deck.py` must deck
  exactly what `build_case` costed.
- **The weapon's spin energy equals the spec's KE** (hub inertia plus the
  head's own at the arc radius). `::TestDeckSpec::test_the_weapon_spins_about_its_hub_with_the_presets_energy`.
- **The weapon hub carries the opponent's mass (`impactor.opponent_mass_kg`,
  the class mass by default); the tooth is a rigid head with no backing
  block.** Changed deliberately on 2026-10-08 from "the class mass"
  (`OPPONENT_MASS_KG[class]`), so a user-overridden opponent mass reaches the
  deck. `::test_the_weapon_hub_carries_the_opponents_mass`,
  `::test_the_tooth_is_rigid_and_has_no_backing_block`.
- **The struck robot is free**: no `boundary_conditions` in the spec; the far
  end is the `rest_of_robot` rigid body over `mount` + `chassis`, chosen over
  the whole model, not sawn through the target.
  `::test_the_robot_is_free_not_clamped`,
  `::test_the_rigid_remainder_is_over_the_whole_model_not_the_target`.
- **Every spec asks for erosion** (`erosion_from_eps_max: true`).
  `::test_parts_erode_at_their_failure_strain`.
- **The scope radius never exceeds `SCOPE_RADIUS_CAP_MM`**, in `build_case`
  and `suggest_scope` alike.
- **A user-placed aim decides which body of a multi-solid part is struck**,
  not `choose_approach`'s exposure score.
  `tests/test_loadcase.py::TestTheAimPicksTheBody`.
- **`solid_index` and `part_id` survive `_rebuild_approach`.** Both have been
  dropped before (v1). `tests/test_loadcase.py::test_the_mesh_payload_carries_the_struck_body_across_the_process_boundary`.
- **The struck part's own volume survives the stand-in fragment**, within
  `MAX_TARGET_VOLUME_DRIFT`. `tests/test_loadcase.py::TestTargetVolumeSurvives`.
- **`Geometry.ToleranceBoolean` is restored after the fragment**, on the
  raising path too. `::TestStandinFuzz::test_the_tolerance_is_put_back_when_the_fragment_raises`.
- **No mesh with a sliver (gamma < `SLIVER_GAMMA`) is written as the build's
  result**: `mesh_geometry` re-meshes or raises. Added 2026-10-08 after a live
  solve went NaN on slivers. `tests/test_loadcase.py::TestSlivers` covers the
  detector; the retry is measured, not unit-tested (it needs a real CAD mesh).
