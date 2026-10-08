# case_cli.py

## Function
The sandbox entry point for load-case work: one subcommand per agent tool
(analyse an upload, suggest a scope, resolve the weapon, build a case). The service runs it with
`docker exec`; it turns the `case` package into JSON-in, JSON-out commands.

## Interface
- `case_cli.py report STEP -o DIR [--clash] [--timeout S]` → writes
  `DIR/report.json` (full cad-step report) and `DIR/geometry.json` (per-part
  quantised triangles + edges, for aim/snap and the web viewer); prints
  `{parts: [{id, name (≤80 chars), volume_mm3, bbox, solids}], bbox, units, seconds}`.
- `case_cli.py scope SETUP.json` → prints `suggest_scope`'s
  `{radius_mm, strike_point, target_id, in_radius_ids, standin_ids, scoped_ids}`,
  honouring the setup's `include`/`exclude`. With `end_time` `"auto"` it
  resolves the duration through `loadcase.resolve_end_time` (the estimate
  `build` will use), so the preview shows the same radius as the build.
- `case_cli.py weapon SETUP.json` → prints `impactor.weapon_card` of the
  setup's `impactor` block (every resolved weapon number, `custom`,
  `warnings`); a bad or contradictory override exits 1 with its error.
- `case_cli.py build SETUP.json -o DIR [--case NAME]` → writes
  `DIR/<case>.msh` and `DIR/<case>.yaml` (the `build_deck.py` spec); prints
  `{case, target_id, radius_mm, scoped_ids, mesh, spec, metrics, tooth{summary}, aim{status, gap_mm, message}|null, end_time,
  weapon, impactor, up, stage_seconds, warnings}`. `tooth.summary` is the one string
  the agent gets about the tooth: "rigid S7 tooth head W x T mm, L mm long,
  with a <land> mm striking land". `ToothGeometry.describe()`,
  `impedance_ratio` and the raw geometry no longer leave the sandbox: they
  described v1's backing block. `end_time` is
  the contact time actually used (estimated when the setup said `"auto"`),
  `weapon` is `weapon.Swing.to_dict()` (`hub, axis, omega, radius_mm,
  tip_velocity, v_tip_ms`), `impactor` the `weapon_card` of the spec the case
  was built with, `up` the up axis used, `stage_seconds` the mesh build's
  per-stage timing. `warnings` starts with the spec's own warnings (e.g. tip
  speed over 300 mph), then the build's, then a marginal aim.
- SETUP.json keys: `step, report, geometry, target_id, materials{part→library
  key}, impactor{weight_class, energy_level, archetype, ...any of
  impactor.OVERRIDE_KEYS (null = preset)}, end_time (seconds, or
  "auto"/null to estimate), up_axis ("auto"/absent = thinnest bbox dimension,
  or x|y|z|-x|-y|-z), mesh_size, exclude_span, aim|null, include|null,
  exclude, standins`. Relative paths resolve against SETUP.json's directory.
  The service owns this file.
- Exit 0 with a JSON result; exit 1 with `{"error": "Type: message"}`; killed
  by a signal on a gmsh/OCC crash (no stdout).

## Implementation
- `report` skips `clash` and `drawing` by default: clash was 129 s of 142 s
  on inertial-v6 and no load case reads it. `--clash` opts back in.
- `step_tessellate` names its output after the model, so it writes into
  `DIR/tessellate/` and `report` moves it to `DIR/geometry.json` — a model
  named `report.step` or `geometry.step` cannot overwrite the fixed names.
- The material library is `materials.yaml` beside this file — the same one
  `build_deck.py` resolves material cards from, so a key the agent picks is
  by construction one the deck knows.
- `build` refuses (`{"error": "Refused: ..."}`) before meshing when a placed
  aim grades as blocking (`inside`, `no_intersection`, `beyond_travel`,
  `occluded`) or the target has no surface to grade against, and after
  meshing when `metrics.unpriced_parts` is non-empty. Both were v1 gate checks
  (an unchecked strike missed the material 46 times in 58 across v1's corpus);
  in v2 nothing else runs them. A `marginal` aim is a warning.
- `end_time` `"auto"` (or missing/null) reaches `build_case` as `None`, and
  `scope` resolves it the same way (`loadcase.resolve_end_time`), so scope and
  build agree on duration and radius. The aim grade's reachable travel is
  checked before the estimate exists, so it uses `weapon.END_TIME_CAP_S`, the
  longest the estimate can come out.
- `scope`, `weapon` and `build` all resolve the impactor through
  `impactor.spec_from_setup`, so a user's overrides reach the scope radius,
  the card and the deck alike.
- Every exception becomes `{"error": ...}` so failures reach the agent as data;
  only a hard crash bypasses that, which is what tells the agent to retry
  `build` with `"standins": false`.
- Measured 2026-10-08 on inertial-v6 (this WSL box, 12 cores): report 15 s,
  scope 0.2 s, build 78 s → 360k elements; the solve of that deck took 6 min
  14 s at `-nt 4`. After the impact-physics rework (graded mesh, 12 lb
  horizontal into the 4130 shell): 124,753 elements, and the 286 µs solve took
  4 min 46 s on 6 threads locally (~10 min on mint).

## Assertions
- [ ] Stdout carries exactly one JSON document; nothing else prints to stdout.
- [ ] `build` never meshes a blocking aim and never returns a case with unpriced parts.
- [ ] Part names are truncated before leaving the sandbox (they reach the model's context).
- [ ] No subcommand writes outside its `-o` directory or the SETUP.json directory.
