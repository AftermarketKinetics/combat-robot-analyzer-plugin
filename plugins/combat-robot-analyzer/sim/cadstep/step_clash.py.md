# step_clash.py

## Function
Interference and clearance checking between assembly parts: bounding boxes
reject disjoint pairs, then a real OCC boolean measures the overlap volume.
Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_clash.py FILE [--json] [--part PAT]... [--clearance MM]
  [--min-volume MM3=1e-3] [--max-pairs N=20000]`.
- `collect(path, ...)` (calls `occenv.ensure_occ()`); `collect_from(loaded,
  parts, min_volume, clearance, max_pairs, gap, path, id_of, deadline)` --
  used by step_report's `clash` section.
- Output: `path, parts, pairs_tested, pairs_total, clashes[], clearances[],
  clearance_limit`; with a deadline also `pairs_candidate, incomplete`.
  Pairs carry `a_id/b_id` when `id_of` is given.
- v2 skips this section on upload (`case_cli.SKIP_ON_UPLOAD`): 129 s of
  142 s on a real bot, and no load case reads it. `case_cli.py report
  --clash` turns it back on.

## Implementation
- Candidates are sorted by shared bounding-box volume, largest first, so a
  budget-cut pass has tested the likeliest interferences.
- A boolean cannot be interrupted, so before each pair it checks
  `now + worst-pair-so-far > deadline` rather than the clock alone.
- More than `max_pairs` total pairs exits (`SystemExit`) before any boolean.
- Bodiless occurrences (`bbox_or_none` is `None`) are dropped; a failing
  boolean is recorded as a clash with `error` and the pass continues.

## Assertions
- [ ] `collect_from` stops starting pairs once the deadline would be overrun
      and marks `incomplete`.
- [ ] Output without a deadline has no `incomplete`/`pairs_candidate` keys.
