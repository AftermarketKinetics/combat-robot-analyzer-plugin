# extract_damage.py

## Function
Per-element damage history across every VTK frame: peak plastic strain and von
Mises stress while alive, and erosion frame/time; cached as `.npz` so the
wear figure redraws in a second.

Copied from the openradioss-sim plugin (`~/code/impact-simulator-skill`,
commit `f8e97bd`); it is a copy, not a submodule, so fixes must be ported by
hand in either direction. **v2 has changed it** (2026-10-08, for rigid
bodies; the plugin is still at `f8e97bd` without it). The whole change, for a
port back:
- `track` computes `rigid` — elements already inactive (`present` but not
  alive) in the first frame — never marks those as eroded, and saves `rigid`
  in the `.npz`.
- `summarise` reports `rigid_elements` and computes `eroded_fraction` over
  deformable material only (rigid elements weigh 0); it reads `rigid` only
  when the `.npz` has it, so older caches still summarise.

## Interface
- CLI: `extract_damage.py VTK_DIR [-p IDS] [--prefix P] [-o OUT.npz] [-q]`;
  default output `<build>/<name>_damage.npz`.
- `.npz` arrays: `centroid, part, element_id, peak_eps, peak_vm, peak_damage,
  erode_frame, erode_time, volume, area, size, cell_type, erosion_tracked,
  rigid, times`.
- Stdout: JSON summary `{npz, frames, elements, eroded_elements,
  rigid_elements, parts, peak_plastic_strain_alive, peak_von_mises_alive,
  total_volume, total_area, element_size_median, erosion_tracked,
  eroded_fraction?, first_erosion_time?, last_erosion_time?,
  strain_at_deletion_median?, end_time?}`; progress on stderr.
- Depends on `extract_results` (same directory).

## Implementation
- Centroids/volumes from the first (undeformed) frame, so maps show where material was lost from the part.
- **Elements inactive at the first frame are rigid, not eroded.** OpenRadioss
  reports elements inside a `/RBODY` as status 0 from t = 0. Before this was
  handled, a rigid tooth read as 100 % eroded and a rigidified armour slab as
  5,700 eroded elements. They are flagged `rigid` and never counted. With the
  fix, the inertial-v6 run (12 lb horizontal into a 4130 shell) reads 1,282
  shell elements eroded.
- `eroded_fraction` is of deformable material: rigid elements' volume (or
  area) is zeroed in the measure. `total_volume`, `total_area` and `elements`
  still include rigid elements; the stderr progress line's denominator does too.
- The first frame is taken to be t = 0 (the engine deck's `/ANIM/DT` starts at
  0), so nothing can have eroded before it.

## Assertions
- [ ] Peaks only count frames where the element is still alive.
- [ ] An element inactive in the first frame is `rigid` and never has an
      `erode_frame`; `eroded_elements` and `eroded_fraction` exclude it.
      `sim/tests/test_deck_cards.py::test_elements_dead_at_the_first_frame_are_rigid_not_eroded`.
