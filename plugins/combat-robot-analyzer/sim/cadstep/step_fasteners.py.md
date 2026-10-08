# step_fasteners.py

## Function
Fastener bill of materials: classifies leaf parts by name (STEP records no
threads), groups identical fasteners, measures each with OCC, and reports
count, mass and layout about a spin axis. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_fasteners.py FILE [--json] [--part PAT]... [--pattern PAT]...
  [--hardware] [--density G_CM3|MAT] [--material PAT=MAT]... [--axis x|y|z]
  [--positions]`.
- `collect(path, ...)` (calls `occenv.ensure_occ()`, loads without
  colours); `collect_from(loaded, ..., path, id_of)` -- used by
  step_report's `fasteners` section. `classify(name, extra_patterns,
  hardware)`, `infer_material(name)`.
- Imports `MATERIALS`, `resolve_density` from step_measure.
- Output: `path, axis, hardware, groups[{designation, kind, count,
  volume_mm3, material, density_g_cm3, density_source, mass_g, mass_each_g,
  example_name, layout, positions, part_ids?}], total_count, total_mass_g,
  ignored_parts`.

## Implementation
- `CATEGORIES` order matters: "Nylon-Insert Locknut" must land on `nut`
  not `insert`; screw keywords are checked last.
- A bare `M10` counts as a size only when it is essentially the whole name;
  a keyword buried mid-name is rejected unless a thread size rescues it and
  the name does not end on a structural noun (`STRUCT_TAIL`).
- Density per group: a matching `--material` rule > `--density` > guessed
  from the first name > steel default (`density_source` says which). Note a
  `--material` rule overrides `--density` despite its "forced on every group"
  help text.
- `main` resolves `--density` before `collect` to fail fast, before any
  nix-shell re-exec.

## Assertions
- [ ] Unrecognised parts are counted in `ignored_parts`, never guessed into
      a group.
- [ ] `collect_from` adds `part_ids` per group when `id_of` is given.
