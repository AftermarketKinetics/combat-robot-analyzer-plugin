---
name: step-features
description: Extract machining features from a STEP file - hole tables with diameters, depths, through vs blind, counterbores, countersinks, bolt circles, shaft/boss diameters, plate thicknesses, and matching fastener sizes (M3 clearance, tap drill, bearing bores). Use when the user asks about holes, hole patterns, bolt patterns, bolt circles, screw or fastener sizes, bores, counterbores, thread sizes, or plate/wall thickness in a CAD file.
---

# Holes, bolt circles and other machining features

A hole is not an entity in STEP - it is a set of cylindrical faces that share
an axis. `step_features.py` does that grouping, works out the axial extent
from the faces' own vertices, decides hole-vs-boss from face orientation, then
looks for counterbores, countersinks and circular patterns.

## Commands

```bash
# hole table for a part, in the part's own frame
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_features.py PART.step

# assembly: positions in assembly coordinates, one part at a time
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_features.py ASM.step --part wep-plate --world

# include external cylinders (shafts, pins, bosses) and plate thicknesses
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_features.py PART.step --bosses --planes

# only fastener-sized holes
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_features.py PART.step --max-dia 8
```

## Reading the output

- `dia` / `depth` are in file units (check `step_info.py` first).
- `type` is `through` when the bore exits material at both ends; `blind` means
  a flat bottom or drill point was found capping it.
- `notes` carries counterbore/countersink geometry and a fastener guess -
  the guess is a nominal-size match (within 0.12 mm), so treat "M3 close
  clearance" as a strong hint, not as a thread callout read from the file.
  STEP does not record threads; a tapped hole appears as its tap-drill bore.
- **Bolt circles** section: coplanar, equal-diameter, parallel holes fitted to
  a circle, with the bolt-circle diameter, centre, and angular spacing.
- **Parallel plane stacks**: the distances between parallel faces along each
  normal - that is where plate thickness and rail wall thickness come from.

## Caveats worth stating to the user

- Counterbore detection pairs a larger coaxial bore that does not overlap the
  smaller one axially; a deep recess concentric with a bore reports the same
  way as a machined counterbore.
- `--world` transforms hole positions through the assembly tree, so a hole in
  a mirrored instance reports its mirrored position.

## Related skills

`step-placements` (part positions), `step-measure` (clash between a fastener
and the hole it sits in), `step-drawing` (`--dims` annotates extents).
