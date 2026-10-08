# placement.py

## Function

Decides where the opponent strikes from, and which part it strikes.

The user tags parts by role; nothing in the file says which *direction* an
opponent comes in. This picks one deterministically from Tier 0 bounding boxes
alone, so a job is reproducible from its inputs and the choice can be printed in
the report for the user to disagree with.

**It is a prefill, not the decision** (v1: PLAN.md decision 35). Graded
against real surfaces the automatic choice is refused in 44 of 58 corpus cases;
snapped onto the material along the direction it already chose, that falls to
6. Its error was distance, not direction. In v2 `loadcase._approach` always
runs `choose_approach`, then overwrites it with SETUP.json's `aim` when one is
given, or snaps it onto the surface when none is.

Copied from v1 (`cra.placement`); imports rewritten to `case.`.

## Interface

- `select_target(parts, candidates, *, tooth_width, standoff, exclude_span=False)
  -> tuple[str, Approach]` — which part this case strikes, with the approach
  that won it the pick. No v2 caller: the target comes from SETUP.json's
  `target_id`.
- `choose_approach(parts, target_ids, *, tooth_width, standoff,
  exclude_span=False) -> Approach` — direction, point, standoff.
- `approach_candidates(...)`, `bbox_corners(...)` — the sweep.
- `snap_to_surface(approach, tris) -> Approach` — move the point onto real
  material along the direction already chosen; the caller pre-selects the
  target's triangles.
- `Approach` — direction, strike_point, standoff, reach, exposure, arbitrary,
  part_id, solid_index. `exposure` is what `select_target` ranks by;
  `arbitrary` is what `describe()` calls out in the report.
- `PlacementError`.

## Implementation

**The rule:** sweep candidate directions in the plane perpendicular to the bot's
shortest axis, discard any direction with something in the way, and take the one
the target reaches furthest along.

**Blocking is a corridor test, not a reach comparison.** A part blocks only if
it reaches past the target's own surface *and* its footprint overlaps the
target's in the plane perpendicular to the strike. Demanding only the former
would let a part on the far side of the bot veto a clear approach.

**`solid_index` is why the Tier 0 part rows carry per-body boxes** (v1: B3's
projection). Without it a
mirrored pair's two halves are indistinguishable and the load case is scoped to
whichever body happens to be first.

**A `None` entry in `solid_bboxes` is a body Tier 0 could not box, and its
position still counts.** The list is one entry per solid, gaps included, so a
position in it names the same solid as the same position in `solid_volumes`
and in the order a STEP import returns. The body is skipped for scoring — it
has no box to score — but the surviving bodies keep their own indices rather
than being renumbered by enumerating what survived. Renumbering would hand
`loadcase` an index that means a different solid than the one scored here, and
the mesh would be taken from that one. A part with no boxable body at all
falls back to the whole-part box with `solid_index` `None`, which
`_struck_solid` refuses for a multi-body target rather than meshing a guess.

**Snapping returns the approach unchanged when the ray misses the part
entirely.** Five corpus cases are like that; quietly relocating the strike would
hide a case that needs a human to pick a different approach.

At 622 lines it exceeds the guidance; target selection and approach choice share
the geometry helpers and the same candidate sweep. Not split.

## Assertions

- **Deterministic from Tier 0 boxes alone** — same inputs, same approach, so a
  job is reproducible. `tests/test_placement.py`.
- **Snapping never changes the direction**, only the distance along it.
- **A ray that misses the part is returned unchanged**, not relocated. This is
  what lets the aim check (`aim.grade_aim`; v1: gate B3.5) refuse it rather
  than silently striking something else.
- The tolerance below which a gap is not the user's fault was *measured*, not
  chosen: 0.0791 mm worst case over ten load cases across five models (v1:
  `cra.calibrate.aim_tolerance`); `aim.AIM_TOLERANCE_MM` is 0.25 mm.
