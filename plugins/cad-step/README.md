# cad-step

CAD STEP file analysis for Claude Code: assembly trees, part placements, hole
and bolt-circle tables, mass properties, clash detection, fastener bills of
materials, SVG technical drawings, entity queries and revision diffs.

## Skills

| skill | answers |
|---|---|
| `step-inspect` | what is this file, what units, what is inside |
| `step-placements` | where is each part, which way does it point |
| `step-features` | holes, bores, counterbores, bolt circles, thicknesses |
| `step-measure` | volume, mass, centre of mass, inertia, clashes |
| `step-fasteners` | screw/nut counts, hardware mass, bolt-circle layout |
| `step-drawing` | SVG orthographic + isometric views with hidden-line removal |
| `step-query` | raw entity graph, revision diff, part extraction |

## Two engine tiers

**Text tier** (`stepcore.py`) is pure standard-library Python. It parses the
ISO 10303-21 exchange structure directly and answers structural questions in
about 0.7 s on a 9 MB, 135 000-entity assembly. Everything except measurement,
clash and drawing runs here.

**Geometry tier** uses `pythonocc-core` (OpenCASCADE) for exact B-rep work.
Scripts that need it call `occenv.ensure_occ()`, which re-execs the process
inside

```
nix-shell -p "python3.withPackages(ps: with ps; [pythonocc-core numpy])"
```

when `OCC` is not already importable. Nothing needs installing first. Set
`STEP_OCC_PYTHON=/path/to/python` to use an interpreter that already has it,
or `STEP_OCC_NO_NIX=1` to fail fast instead of re-execing.

## Bulk endpoint: `step_report.py`

Each skill loads the file itself, which is right when a person is asking one
question at a time. A program wanting all of the answers pays that cost seven
times -- minutes, on a 32 MB assembly. `step_report.py` runs every collector
over **one** `stepcore` parse and **one** OpenCASCADE transfer:

```
python3 scripts/step_report.py FILE --json [--skip drawing,clash]
                                    [--min-dia 0.5] [--max-seconds 120]
```

It has no SKILL.md on purpose: it is machine-facing, and a combined dump reads
worse than the focused tools. The document is

```json
{
  "path": "...", "size_bytes": 9847232, "seconds": 14.2,
  "schema": "AUTOMOTIVE_DESIGN", "units": {"length": ["millimetre", 1.0]},
  "parts": [ {"id": "p003", "joined": "exact", "volume_mm3": 182043.2,
              "closed": true, "bbox": {"min": [...], "max": [...]},
              "has_cylindrical_bore": true, "max_bore_dia_mm": 8.0,
              "min_feature_mm": 0.4, ...} ],
  "bbox": {"min": [...], "max": [...], "size": [...]},
  "timestep_drivers": [ {"part_id": "p012", "part": "gearbox-plate",
                         "kind": "hole", "size_mm": 0.4,
                         "center": [12.0, 3.4, 8.1], "count": 6} ],
  "unjoined_parts": 0,
  "join_methods": {"exact": 43, "geometry": 14},
  "sections": {
    "info":    {"seconds": 0.9, "data": { ... }},
    "drawing": {"seconds": 0.0, "error": "skipped"}
  }
}
```

A section that raises keeps its failure to itself -- an SVG projection that
will not converge costs one `"error"` string, not the whole document. Records
inside each section carry `part_id` (or `a_id`/`b_id`, `part_ids`) alongside
the names they already had. The drawing's SVG comes back inline as a string,
so nothing has to find somewhere writable to put it.

`--validate` adds a full `BRepCheck_Analyzer` pass per part; it is off by
default because it costs two orders of magnitude more than `closed` does
(1.9 s against 0.01 s over the 57 parts of `inertial-v6.step`).

### Smallest features

In an explicit FE solve the stable timestep follows the smallest element, so a
0.4 mm fillet can cost ten times what a part is worth. `timestep_drivers`
lists what would drive it -- smallest first, identical features on a part
collapsed into one row with a count, because six 0.4 mm holes are one decision
and not six. Refusing a job is only useful with that list attached; "your model
is too detailed" is not something anyone can act on.

