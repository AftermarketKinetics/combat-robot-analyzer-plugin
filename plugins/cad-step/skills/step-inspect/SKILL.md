---
name: step-inspect
description: Summarise a STEP/STP CAD file - units, schema, assembly vs single part, product tree, solid and surface counts, colours, bounding box. Use when the user points at a .step/.stp/.STEP file and asks what it is, what is in it, how big it is, what units it uses, or which parts an assembly contains; also the first thing to run before any other STEP analysis.
---

# Inspecting a STEP file

Never hand-write a regex parser for STEP files. These scripts already parse
the exchange structure correctly, including complex instances and unit
contexts, and they run in well under a second on multi-megabyte assemblies.

Scripts live in `${CLAUDE_PLUGIN_ROOT}/scripts/`.

## Start here

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_info.py FILE.step
```

Reports provenance (exporting CAD system, timestamp), **units** (never assume
millimetres - the sample corpus contains an INCH file), whether the file is an
assembly or a single part, product/instance/solid counts, surface-type
breakdown, colour palette with material style names, and an approximate
bounding box.

## Then

```bash
# assembly structure
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_tree.py FILE.step [--depth N] [--world] [--solids]

# bounding boxes per part
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_bbox.py FILE.step [--part NAME] [--exact]
```

`step_tree.py` prints the product tree with instance labels, solid counts and
appearance. `--world` adds each instance's origin in assembly coordinates.

`step_bbox.py` is vertex-based by default (fast, but the silhouette of a
curved face can fall slightly outside the reported box). `--exact` switches to
OpenCASCADE for true B-rep extents and is worth it whenever a number will be
used for a fit or a clearance.

## Reading the output

- `units.assumed: true` in `--json` means the file never declared a unit -
  say so rather than quoting millimetres as fact.
- `kind: single part` still usually shows one instance and depth 1 - CAD
  exporters wrap a lone body in a one-instance assembly, so the count alone
  does not tell you whether there is a real assembly to explore.
- Every script takes `--json` for machine-readable output.

## Related skills

`step-placements` (where parts sit), `step-features` (holes and bolt
circles), `step-measure` (volume/mass/clash), `step-drawing` (SVG views),
`step-query` (raw entity graph).
