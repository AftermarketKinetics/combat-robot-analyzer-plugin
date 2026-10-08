# mesh_step.py

## Function
Stage 1 of a solve: mesh a STEP (or IGES/BREP) file with gmsh into a `.msh`,
one physical group per solid so parts stay separable for per-part materials
and contacts. Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- CLI: `mesh_step.py STEP [-o OUT.msh] -s SIZE [-e tet|quad|tri] [--heal] [--optimize] [--algo3d …] [--algo2d …] [--list-faces] [--json PATH] [-v]`.
- Stdout: JSON summary (node/element counts, per-part groups, quality).
- Imports `gmsh` (needs `PYTHONPATH` from `nix/sim-env.nix`).

## Implementation
- `gmsh.initialize()` raises `RLIMIT_STACK` process-wide; the solver must not run in this process (see `openradioss.nix.md`).

## Assertions
- [ ] Each solid in the input is its own physical group.
- [ ] Stdout is JSON only; logs go to stderr.