`kind` is `hole`, `fillet` or `round`. `size_mm` is a hole's **diameter** and a
blend's **radius** -- not the same kind of number, but the two a CAD user
recognises, and both scale the same way against element size. `--driver-max`
sets the cut-off (1 mm by default); `--min-dia` hides small holes from this
list as well as from the hole table, so leave it at zero when sizing a mesh.
An empty list means nothing was that small; `timestep_drivers_error` says so
when it instead means the features pass did not run.

Fillets come from `stepfeatures.blends_of`, which is new and is not wired into
the `step-features` skill. A toroidal face is a blend by construction and its
minor radius is the fillet radius outright. A *convex cylindrical* face is more
equivocal -- it is a rolled edge, or it is a shaft -- so those are only taken
below 1 mm, where nothing is a shaft. Concave blends need no special handling:
they already come out of `holes_of` as very small holes.

Per part, `min_feature_mm` is the smallest of those; `has_cylindrical_bore` and
`max_bore_dia_mm` back the "a weapon part with nowhere to put a shaft is
probably mis-tagged" check. The flag alone cannot tell a shaft bore from a
tapped M2 hole, which is why the diameter is there too. Both read `false`/`null`
when the part genuinely has no holes and `null` when nothing looked.

### What it costs

Wall clock on one laptop core, seconds:

| | `inertial-v6` 9.7 MB, 57 parts | `meowtybrain` 32 MB, 137 parts |
|---|---|---|
| load (parse + XCAF transfer + join) | 6.5 | 43.5 |
| info / placements | 0.6 | 3.4 |
| measure | 3.1 | 9.3 |
| features | 1.0 | 43.9 |
| fasteners | 0.2 | 2.8 |
| drawing | 7.3 | 67.5 |
| clash | 161 | not measured |
| **all but clash** | **18.7** | **170** |

Two things to plan around. `clash` is quadratic in part count and dwarfs
everything else, so skip it unless the answer is the point. And the load is a
floor that no `--skip` or `--max-seconds` can get under -- 43 s of a 32 MB
file is gone before the first section starts, essentially all of it in
OpenCASCADE's XCAF transfer. Under a tight budget on a big file, the
text-tier scripts alone (3.6 s to parse the same file) are the only option.

### Running to a budget

`--max-seconds` is a real deadline, not a check between sections: `clash`
tests it per pair, `drawing` per view and `features` per solid. A section that
runs out returns what it finished with `"incomplete": true` and a count --
`"pairs_tested": 12, "pairs_candidate": 217` -- rather than an error or a
silent truncation. `clash` and `drawing` run last whatever order they are
reported in, so the two expensive passes cannot be the reason a section worth
a fifth of a second never ran.

It can still overshoot by one unit of work, because a boolean or an HLR pass
cannot be interrupted once begun and the same model has pairs ranging from
milliseconds to ten seconds. Both budget against the worst unit seen so far
rather than the average, on the grounds that overrunning a hard timeout loses
the whole run while stopping early loses one pair. Asking `inertial-v6` for
15 s takes 17 s; asking for 40 s takes 40 s.

Because most of it is usually wasted, `clash` sorts its candidate pairs by
how much their bounding boxes share before testing any of them. A solid can
overlap no more than its box does, so that is the best cheap guess at where a
real interference is. With the full budget the result is identical -- every
pair gets tested and the output is sorted by volume either way -- but a run
cut short at 12 of 217 pairs finds a genuine interference where the first 12
in file order find nothing.

## Part identity

Both tiers otherwise identify parts by name, and a name is not an identity.
Eight identical standoffs under one parent share theirs, and the two tiers do
not even agree on which name to use -- `stepcore` takes the PRODUCT name,
XCAF prefers the component's instance name. Joining user-entered data (a
material, a role) against that mis-assigns silently, which is worse than
failing.

`stepjoin.py` gives every occurrence an opaque `id` and says how it got there:

