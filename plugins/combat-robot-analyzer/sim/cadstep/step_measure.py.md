# step_measure.py

## Function
Exact mass properties per part via OpenCASCADE: volume, surface area, centre
of mass, optional inertia, and mass from densities. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_measure.py FILE [--json] [--part PAT]... [--density G_CM3|MAT]
  [--material PAT=MAT]... [--inertia]`.
- `collect(path, ...)` (calls `occenv.ensure_occ()`); `collect_from(loaded,
  parts, density, per_part, inertia, path, id_of)` -- used by step_report's
  `measure` section.
- `MATERIALS` (g/cm3 by name) and `resolve_density(name)` -- imported by
  step_fasteners.
- Output: `path, parts[{part, path, volume_mm3, volume_cm3, area_mm2, com,
  density_g_cm3, mass_g, solids, part_id?, inertia?...}], total_volume_mm3,
  total_mass_g, com, unmeasured`. step_report keys `parts[].part_id`.

## Implementation
- A `--material` rule matching the part's own name beats one matching only
  its assembly path, so a subassembly named "spring-loaded" cannot claim
  every fastener inside it.
- Assembly `com` is mass-weighted when any part has a mass, otherwise
  volume-weighted.
- `resolve_density` accepts a number or a material name; unknown names exit.

## Assertions
- [ ] `MATERIALS` and `resolve_density` keep their names (step_fasteners).
- [ ] `collect_from` adds `part_id` when `id_of` is given.
