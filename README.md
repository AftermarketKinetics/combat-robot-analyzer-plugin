# accelerate-analyzer

A Claude Code plugin marketplace for combat robot engineering.

| Plugin | What it does |
|---|---|
| [combat-robot-analyzer](plugins/combat-robot-analyzer) | Simulates an opponent's spinning weapon hitting your robot's own STEP CAD with OpenRadioss, and explains the damage. The local version of the Combat Robot Analyzer web app. |
| [cad-step](plugins/cad-step) | Analyses STEP files: assembly trees, placements, holes, mass properties, clashes, fasteners, drawings, diffs. |

## Install in Claude Code

```
/plugin marketplace add AftermarketKinetics/combat-robot-analyzer-plugin
/plugin install combat-robot-analyzer@accelerate-analyzer
/plugin install cad-step@accelerate-analyzer
```

To work from a local clone instead, pass its path to `marketplace add`
(e.g. `/plugin marketplace add ~/code/accelerate-analyzer`); the install
commands stay the same, since `@accelerate-analyzer` is the marketplace's
`name` in `.claude-plugin/marketplace.json`, not the repo name.

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
