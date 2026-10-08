# Troubleshooting

| Symptom | Cause and what to do |
|---|---|
| `build` reports meshing crashed (signal 6 or 11) | gmsh crashes on some CAD when it fuses the stand-in blocks. Retry with `{"standins": false}` to mesh the target alone, and tell the user that load paths into the rest of the robot are then lost. |
| `build` refuses with "degenerate (flat) elements" | The mesh still had sliver elements after four re-meshes. Try a smaller `mesh_size`, or `exclude` the part named. Never solve such a mesh: slivers erode at almost no load, their neighbours invert, and the solve goes to NaN within microseconds while still reporting normal termination. |
| `build` warns "re-meshed to remove degenerate elements" | Expected on thin walls; the build refined the mesh where slivers appeared. It costs more elements and solve time; nothing to fix. |
| `build` reports over_budget | Offer a coarser `mesh_size` (2–2.5 mm), a shorter fixed `end_time`, or a smaller scope (`exclude`). Solve with `--allow-over-budget` only if the user accepts the wait. The estimate assumes an older desktop CPU; a modern machine is often faster. |
| "aim rejected", or "this face is parallel to the weapon's plane of rotation" | The strike point is not on the target, or the weapon cannot reach that face (e.g. the top of the robot for a horizontal spinner; a face pointing straight up or down for a drum). Pick another point or weapon type. |
| `results`: NaN energies, or the run ended far short of `end_time` | The run blew up. Look at `CASE/build/run/strike_0001.out` for the first `DELETE SOLID ELEMENT` and `NEGATIVE` lines, and check which part those elements belong to in `strike_0000.rad`. Do not report damage from such a run. |
| `results`: `hit.still_absorbing` | The hit was not over when the run stopped. The damage is a lower bound; offer a longer `end_time`, up to 5e-4 s. |
| The first command takes minutes | The nix environment is being built and the solver downloaded, once. |

If a run looks broken and none of the above explains it, tell the user so
plainly rather than drawing conclusions from it.
