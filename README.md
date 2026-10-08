# accelerate-analyzer

A Claude Code plugin marketplace for combat robot engineering.

| Plugin | What it does |
|---|---|
| [combat-robot-analyzer](plugins/combat-robot-analyzer) | Simulates an opponent's spinning weapon hitting your robot's own STEP CAD with OpenRadioss, and explains the damage. The local version of the Combat Robot Analyzer web app. |
| [cad-step](plugins/cad-step) | Analyses STEP files: assembly trees, placements, holes, mass properties, clashes, fasteners, drawings, diffs. |

## Install in Claude Code

```
/plugin marketplace add ~/code/accelerate-analyzer
/plugin install combat-robot-analyzer@accelerate-analyzer
/plugin install cad-step@accelerate-analyzer
```

Once the repo is on GitHub, the marketplace can also be added by its
`owner/repo` name instead of the local path.

Both plugins need [Nix](https://nixos.org/download): every tool (gmsh,
OpenRadioss, pythonocc, the Python libraries) comes from a pinned nixpkgs. The
first command builds the environment, which takes a few minutes.

## Develop

```
nix-shell                                         # the analyzer plugin's toolchain
plugins/combat-robot-analyzer/bin/robot-impact test
```

Code files in `plugins/combat-robot-analyzer` each have a `<file>.md` doc page
beside them (`.claude/sidecar.json`). `.claude/lessons/` holds the
non-obvious findings behind the meshing and physics choices.
