# step_features.py

## Function
CLI and collector over `stepfeatures`: hole tables, bolt circles, plane
stacks and (for meshing) blends, per solid, optionally in assembly
coordinates. Text tier only, no OCC. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_features.py FILE [--json] [--part PAT]... [--world] [--bosses]
  [--min-dia MM] [--max-dia MM] [--planes] [--min-circle N=3]`.
- `collect(path, ...)`; `collect_from(sf, parts, world, bosses, min_dia,
  max_dia, planes, min_circle, id_of, blends, deadline)` -- step_report
  calls it with `world=True, planes=True, blends=True`.
- Output: `path, frame, holes[], bolt_circles[], plane_stacks[],
  parts_skipped`, plus `blends[]` when asked and `solids_reached,
  solids_total, incomplete` when a deadline is given. Records get
  `part_id` when `id_of` is given.

## Implementation
- `blends` is off by default and the CLI never sets it, so the tool's own
  output is unchanged; a mesh-sizing caller wants it because a fillet drives
  the timestep like a hole does.
- The deadline is checked between solids (`C.expired`).
- `--world` transforms `center, start, end` and `axis` by the owning
  node's world transform.

## Assertions
- [ ] No OCC import.
- [ ] `incomplete`/`solids_reached` present exactly when a deadline is
      passed (step_report's `timestep_drivers_error` reads them).
