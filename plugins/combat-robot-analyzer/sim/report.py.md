# report.py

## Function
Assemble one JSON "analysis context" for a finished run — setup, quality
checks, per-frame evolution, hotspots, per-part damage — designed to be handed
to the model to interpret. Copied unchanged from the openradioss-sim plugin (`~/code/impact-simulator-skill`, commit `f8e97bd`); it is a copy, not a submodule, so fixes here must be ported by hand in either direction.

## Interface
- CLI: `report.py BUILD_DIR [-c SPEC] [--prefix P] [--text] [-n HOTSPOTS] [--band-size MM] [--axis …] [-t THRESH…]`.
- Module: `locate_artefacts`, `read_config`, `read_pipeline_json` (used by `wear_figure.py`).
- Depends on `extract_results`.

## Implementation
- Spec auto-detected from the build dir when `-c` is not given.

## Assertions
- [ ] Functions imported by `wear_figure.py` keep existing.
