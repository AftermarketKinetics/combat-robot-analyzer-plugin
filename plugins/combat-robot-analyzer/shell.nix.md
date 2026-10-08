# shell.nix (plugin)

## Function
The plugin's runtime: gmsh, OpenRadioss, mmg and the sim Python with pytest,
from `nix/sim-env.nix`, pinned by `nix/pkgs.nix`. Entered by `bin/robot-impact`.

## Interface
- Packages: `sim.tools` + `sim.pythonDev`.
- Environment: PYTHONPATH = plugin root, `sim/`, gmsh's Python API; `STEP_OCC_NO_NIX=1`; `CRA_IN_SHELL=1`.

## Implementation
- `toString ./.` puts the plugin's source path, not a store copy, on PYTHONPATH, so edits take effect without a rebuild.
- `STEP_OCC_NO_NIX=1` stops the bundled cad-step scripts re-entering a nix-shell of their own (as in the web app's sandbox image).
- No pip, no lockfile: every sim library comes from nixpkgs.

## Assertions
- [ ] gmsh, OpenRadioss and pythonocc-core are all importable/runnable inside the shell.
- [ ] `CRA_IN_SHELL` is set, or `robot-impact` nests shells.
