# postprocess.py

## Function
Stage 4: read a finished run's engine `.out`, write the energy/mass history
as CSV, optionally plot it, convert animation frames to VTK, and flag results
not to trust (energy drift, added mass, early stop). Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- CLI: `postprocess.py RUN_0001.out [--csv PATH] [--plot [PNG]] [--vtk [DIR]] [--json PATH]`.
- Stdout: JSON with `normal_termination, cycles, end_time, timestep, energy, mass, warnings, csv, vtk{directory, frames}`.
- `--vtk` uses `anim_to_vtk_linux64_gf` (PATH or `$OPENRADIOSS_PATH/exec`); default dir `<run>/vtk`.

## Implementation
- The `.out` file is the only output always present and textual, so it is the source of truth for the history.

## Assertions
- [ ] Energy drift beyond 5% produces a warning.
- [ ] Missing `anim_to_vtk` is reported, not fatal.
