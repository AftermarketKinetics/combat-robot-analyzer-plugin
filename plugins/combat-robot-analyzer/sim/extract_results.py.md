# extract_results.py

## Function
Read OpenRadioss VTK frames and summarise per-element/per-node results:
frame-by-frame maxima, spatial distribution, hotspots. CLI and importable
module (used by `report.py`, `extract_damage.py`). Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- CLI: `extract_results.py VTK_DIR [--prefix P] [--part IDS] [-t THRESH…] [-n HOTSPOTS] [--spatial] [--band-size MM] [--axis r|x|y|z] [--parts-list] [--last-only] [--json]`.
- Module: `resolve_damage_field`, `resolve_element_status` and the VTK readers imported by siblings.

## Implementation
- Reads ASCII VTK only (what `anim_to_vtk` writes).

## Assertions
- [ ] Names imported by `extract_damage.py` and `report.py` keep existing.
