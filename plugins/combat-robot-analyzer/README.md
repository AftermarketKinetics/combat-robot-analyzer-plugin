# combat-robot-analyzer

Find out what an opponent's spinning weapon does to your robot. Claude
analyses your STEP file, settles the setup with you (struck part, materials,
weapon), meshes the region around the strike, solves the hit with
OpenRadioss and explains where the robot strained and lost material.

The skill is `robot-impact-analysis`; ask Claude something like "what does a
12 lb drum do to this wedge?" with a STEP file in the working directory.

## What is modelled

- The opponent's weapon is a rigid tooth on a spinning body that carries the opponent robot's mass. By default it is a quarter of the class weight in steel (a bar for horizontal spinners, a disc for verticals and drums) at a preset RPM; every number can be customised.
- Your robot is free: the region around the strike is meshed, and everything else rides on a rigid body with its real mass.
- Material erodes past its failure strain; the run lasts until the hit is over (up to 500 µs).
- Material values are handbook-typical. It compares designs and finds weak spots; it does not certify a part.

## Layout

| Path | Contents |
|---|---|
| `bin/robot-impact` | the entry point; enters the nix-shell |
| `cra/` | the case workflow (setup, build, solve, results), ported from the web app's service |
| `sim/` | the meshing, deck and post-processing code, copied from the web app's `v2/sim` (2026-10-08, commit 8ed7d9d) |
| `nix/`, `shell.nix` | the pinned toolchain |
| `skills/robot-impact-analysis/` | the skill and its references |
| `fixtures/projectile_plate.step` | a small part for checking the pipeline |

`sim/` is a copy, not a link: changes made in the web app's repo need copying
across, and the other way round.

## Check it works

```
robot-impact test
robot-impact init fixtures/projectile_plate.step /tmp/plate
robot-impact set /tmp/plate '{"target_id":"p000","default_material":"aluminium_6061_t6","impactor":{"weight_class":"1lb","archetype":"horizontal"}}'
robot-impact build /tmp/plate && robot-impact solve /tmp/plate && robot-impact results /tmp/plate
```

On the fixture that builds 4.5k elements, solves 500 µs in about 10 s, and
erodes about 430 elements. The impact playback is unavailable for that mesh,
which is not all tetrahedra.
