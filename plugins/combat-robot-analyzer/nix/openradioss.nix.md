# openradioss.nix

## Function
Packages the upstream prebuilt OpenRadioss linux64 release (starter, engine,
anim_to_vtk) for nix. Copied unchanged from the openradioss-sim plugin
(`~/code/impact-simulator-skill`, commit `f8e97bd`).

## Interface
- `callPackage`-able derivation; `$out/bin/` has wrapped `starter_linux64_gf`,
  `engine_linux64_gf`, `anim_to_vtk_linux64_gf` and the other `exec/` binaries.
- Wrappers set defaults for `OMP_STACKSIZE`, `OPENRADIOSS_PATH`, `RAD_CFG_PATH`, `RAD_H3D_PATH`.

## Implementation
- Prebuilt tarball + autoPatchelf instead of a ~1 h source build.
- The tree stays intact under `$out/share/OpenRadioss` because the solver resolves `hm_cfg_files` relative to `OPENRADIOSS_PATH`.
- `OMP_STACKSIZE=400m`: an unlimited `RLIMIT_STACK` (gmsh sets one) makes glibc pick 2 MB thread stacks and segfaults the engine.

## Assertions
- [ ] `OMP_STACKSIZE` default is set on every wrapper.
- [ ] Fixes made here are also offered upstream to the plugin (this is a copy, not a submodule).
