---
name: step-query
description: Inspect the raw ISO 10303-21 entity graph of a STEP file, diff two revisions of a model, or extract parts into a standalone STEP file. Use when the user asks about specific STEP entity ids or types, wants to know what changed between two versions of a CAD file, or wants a subassembly pulled out into its own file.
---

# Entity queries, revision diffs, and extraction

## Query the entity graph

```bash
# one entity, references expanded two levels deep
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_query.py FILE.step --id 133 --resolve 2

# every entity of a type
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_query.py FILE.step --type CYLINDRICAL_SURFACE --limit 100

# who points at this entity / what does it point at
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_query.py FILE.step --refs-to 742
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_query.py FILE.step --refs-from 742

# regex over the raw records
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_query.py FILE.step --grep 'Body[0-9]'
```

References are resolved inline instead of printed as bare `#123`, and
geometric entities get a plain-language note (`cylinder D=8.0, axis +Z through
(-11, 14.25, 2.25)`). Reach for this when a higher-level tool reports
something surprising and you need to see the underlying records.

## Diff two revisions

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_diff.py OLD.step NEW.step
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_diff.py OLD.step NEW.step --no-holes   # faster
```

Instances are matched by their path through the assembly tree, so a part that
only moved is reported as **moved** (with delta and distance) rather than as a
removal plus an addition. Geometry changes report face-count deltas and which
hole diameters appeared or disappeared.

## Extract parts

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_extract.py ASM.step --list
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_extract.py ASM.step --part wep-plate -o plate.step
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/step_extract.py ASM.step --part motor --local -o motor.step
```

Default keeps assembly placement; `--local` moves the first match back to its
own origin. Extraction uses OpenCASCADE (auto nix-shell).

## Related skills

`step-inspect` to orient yourself first, `step-placements` and
`step-features` for the interpreted views of the same data.
