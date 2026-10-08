# wear_figure.py

## Function
Draw a self-contained SVG wear/damage map of one part: peak plastic strain and
eroded elements on the part's own plane, zoom panels per erosion cluster, a
through-thickness section and a legend. Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- CLI: `wear_figure.py BUILD|DAMAGE.npz -p PART [-c SPEC] [-o OUT.svg] [--axis auto|x|y|z] [--title T] [--annotate SPEC…] [--spin-rpm R] [--max-zooms N] [--eps-erosion E] [--density D] [--thickness T] [--embed] [--stats-only] [--prefix P] [-q]`.
- Default output `<build>/<name>_wear.svg`.
- Depends on `extract_damage` and `report`.

## Implementation
- Erosion threshold, density and part name come from the spec unless overridden.

## Assertions
- [ ] Output SVG has no external references (embeddable in the web UI).
