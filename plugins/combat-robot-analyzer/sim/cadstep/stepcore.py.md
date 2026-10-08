# stepcore.py

## Function
Dependency-free reader for ISO 10303-21 STEP files -- the "text tier".
Answers structural questions (assembly tree, placements, surfaces, colours,
units) without OpenCASCADE. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- `load(path)` -> `StepFile`. Used by step_info, step_features,
  step_placements, step_report, step_tessellate, step_drawing, and
  `v2/sim/case/cadstep.py` (`load_step`).
- `StepFile` members used by siblings: `path, schema, header, entities,
  count, type_census, units(), solids(), assembly(), colors(), products(),
  world_bounds(), face_vertices, solid_bounds, shape_reps`, and the private
  `_cache` dict (stepfeatures memoises on it). Many methods are attached
  after the class body (`StepFile.solids = _solids`, ...).
- `Vec` (`from stepcore import Vec` in stepfeatures; `stepcore.Vec` in
  step_features/step_placements), `AssemblyNode` (`walk, path(), product,
  instance, pd_id, nauo_chain, world, local, solids, children, depth`),
  `Transform` (`apply, apply_dir, inverse, axis_angle, t`, `*`).
- CLI: `stepcore.py FILE` prints entity count, parse time, schema, units,
  and the top 10 entity types (text only).

## Implementation
- Quoted strings and `/* comments */` are masked in one pass before record
  splitting, so `;` or `#` inside a string cannot confuse the parser.
- Entity parameters parse lazily; loading only splits records and indexes
  types.
- Complex instances (`#68=( A(...) B(...) )`) parse eagerly and are indexed
  under every subtype, because assembly transforms and unit contexts live in
  them.
- `units()` reads `GLOBAL_UNIT_ASSIGNED_CONTEXT` rather than the first
  `SI_UNIT`; with none found it reports millimetre with `assumed: True`.
- `AssemblyNode.pd_id` repeats per product; `nauo_chain` is unique per
  occurrence.
- 1210 lines, not split: it is reused as-is.

## Assertions
- [ ] No third-party imports (stdlib only); `v2/sim/case` loads it without OCC.
- [ ] `StepFile._cache` exists from construction (stepfeatures writes to it).
- [ ] `load`, `StepFile`, `Vec`, `AssemblyNode` keep their names.
