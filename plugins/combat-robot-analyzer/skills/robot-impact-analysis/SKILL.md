---
name: robot-impact-analysis
description: Find out what an opponent's spinning weapon does to a combat robot, by simulating the hit on the robot's own STEP CAD with OpenRadioss. Use when someone asks whether their armour, wedge, chassis or other part survives a hit from a horizontal spinner, vertical spinner or drum, where it would break, or how two designs compare under a hit. Covers model analysis, materials, the opponent weapon (presets or custom), meshing, solving and explaining the damage.
---

# Combat robot impact analysis

This runs the Combat Robot Analyzer pipeline locally. You do the job the web
app's assistant does: settle the setup with the user, build, solve, and
explain the damage.

```
STEP → analyse parts → setup (target, materials, weapon) → build (mesh + deck) → solve → results
```

**Requires Nix.** Every command goes through one wrapper, which enters the
plugin's nix-shell (gmsh, OpenRadioss, the sim Python). Never call `python3`
yourself.

```bash
${CLAUDE_PLUGIN_ROOT}/bin/robot-impact <command> ...
```

`bin/` is on the Bash tool's PATH while the plugin is active, so plain
`robot-impact` usually works. The first run builds the environment, which
takes a few minutes; later runs start in seconds. Each command prints one JSON
document. Failures print `{"error": ...}` and exit 1; report that message to
the user as is.

| Command | Does |
|---|---|
| `init STEP CASE` | copy the model into a new case directory and list its parts |
| `show CASE` | the setup, what is still missing, the resolved weapon |
| `set CASE 'JSON'` | change the setup; only the given keys change ([reference/setup.md](reference/setup.md)) |
| `materials [QUERY]` | material keys, density, yield, failure strain |
| `scope CASE` | which neighbouring parts a build carries |
| `build CASE` | mesh, write the deck, check it with the solver's starter, estimate solve time |
| `solve CASE [-nt N] [--allow-over-budget]` | run the solver |
| `results CASE` | energies, damage per part, wear map ([reference/results.md](reference/results.md)) |
| `test` | the plugin's own tests |

Put CASE in the user's working directory, one directory per robot and setup,
for example `./impact/<robot>-<weapon>`. It holds everything the case produces.

## Working a request

1. **Analyse the model.** Run `init` and tell the user briefly what the model
   contains: the main parts and its overall size. Part ids look like `p035`.
2. **Settle the setup with the user.** Three things, set with `set`:
   - **Which part gets hit** (`target_id`).
   - **What each part is made of** (`materials`, with `default_material` for
     the rest). Ask rather than guess when it changes the answer, especially
     the struck part's material. Part names are often a good hint; say when
     you are inferring.
   - **What hits it** (`impactor`): opponent weight class (1lb, 3lb, 12lb or
     30lb), typical or high spin, and horizontal spinner or vertical/drum. Any
     weapon number can be customised; see [reference/setup.md](reference/setup.md).
   `set` returns the resolved weapon. Tell the user its size, RPM, tip speed
   and energy, and which numbers are customised. If it warns that the tip
   speed is over 300 mph, ask the user to confirm before building.
3. **Where it hits.** Without an `aim`, the strike point is chosen
   automatically on the most exposed face of the target. If the user names a
   spot, set `aim` to a point on the surface (mm, model coordinates) and the
   outward surface normal there.
4. **Confirm the up axis.** `build` infers it as the robot's thinnest
   dimension. State it and ask the user to confirm before the first build: a
   wrong up axis aims a horizontal spinner wrongly. Set it with `up_axis`.
5. **Scope.** Run `scope` and explain in a sentence which neighbouring parts
   are kept, as blocks of their own material that carry the load into the
   chassis, and that the rest is cut away. `include` and `exclude` change it.
6. **Build.** Report the element count and the estimated solve time. If
   `over_budget` is set, offer a coarser `mesh_size` or a shorter `end_time`,
   or the long wait; only pass `--allow-over-budget` if the user accepts.
   Pass on the build's warnings, such as a re-mesh to remove degenerate
   elements.
7. **Solve**, then **results**. Explain whether the part survived, where
   strain concentrated, how much material eroded, and the quality warnings
   (state those before the conclusions). Lead with the answer to the user's
   question. Point them to the wear map and energy plot files.

## How the hit is modelled

Say this plainly when asked.

- **The weapon:** the opponent's weapon is a rigid tooth on a spinning body.
  It carries the weapon's energy as rotation and has the opponent robot's
  mass at its hub. Its arc passes through the strike point.
- **Spin axis:** a horizontal spinner spins about the robot's up axis; a drum
  or vertical spinner spins about a horizontal axis across the line of attack.
- **Glancing hits:** the tooth meets a wall head-on and a sloped wedge at an
  angle, so it can glance off.
- **The default weapon is an estimate:** unless customised, it is a quarter of
  the class weight in steel (a 6:1 bar for horizontal, a solid disc for
  vertical) at a preset RPM.
- **The struck robot is free:** everything not meshed rides on a rigid body
  with its real mass, so the robot gets knocked away.
- **Material breaks off:** parts lose elements when they pass their failure
  strain.
- **Duration:** the run lasts until the hit is over (estimated, 40–500 µs)
  unless `end_time` is set.

Be honest about what this is, once rather than every turn:

- material values are handbook-typical;
- the opponent is an estimated weapon (unless customised), not a specific
  robot;
- the hit is simulated for at most half a millisecond.

It compares designs and finds weak spots; it does not certify a part. When
something goes wrong, see [reference/troubleshooting.md](reference/troubleshooting.md).
