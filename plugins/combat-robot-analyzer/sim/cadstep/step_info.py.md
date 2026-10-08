# step_info.py

## Function
Summarises a STEP file from the text tier: provenance header, units,
assembly structure, entity/geometry counts, colours. Run first on an
unfamiliar file. No OpenCASCADE. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_info.py FILE [--json] [--census N=15]`.
- `collect(path, census_n=15)`; `collect_from(sf, census_n=15)` -- used by
  step_report's `info` section.
- Output keys: `path, size_bytes, schema, description, name, timestamp,
  author, organization, preprocessor, originating_system, units{length,
  mm_per_unit, angle, tolerance, assumed}, entities, structure{products,
  instances, solids, roots, max_depth, solid_bearing_nodes, is_assembly},
  geometry{faces, edges, vertices, surfaces, bounds}, appearance, census`.

## Implementation
- `is_assembly` is "more than one solid-bearing node or depth > 1", not
  "has a NAUO": exporters wrap even a single body in a one-instance assembly.
- `geometry.bounds` is vertex-based and approximate (labelled so in
  `method`).
- The text output's "next tools" hint lists only scripts present in v2
  (v2: dropped the `step_tree.py` mention).

## Assertions
- [ ] Imports only `_common` and `stepcore` (no OCC).
- [ ] `collect_from` keeps accepting an already-parsed `StepFile`.