| `joined` | how |
|---|---|
| `exact` | through STEP entity ids recovered from the OCC transfer |
| `geometry` | by world transform, or failing that bounding-box centre |
| `name` | by name alone -- weak, and only used when unambiguous |
| `ambiguous` | several candidates were equally good; nothing was assigned |
| `unmatched` | one tier has this occurrence and the other does not |

Only `exact` and `geometry` are safe to key data against; `unjoined_parts`
counts the rest. The exact route works because `STEPCAFControl_Reader` keeps
its work session, whose transfer reader maps each shape back to the STEP
entity that produced it -- for a component label that is the
`NEXT_ASSEMBLY_USAGE_OCCURRENCE`, the only occurrence-unique key the format
has. `stepcore.AssemblyNode` carries the same ids as `nauo_id` / `nauo_chain`.
XCAF cannot name the NAUO behind every reference (a reference to a
subassembly resolves to a compound that was never a transfer result), so the
chain is matched as a subsequence and the world transform settles the rest.

## Scripts

```
scripts/stepcore.py        standard-library STEP-21 reader (entities, units,
                           frames, assembly tree, solids, colours)
scripts/stepfeatures.py    surfaces -> holes, counterbores, bolt circles
scripts/occenv.py          nix-shell re-exec for pythonocc
scripts/occshapes.py       OpenCASCADE loading and measurement helpers
scripts/stepjoin.py        reconciles the two tiers into stable part ids

scripts/step_info.py       file summary and router
scripts/step_tree.py       product tree
scripts/step_placements.py world transforms, axes, relative frames
scripts/step_features.py   hole tables and bolt circles
scripts/step_bbox.py       bounding boxes (fast, or --exact)
scripts/step_measure.py    volume / mass / COM / inertia
scripts/step_clash.py      interference and clearance
scripts/step_drawing.py    SVG technical drawings
scripts/step_query.py      entity graph queries
scripts/step_diff.py       revision comparison
scripts/step_extract.py    write parts out as standalone STEP
scripts/step_report.py     every collector at once, from one parse
```

Every script takes `--json`. Every collector is split into `collect(path,
...)`, which loads the file, and `collect_from(loaded, ...)`, which takes an
already-parsed `stepcore.StepFile` or a list of `occshapes.Part`. The former
is a thin wrapper over the latter, so the CLIs and their JSON are unchanged;
the latter is what makes one shared parse possible.

## Parser notes

Things that bite naive STEP readers, all handled here:

- **Complex instances** (`#68=( A(...) B(...) C(...) )`) are indexed under
  every subtype. Assembly transforms, unit contexts and rational B-splines all
  live inside them, so a `#\d+=([A-Z_]+)` regex silently misses them.
- **Units come from `GLOBAL_UNIT_ASSIGNED_CONTEXT`**, not from the first
  `SI_UNIT` in the file - models routinely declare several length units and
  only the context says which one the coordinates use.
- Strings are masked before records are split, so a `;` or `#` inside a name
  cannot corrupt the parse. `\X2\...\X0\` escapes are decoded.
- `.T.` / `.F.` are booleans, not enumerations - face orientation depends on
  it, and getting it wrong makes every hole look like a boss.

## Tests

```
STEP_TEST_DIR=/path/to/step/files bash tests/run_tests.sh
```

The suite skips gracefully when the corpus is absent, and skips the
OpenCASCADE cases when neither `OCC` nor `nix-shell` is available.

Two of the cases guard the machinery above and can be run directly:

```
python3 tests/join_integrity.py FILE       # ids unique, no node claimed twice,
                                           # both tiers agree on placement
python3 tests/join_degradation.py FILE     # with the exact route knocked out,
                                           # answers are lost, never invented
python3 tests/section_equivalence.py FILE  # step_report == the standalone
                                           # scripts, modulo the part_id keys
python3 tests/cross_tier.py FILE           # fast bounds contained by exact
```

`join_degradation.py` is the one worth keeping honest. It reruns the join with
the STEP entity ids removed, and then with the transform comparison removed on
top, and fails if any surviving `exact`/`geometry` answer disagrees with the
fully-informed one. Getting that wrong is not a crash, it is a report with the
right mass against the wrong part.
