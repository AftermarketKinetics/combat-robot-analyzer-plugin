# aim.py

## Function

Answers one question: is the strike actually on the target? In v1 this was the
geometry gate B3.5 graded a confirmed aim with, before the card was authorised.
In v2 nothing in `case` or `case_cli.py` calls `grade_aim` yet: `loadcase` uses
`load_surface` (to snap the automatic prefill onto the material) and
`placement` uses `first_hit`.

Copied from v1 (`cra.aim`); imports rewritten to `case.`.

The defect it exists to stop: across the corpus, 46 of 58 automatically placed
strikes landed more than 1 mm off any material — in the worst cases 170, 129 and
120 mm clear. The tooth travels 3.9 to 4.8 mm in the whole simulation, so those
runs ended with it still in flight: full duration, `NORMAL TERMINATION`, no
contact, no strain energy, clean throughput. Thirty-eight of forty-four solved
cases tested nothing.

## Interface

- `grade_aim(point, direction, tris, *, travel, target_id,
  tolerance=AIM_TOLERANCE_MM, doc=None) -> AimVerdict`
- `AimVerdict` — `status`, `gap_mm`, `travel_mm`, `message`, and `blocker`
  (set only by the shadowed-approach case); `blocks` and `warns` are
  properties over `status`; `BLOCKING` lists the blocking statuses.
- `load_surface(doc, part_id) -> list[Triangle]` — decode the packed geometry.
- `first_hit(...)`, `first_blocker(...)`, `is_inside(...)` — the three tests.
- `travel_mm(v_tip_ms, end_time_s) -> float` — how far the tooth can reach.
- `AIM_TOLERANCE_MM`, `Triangle`.

## Implementation

**Nothing that reasons about bounding boxes can catch this**, which is why it
went unnoticed for so long: Tier 0 (the cad-step report) had no surfaces. It
has them now — the tessellated geometry document `case_cli.py report` writes
as `geometry.json` — so the question can be asked before a solve rather than
inferred afterwards from an energy history nobody was reading.

**Standard library only, deliberately** — no numpy. In v1 a gate that decided
whether someone is charged had to be unit-testable with no wheels at all, the
same argument that kept `mesh_metrics` pure stdlib. v2's sim Python
(`v2/nix/sim-env.nix`) does carry numpy; the module stays stdlib because a
part is a few thousand triangles and there is nothing to buy.

**Four things block** (v1: PLAN.md decision 46) and nothing else: the point is
inside the material, the ray never meets the part, the gap exceeds the tooth's
travel, or another part shadows it.

**`AIM_TOLERANCE_MM` is measured, not chosen** — 0.0791 mm worst case over ten
load cases across five models, by v1's `cra.calibrate.aim_tolerance` (not
copied to v2), which brute-force-checks its own nearest-surface search before
measuring anything because it once reported a 1.5 mm disagreement that was
entirely its own off-by-one. The constant is 0.25 mm, about three times that
worst case.

## Assertions

- **A strike off the material blocks.** This is the whole point.
  `tests/test_aim.py`.
- **The decode path is the real one** — `tests/test_aim.py` builds geometry
  with its own `_doc` helper, which quantises onto the same 16-bit grid over a
  shared `lo`/`span` frame and base64s exactly as `step_tessellate` packs it,
  the grid the browser's clicks have already been through. (v1 used
  `conftest.make_viewer_geometry`, which was not copied.)
- **Known contradiction:** `Split Mk 1 v41` lc2 made an ordinary impact
  (9.5e-3 strain fraction) on an approach this module says cannot reach the
  part. Three of the four testable refusals were confirmed to twenty orders of
  magnitude; this one disagrees and is uninvestigated. (v1:
  `v1/docs/history/STATE-OF-THE-CODEBASE.md` §4.8 — where it was the gate
  that refused jobs before payment.)
