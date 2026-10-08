# step_drawing.py

## Function
Orthographic SVG drawings of a STEP model via OCC hidden-line removal --
visible edges solid, optional hidden edges dashed. Default sheet: top, iso,
front, right at one shared scale. Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- CLI: `step_drawing.py FILE [--json] [-o OUT.svg] [--view NAME]... [--part
  PAT]... [--hidden [full|outline]] [--color] [--no-smooth] [--page PX=1600]
  [--deflection MM] [--dims]`. Writes the SVG (default `<stem>.svg` in the
  cwd); stdout is a summary.
- `VIEWS`: `front, back, top, bottom, right, left, iso, iso-rear`;
  `DEFAULT_SHEET`.
- `run(path, out_path, ...)` (calls `occenv.ensure_occ()`, writes a file);
  `render(loaded, ..., color_source, id_of, deadline)` -> `(svg, info)`,
  writes nothing -- used by step_report's `drawing` section.
- With `id_of`, every path carries `data-part`; each view is a
  `<g class="view">` carrying its projection.
- v2 skips this section on upload; `run_report`'s `drawing_hidden` only
  matters when it is not skipped.

## Implementation
- `hidden="outline"` keeps only each hidden part's silhouette; `"full"`
  draws every hidden edge (docstring: 137,410 vs 8,313 visible polylines on a
  56-part bot). Outlines also make buried parts addressable (47 of 56 parts
  drawn vs 13).
- Deadline is checked between views with a look-ahead of the average view
  time; the first view is always drawn.
- Colours fall back to the text tier (`_colors_by_name`) because OCC often
  loses STEP styling; it accepts a path or an already-parsed `StepFile`.
- Default deflection is `max(size)/800`, floor 1e-3 mm.

## Assertions
- [ ] `render` never writes to disk.
- [ ] `info["views"]` lists only views actually drawn; `incomplete` set
      when a deadline cut the sheet short.
