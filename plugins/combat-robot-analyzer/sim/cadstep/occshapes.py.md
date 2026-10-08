# occshapes.py

## Function
OpenCASCADE-backed STEP loading and measurement helpers -- the "tier 2"
counterpart to `stepcore`, returning plain Python tuples so both tiers mix.
Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- Must be imported only after `occenv.ensure_occ()` (imports `OCC.Core` at
  module top).
- `Part` -- one located leaf: `name, path, shape, color, entity,
  nauo_chain, world`. `entity`/`nauo_chain` may be `None`/empty.
- `load_parts(path, with_colors=True, with_entities=True)` -> `[Part]`;
  `load_shape(path)` -> one compound.
- Names used by siblings: `load_parts, bbox, bbox_or_none, volume_props,
  surface_area, topology_census, principal_axes, solid_extents,
  solid_volumes, is_closed, is_valid, min_edge_length, mesh`
  (step_report, step_measure, step_clash, step_fasteners, step_drawing,
  step_tessellate; `bbox` is passed to `stepjoin.build_index`).
- Not imported by any `v2/sim/case/*.py` module.

## Implementation
- `entity_resolver` recovers each shape's originating STEP entity id from
  the reader's transfer session; returns `None` when the OCCT build lacks
  that plumbing, and callers must fall back to geometry matching.
- `TDocStd_Document` must be given a plain `str`; a
  `TCollection_ExtendedString` aborts the interpreter (OCCT 7.9).
- `bbox` deliberately ignores existing triangulation (`brepbndlib.Add(...,
  False)`): a shape tessellated earlier (e.g. by a drawing) would otherwise
  report a box off by the chord deflection, making results order-dependent.
- `bbox_or_none` returns `None` for a void box, since `Bnd_Box.Get` raises
  on bodiless construction occurrences.
- XCAF placeholder names (`COMPOUND`, `SOLID`, `=>[0:1:...]`) are not
  treated as real part names.

## Assertions
- [ ] Every name listed under Interface keeps existing with its signature.
- [ ] `bbox` stays independent of whether the shape was meshed earlier.
- [ ] `bbox_or_none` returns `None`, never raises, for a void shape.
