# stepjoin.py

## Function
Joins the text tier's assembly occurrences (`stepcore.AssemblyNode`) to the
OCC tier's located shapes (`occshapes.Part`) and gives each occurrence one
opaque id both tiers key on. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- `build_index(sf, parts, occ_bbox=None)` -> `PartIndex`. Called by
  step_report and step_tessellate with `occ_bbox=occshapes.bbox_or_none`.
- `PartIndex`: `entries`, `id_for_part`, `id_for_node`, `id_for_solid`,
  `for_part/for_node/for_solid`, `unjoined` (property), `counts()`.
  step_report uses `entries, id_for_part, id_for_node, unjoined, counts`.
- `Entry`: `id, part, node, joined, name, path`, `trusted` property.
- Ids are `"p%03d"` (`p000`, `p001`, ...): OCC part order first, then
  text-only leftovers. These are the `parts[].id` values in the report.
- `joined` is one of `exact | geometry | name | ambiguous | unmatched`.

## Implementation
- Four passes: (1) STEP entity ids from the transfer, narrowed by NAUO chain;
  (2) identical world transform; (3) bounding-box containment and deviation,
  best wins, near-ties refused as `ambiguous` (only when `occ_bbox` given);
  (4) unique product name, last resort.
- Only `exact` and `geometry` count as trusted; names are not an identity
  (eight identical standoffs share name and path, and the tiers disagree on
  which name to use).
- An unresolvable entity widens the candidate pool to every node ("could be
  anything", not "could be nothing").
- Pass 3 calls `occ_bbox` on each still-unmatched part and skips a part
  whose box is `None`. It runs outside step_report's per-section guard, so
  one bodiless part used to take the whole report down; v2 fix: callers pass
  `bbox_or_none` and pass 3 skips `None`.

## Assertions
- [ ] Ids are stable for a given file and never derived from names.
- [ ] Every occurrence from either tier gets an entry (union, not intersection).
- [ ] `trusted` stays limited to `exact` and `geometry`.
