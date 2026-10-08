# robot-impact

## Function
The plugin's single entry point. Enters the plugin's nix-shell and runs
`python3 -m cra`, so Claude needs neither a working directory nor a shell of
its own. `bin/` is on the Bash tool's PATH while the plugin is active.

## Interface
- `robot-impact <cra subcommand> ...` — see `cra/__main__.py.md`.
- `robot-impact test [pytest args]` — runs `sim/tests` and `tests`.
- `robot-impact shell` — an interactive nix-shell.
- No arguments or `-h` prints the header comment.

## Implementation
- Arguments are quoted with `printf %q` through `nix-shell --run`, so paths with spaces and JSON survive.
- Inside the shell (`CRA_IN_SHELL=1`, set by `shell.nix`) it runs commands directly instead of nesting another nix-shell.
- PYTHONPATH comes from `shell.nix`, not from here: setting it here once dropped gmsh's Python API, because this script's own environment is outside the shell.

## Assertions
- [ ] Never sets PYTHONPATH itself.
- [ ] Resolves its own location through symlinks (`readlink -f`).
