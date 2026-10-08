---
name: step-fasteners
description: Fastener bill of materials from a STEP assembly - screw, nut and washer counts grouped by size, per-piece and total hardware mass, and where each group sits (bolt-circle radius, levels along an axis, angular spacing). Use when the user asks how many screws or bolts an assembly uses, what the fastener hardware weighs, for a fastener BOM or hardware list, or where the M8s / tooth bolts / stack bolts are.
---

# Fastener BOM and hardware mass

STEP files carry no thread data - a bolt is just a stepped cylinder. So this
skill reads the *names* the CAD library gave the parts (`M8x70 FHCS`,
`90695A033_Medium-Strength Steel Thin-Profile Hex Nut`), groups identical
fasteners, and measures every instance's real volume with OpenCASCADE for
mass. Names come from the XCAF product tree, so anything the exporter left
unnamed cannot be classified.

No setup needed: the script re-execs itself into `nix-shell` when pythonocc is
missing (first run prints `[occ] entering nix-shell...`).

## Commands

```bash
# hardware bill of materials, steel assumed unless the name says otherwise
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step

# include springs, bearings, seals, magnets and retaining rings
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step --hardware

# names this CAD library uses that the classifier does not know
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step \
    --pattern '*oilite*' --pattern 'DIN912*'

# per-group materials, and every instance's coordinates
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step \
    --material '*standoff*=aluminium' --material '*washer*=nylon' --positions

# bolt circles about the spin axis of a horizontal spinner lying in XY
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step --axis z

# one subassembly only
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_fasteners.py ASM.step --part drivetrain
```

Material names and `--density` are the same set as `step-measure`
(aluminium, steel, stainless, titanium, nylon, ...); any number is g/cm^3.

## Reading the output

- **fastener table**: designation, quantity, per-piece mass, group mass,
  volume, and the material with where the density came from - `name` means it
  was read out of the part name ("Alloy Steel", "18-8 Stainless", nylon),
  `(default)` means steel was assumed, `(--density)`/`(--material)` mean you
  set it.
- **Layout table**: radius of the group from the chosen axis, the distinct
  levels along it, and `spacing` - filled in only when three or more pieces
  sit on one circle at equal angles, which is exactly the bolt-circle case.
  Levels are how you spot a sandwich stack: one designation at two z levels
  is a bolt through both plates.
- `--positions` prints every instance centre for hand-checking.

## How names are classified, and where it fails

- Sizes are read as `M8x70`, `M8`, `#6-32`, `1/4"-20`; head styles from
  SHCS/BHCS/FHCS, socket/button/flat head, shoulder, set screw.
- Instance and revision decoration (`v2`, `<3>`, `:1`, `(Mirror)`) is stripped
  before grouping, so copies land in one row.
- A keyword only counts when it is the noun the name *ends* on. That is what
  keeps `battery-cage-and-bearing-mount`, `spring-loaded` and
  `thrust-bearing-spacer` out of the hardware list - they are structure, not
  hardware. The cost is that a genuinely oddly-named fastener is missed; the
  footer reports how many parts were ignored, and `--pattern` forces them in.
- A bare size with no keyword (`- M10 Steel`) is reported as `M10 fastener`,
  not as a screw - the name does not say which.
- Quantity is the number of instances actually placed in the file. Models
  where the designer dropped one representative screw report 1; say the count
  is what the model contains, not what the build needs.
- Mass is real B-rep volume x density, so a screw modelled with plain
  cylindrical threads reads slightly heavy or light versus a catalogue mass.

## Related skills

`step-features` for the holes the fasteners go into (clearance vs tap drill,
bolt-circle diameters from the geometry rather than from the parts),
`step-measure` for whole-assembly mass and for clash between a bolt and its
hole, `step-placements` for full frames of the parts a fastener joins.
