---
name: step-drawing
description: Produce SVG orthographic technical drawings of a STEP model using hidden-line removal - front/top/right/isometric views on one sheet, optional dashed hidden lines, per-part STEP colours, and dimension annotation. Use when the user asks to draw, render, visualise, sketch, or see a CAD part or assembly, or wants a drawing/blueprint/views of a STEP file.
---

# SVG technical drawings

`step_drawing.py` runs OpenCASCADE hidden-line removal (`HLRBRep_PolyAlgo`)
per view and writes vector SVG - visible edges solid, hidden edges dashed.
This is a real projection, not a shaded render. pythonocc is fetched
automatically via nix-shell on first use.

## Commands

```bash
# default sheet: top, iso, front, right at one shared scale
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_drawing.py PART.step -o part.svg

# a single view
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_drawing.py PART.step --view iso -o iso.svg

# assembly, coloured per part, with overall extents in the title block
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_drawing.py ASM.step --color --dims -o asm.svg

# hidden lines, one part isolated
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_drawing.py ASM.step --part wep-plate --hidden -o plate.svg
```

Views: `front back top bottom left right iso iso-rear`. Repeat `--view` to
choose the sheet contents and order.

## After writing the file

Show it to the user - the SVG is the deliverable:

```
SendUserFile({files: ["part.svg"], display: "render", status: "normal"})
```

## Options that matter

| flag | when to use |
|---|---|
| `--hidden` | interior detail matters; on a full assembly this adds tens of thousands of dashed segments and gets noisy |
| `--no-smooth` | curvy organic parts where tangent edges clutter the outline |
| `--color` | assemblies, so parts are distinguishable; colours come from the STEP styling |
| `--deflection` | default is size/800; lower for tighter curves, higher for smaller files |
| `--page` | SVG width in px (default 1600) |

All views share one scale so they can be compared; each view is centred in its
own cell. The title block carries the file name, model size, part count and a
scale bar.

## Related skills

`step-features` for the numbers to quote alongside a drawing, `step-measure`
for mass, `step-extract` (in `step-query`) to isolate a subassembly first.
