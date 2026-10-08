# run_sim.sh

## Function
Stage 3: run a deck — starter, then engine — and refuse to run the engine on a
deck the starter rejected. `--check` is the cheap validity test. Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- `run_sim.sh STARTER_0000.rad [-nt N] [-np N] [--check]`.
- On success prints the engine `.out` path on stdout; progress on stderr.
- Exit 1 on starter errors (printed), engine crash (signal named), or abnormal termination.
- Finds the solver on PATH, else `$OPENRADIOSS_PATH/exec`.

## Implementation
- Runs from the deck's directory: the solver derives every output name from the deck basename.
- Sets `OMP_STACKSIZE=400m` unless the caller set it.

## Assertions
- [ ] The engine never runs when the starter reported errors.
- [ ] A signal-killed engine is reported as a crash, not as a bad deck.
