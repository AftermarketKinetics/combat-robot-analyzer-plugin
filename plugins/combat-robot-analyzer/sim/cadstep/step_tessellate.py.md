# step_tessellate.py

## Function
Per-part triangles and discretised B-rep edges for a viewer, written to disk
as one compact JSON document; returns only a manifest. Feeds the browser view
and impact aiming. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_tessellate.py FILE -o DIR [--max-parts N=0] [--names-out
  PATH]` -- own argparse, no `--json`; always prints the manifest as JSON.
- `collect(path, out_dir, max_parts, deadline, names_out)` (calls
  `occenv.ensure_occ()`, builds its own `stepjoin` index);
  `collect_from(loaded, out_dir, name, source, id_of, max_parts, deadline,
  names_out)` -- used by step_report's `tessellate` section.
- Writes `<out_dir>/<name>.json` (name = STEP file stem): `model, source, lo,
  span, deflection_mm, part_count_total, unmeasured, parts[{id, volume_mm3,
  bbox, color, nv, verts, tris, ns, segs}]`. `case_cli.py report` renames it
  to `geometry.json`.
- Manifest: `file, names_file, bytes, deflection_mm, angular, lo, span,
  parts_total, parts_tessellated, parts_dropped_by_max, triangles,
  unmeasured, incomplete, seconds, parts[{id, nv, ntris, ns}]`.

## Implementation
- `verts`/`segs`: base64 uint16 positions quantised over the *model* bbox
  (shared frame; ~8 um on a 500 mm bot), stored as all x, then all y, then all
  z. `tris`: base64 little-endian uint32 indices.
- Deflection = model bbox diagonal x `DEFLECTION_RATIO` (1/600); `ANGULAR`
  0.6 rad. Vertices dedup at 1 um (`VERTEX_QUANTUM`).
- Real B-rep edges, not triangle outlines, for the wireframe.
- Opaque ids only, no names: the document goes to a browser. Names go to a
  separate `names_out` file only when asked.
- `max_parts=0` keeps every part; a dropped part cannot be tagged or aimed.
- `triangles`/`ntris` come from `_ntris`, on the *decoded* index bytes (12
  per triangle). v2 fix: the copy counted the base64 text, ~4/3 too high.
- Passes `occ_bbox=occshapes.bbox_or_none` to `stepjoin.build_index` (v2 fix).

## Assertions
- [ ] No part names or paths in the geometry document.
- [ ] Geometry goes to disk; the returned manifest stays small.
- [ ] A part whose volume fails is listed in `unmeasured` by id, not dropped
      silently.
