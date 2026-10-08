# step_placements.py

## Function
Where every assembly instance sits and which way it points: world origin,
local +Z/+X directions, axis/angle rotation, optionally relative to another
part's frame. Text tier only, no OCC. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_placements.py FILE [--json] [--part PAT]... [--relative-to
  PART] [--leaves]`.
- `collect(path, ...)`; `collect_from(sf, parts, relative_to, leaves_only,
  id_of)` -- used by step_report's `placements` section.
- Output: `path, relative_to, placements[{product, instance, path, depth,
  solids, origin, z_axis, x_axis, rotation_axis, rotation_deg, local_origin,
  part_id?}]`.

## Implementation
- `--relative-to` takes the first node whose product matches and multiplies
  by its inverse world transform.
- `part_id` is what tells apart identical instances that share a name and a
  path.
- An unmatched `--relative-to` exits with a hint to list part ids with
  `step_info.py` (v2: the copy named `step_tree.py`, which v2 does not have).

## Assertions
- [ ] Imports only `_common` and `stepcore` (no OCC).
- [ ] `collect_from` adds `part_id` when `id_of` is given.
