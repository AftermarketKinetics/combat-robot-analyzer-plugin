# step_report.py

## Function
Every collector's output for one STEP file from a single `stepcore` parse and
one OpenCASCADE transfer, with every occurrence keyed by an opaque part id.
The only cad-step entry point v2 runs. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_report.py FILE [--json] [--skip a,b] [--min-dia MM]
  [--driver-max MM=1.0] [--max-seconds S] [--validate] [--view NAME]...
  [--hidden [full|outline]] [--clearance MM] [--max-pairs N=20000]
  [--tessellate-dir DIR] [--max-tessellated N=0]`.
- Sections (`SECTIONS`): `info, measure, features, placements, clash,
  fasteners, drawing, tessellate`. An unknown `--skip` name exits.
- Called by `v2/sim/case/cadstep.py:run_report` as a subprocess with
  `--json`; `case_cli.py report` skips `clash,drawing` (`drawing` only
  with `--clash`), `case/simgeom.py` also skips features/fasteners/placements.
- Stdout (`--json`): `path, size_bytes, seconds, schema, units{length,
  angle, assumed}, parts[], parts_seconds, bbox, timestep_drivers,
  timestep_drivers_max_mm, timestep_drivers_error, unjoined_parts,
  join_methods, sections{name: {seconds, data | error}}`.
- `parts[]` row: `id, name, path, product_definition_id, nauo_chain,
  occurrence, joined, volume_mm3, area_mm2, com, bbox, solids, solid_bboxes,
  solid_volumes, closed, color, min_edge_mm, has_cylindrical_bore,
  max_bore_dia_mm, min_feature_mm` (+ `valid` with `--validate`). v2 reads
  `id, name, volume_mm3, bbox, solids, solid_bboxes, solid_volumes`.
- Python: `collect(...)`, `part_rows`, `feature_rollups`, `timestep_drivers`.

## Implementation
- Each section runs in `_section`, which turns any exception (including
  `SystemExit`) into `{"error": ...}`, so one bad section costs only itself.
  Skipped sections report `error: "skipped"`; past `--max-seconds`,
  `"skipped: time budget exhausted"`.
- clash and drawing run last because they cost more than everything else.
- tessellate reuses the loaded shapes; without `--tessellate-dir` it errors
  out of its section rather than returning megabytes inline.
- `parts` rows reuse measure's mass properties instead of recomputing; parts
  the OCC tier never saw keep null measured fields rather than being dropped.
- `solid_bboxes`/`solid_volumes` only when a part has more than one solid,
  index-aligned, with `None` per unboxable body.
- `bbox` is the union of exact per-part OCC boxes (step_info's is
  vertex-based and understates curved parts).
- `timestep_drivers` groups identical-size features per part, smallest first,
  up to `--driver-max`; `--min-dia` hides small holes from it too.
- Loading, `stepjoin.build_index`, and `part_rows` run outside `_section`;
  a failure there fails the whole run.

## Assertions
- [ ] With `--json`, stdout is exactly one JSON document (parsed whole by
      `case/cadstep.py`); diagnostics go to stderr.
- [ ] Section failure never fails the document; every name in `SECTIONS`
      appears in `sections`.
- [ ] `parts[].id` comes from `stepjoin` and every section keys by it.
- [ ] `--skip`, `--tessellate-dir`, `--max-tessellated`, `--min-dia`,
      `--hidden` keep their names (`run_report` passes them).
