---
name: step-placements
description: Find where each part of a STEP assembly sits and which way it points - world coordinates, axis directions, rotations, and distances between parts. Use when the user asks where a part is, how far apart two parts are, which direction a hole/shaft/motor axis points, what a part's coordinate frame is, or wants coordinates expressed relative to another part.
---

# Part placement and coordinate frames

Assembly transforms in STEP are buried in complex instances
(`REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION` inside a parenthesised
record) reached through `NEXT_ASSEMBLY_USAGE_OCCURRENCE` ->
`PRODUCT_DEFINITION_SHAPE` -> `CONTEXT_DEPENDENT_SHAPE_REPRESENTATION`.
`step_placements.py` walks that chain and accumulates world transforms; do not
try to re-derive it with grep.

## Commands

```bash
# every instance: world origin, axis directions, rotation
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_placements.py FILE.step

# just the parts you care about (substring or glob, repeatable)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_placements.py FILE.step --part motor --part rail

# leaf instances only, skipping subassembly nodes
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_placements.py FILE.step --leaves

# re-express everything in one part's frame - this is how you measure
# "how far along the rail is the motor" without doing vector algebra by hand
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_placements.py FILE.step --relative-to wep-rails
```

## Reading the output

| column | meaning |
|---|---|
| `origin` | instance origin in assembly (or `--relative-to`) coordinates |
| `+Z` / `+X` | where the part's own Z and X axes point after placement |
| `rotation` | axis/angle form of the placement rotation |

Axis columns print as `+Z`, `-Y` etc. when they land on a principal axis, and
as a unit vector otherwise. A part showing `origin (0,0,0)` with no rotation
was exported already positioned in assembly coordinates - that is normal for
parts modelled in place, not a bug.

## Answering distance questions

Run with `--relative-to PART` and read the other part's origin directly: the
components are the along-axis distances in that part's frame. For extents
rather than origins use `step_bbox.py --exact`.

## Related skills

`step-inspect` (what the file contains), `step-features` (hole positions,
which can also be reported in world coordinates with `--world`),
`step-measure` (centre of mass, clash checks).
