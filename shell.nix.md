# shell.nix (repo root)

## Function
The repo's dev shell: simply the combat-robot-analyzer plugin's runtime, so
`nix-shell` at the root gives gmsh, OpenRadioss and the sim Python with pytest.

## Interface
- `import ./plugins/combat-robot-analyzer/shell.nix { }`.

## Implementation
- One source of truth for the toolchain: the plugin's shell. The cad-step plugin re-enters nix itself when its scripts need pythonocc.

## Assertions
- [ ] Stays a plain import of the plugin's shell.
