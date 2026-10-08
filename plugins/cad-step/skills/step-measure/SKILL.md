---
name: step-measure
description: Exact geometric measurement of STEP models with OpenCASCADE - volume, surface area, centre of mass, moments of inertia, mass from material densities, plus part-to-part interference and clearance checking. Use when the user asks how heavy a part or assembly is, what its volume or centre of gravity is, whether parts collide or interfere, or how much clearance there is between components.
---

# Mass properties and clash detection

These scripts need real B-rep geometry, so they use pythonocc-core. **You do
not need to set anything up**: if `OCC` is not importable the script re-execs
itself inside `nix-shell -p "python3.withPackages(ps: with ps; [pythonocc-core
numpy])"` automatically. Just run them with plain `python3`. First run prints
`[occ] entering nix-shell...` and takes a few extra seconds.

## Mass properties

```bash
# volumes only
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_measure.py FILE.step

# one density everywhere (g/cm3 or a material name)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_measure.py FILE.step --density aluminium

# per-part materials; each rule is matched against the part name first and
# only then against its assembly path, so the first rule that names the part
# wins over one that merely matches a parent subassembly
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_measure.py FILE.step \
    --density aluminium --material 'shell=abs' --material 'lipo*=lipo' \
    --material '*motor*=steel'

# add principal moments of inertia
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_measure.py PART.step --density 7.85 --inertia
```

Known material names include aluminium, al7075, steel, stainless, titanium,
brass, copper, tungsten, abs, pla, petg, nylon, delrin, ptfe, uhmw, g10,
carbon-fiber, lipo. Any number is taken as g/cm^3.

Output is sorted heaviest-first and ends with total volume, total mass and the
mass-weighted centroid. Parts left without a density are excluded from the
mass total and the count is reported - say so rather than quoting a total as
complete.

## Interference and clearance

```bash
# real boolean intersection volume between every part pair
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_clash.py ASM.step

# also list pairs that miss each other by less than 0.5 mm
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_clash.py ASM.step --clearance 0.5

# narrow it down when an assembly has many parts
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_clash.py ASM.step --part rail --part motor
```

Bounding boxes reject disjoint pairs first, then `BRepAlgoAPI_Common` measures
the real overlap volume and where it is centred. Interpret small overlaps with
care: press fits, threads modelled as plain cylinders and deliberately
interfering seals all show up as clashes.

## Related skills

`step-bbox`/`step-inspect` for extents, `step-features` for the hole a
fastener goes into, `step-drawing` to show the clash region visually.
