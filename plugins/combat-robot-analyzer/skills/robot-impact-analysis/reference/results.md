# Results reference

`robot-impact results CASE` writes `CASE/build/results/` and prints the same
summary it saves there as `summary.json`. Units are the deck's mm-Mg-s:
stress in MPa, energy in mJ (1.1e6 mJ is 1,100 J), length in mm.

| Field | Meaning |
|---|---|
| `termination` | `normal_termination`, `cycles` and `end_time` reached. A run that stopped well short of its end time did not simulate the hit, whatever the termination says. |
| `energy` | the energy balance from the solver's cycle table. Look at `NaN` first: any NaN means the run blew up and nothing in it can be trusted. |
| `quality_warnings` | state these before any conclusion. An energy-balance note about eroded elements is expected with erosion; added mass or a large drift without erosion is not. |
| `hit.still_absorbing` | true when the target's internal energy was still growing at the end. The damage is then a lower bound; offer a longer `end_time`. `absorbed_fraction_of_initial_energy` is how much of the weapon's energy the robot took. |
| `overall` | element count, eroded elements, peak plastic strain and von Mises stress over elements that survived. |
| `per_part` | the same per part (`p035` is the target; `fill_<id>` are the neighbour stand-ins; `tooth_head` is the weapon, which is rigid). `eroded_fraction` and `mass_eroded_g` are material lost. `damage_clusters` counts separate damaged regions. |
| `target_wear` | the struck part's wear statistics. |
| `files` | `energy.png` (energy history), `history.csv`, `wear.svg` (where the target lost material and strained), `playback.json/.bin` (the web app's animation format; unavailable for meshes that are not all tetrahedra). |

Lead with the answer to the user's question: did the part survive, where did
it fail or nearly fail, and how much material did it lose. Then give the
supporting numbers with units and part names.

The raw solver frames stay in `CASE/build/run/vtk`, so post-processing can be
re-run. They are large (about 1 GB for an 800k-element run); offer to delete
them once the user has their results.
