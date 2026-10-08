# case_dir.py

## Function
A case directory: one robot, its setup and everything built from it, laid out
like a web-app session (`model.step`, `report/`, `setup.json`, `build/`) so
the sim scripts' relative paths work unchanged. Holds the setup defaults, the
"what is still missing" check, and the one helper every command uses to run a
sim script.

## Interface
- `PLUGIN`, `SIM`, `LIBRARY` — the plugin root, its `sim/`, `sim/materials.yaml`.
- `DEFAULTS` — setup.json defaults (the web app's `setup_file.DEFAULTS`).
- `RESOLVED = "setup.resolved.json"` — the setup with `default_material` expanded, which `case_cli.py scope|build` read.
- `CaseError` — a failure to show the user as is; `__main__` prints it as `{"error": ...}`.
- `load(case) -> dict`, `save(case, setup)`, `report(case) -> dict | None`.
- `resolved(case, setup) -> dict` — every part given a material.
- `missing(case, setup) -> list[str]` — what a build still needs.
- `write_resolved(case)` — writes RESOLVED; raises CaseError listing what is missing.
- `sim(case, script, *args, timeout=1800) -> Any` — runs `sim/<script>` with cwd = case and `sim/` on PYTHONPATH. Python scripts: returns their JSON; `.sh` scripts: returns `{stdout, stderr}`. Raises CaseError with the script's own `error`, its exit code and stderr tail, "crashed (signal N)", or a timeout.

## Implementation
- Mirrors the web app's `service/analyzer/setup_file.py` so a case built here and a session built there resolve identically.
- `_json` accepts the whole of stdout first (wear_figure indents its JSON) and falls back to the last line (scripts that print progress first).
- A negative return code is a signal: gmsh/OCC segfaults show up as signal 11 or 6, which the skill tells Claude to answer with `standins: false`.

## Assertions
- [ ] `DEFAULTS` stays in step with the web app's setup_file.DEFAULTS (same keys, same defaults).
- [ ] `sim` always runs with cwd = the case directory; the scripts' paths are relative to it.
- [ ] A script's own `{"error": ...}` reaches the user unchanged.
