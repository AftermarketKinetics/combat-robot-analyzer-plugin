# occenv.py

## Function
Makes pythonocc-core (OpenCASCADE) importable for the scripts that need exact
B-rep geometry, re-execing the current process under `nix-shell` when
`OCC` is missing. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- `ensure_occ(extra_packages=())` -- returns if `import OCC` works;
  otherwise replaces the process (`os.execve`/`os.execvpe`) and never returns.
- `have_occ()` -- bool.
- Env: `STEP_OCC_PYTHON` (interpreter with OCC to re-exec into),
  `STEP_OCC_NO_NIX=1` (never re-exec; exit 3 with help text),
  `STEP_OCC_REEXEC` (internal guard; set on the re-exec'd child).
- CLI: `occenv.py` prints the pythonocc version and interpreter.
- Called first by `collect`/`run` in step_clash, step_drawing,
  step_fasteners, step_measure, step_tessellate, and by `step_report.collect`.

## Implementation
- Fallback order: OCC importable -> `STEP_OCC_PYTHON` -> `nix-shell -p
  "python3.withPackages(ps: with ps; [pythonocc-core numpy ...])" --run ...`
  -> exit 3. The nix-shell expression is an unpinned `-p` against the
  caller's channel, not this repo's pinned `shell.nix`.
- If the guard is already set and OCC is still missing, exits 3 instead of
  looping.
- In v2, pythonocc-core is in the sim Python (dev shell and sandbox image),
  so `ensure_occ()` returns immediately; the nix-shell branch is dead there.
  If it ever fires inside the sandbox it would need `nix-shell` and network.

## Assertions
- [ ] `ensure_occ()` is a no-op whenever `import OCC` succeeds.
- [ ] The re-exec guard prevents a second re-exec (exit 3, not a loop).
- [ ] Nothing is written to stdout before a re-exec; status goes to stderr.
