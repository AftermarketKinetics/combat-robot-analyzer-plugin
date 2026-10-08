# sim-env.nix

## Function
Defines the runtime the `sim/` code needs — gmsh, OpenRadioss, mmg, and a
Python with numpy/meshio/pyyaml/matplotlib/pythonocc-core — once, for both the dev shell and the
sandbox image, so "works in the shell" means "works in the sandbox".

## Interface
- `{ pkgs }: { pythonLibs, python, pythonDev, openradioss, gmshPythonPath, tools }`
  - `python` — the sandbox's Python; `pythonDev` — the same plus pytest, for the dev shell only.
  - `tools` — everything except a Python: gmsh, mmg, OpenRadioss, and bash/coreutils/grep/awk that `run_sim.sh` calls. Each consumer adds the Python it wants.
  - `gmshPythonPath` — directory to add to `PYTHONPATH` so `import gmsh` works.

## Implementation
- gmsh ships `gmsh.py` beside `libgmsh.so`, not as a site-package; callers must set `PYTHONPATH`.
- The sim Python's libraries come from nix rather than a lockfile because the
  image has no package manager; the service's Python is separate (uv).

## Assertions
- [ ] Every import in `sim/*.py` outside the stdlib is provided by `python` here.
- [ ] Every external command `sim/run_sim.sh`, `sim/postprocess.py` and `case/mmgpass.py` (`mmg3d`) call is in `tools`.
- [ ] pytest is never in `python` (it would ship in the image).
