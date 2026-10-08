# __main__.py

## Function
The `robot-impact` command line (`python3 -m cra`, reached through
`bin/robot-impact`): one subcommand per step of the analysis, each printing
one JSON document.

## Interface
- `init STEP CASE` (copies the model to `CASE/model.step`, runs `case_cli.py report`, writes default `setup.json` if absent), `show CASE`, `set CASE JSON`, `materials [QUERY]`, `scope CASE`, `build CASE`, `solve CASE [-nt N] [--allow-over-budget]`, `results CASE`.
- Exit 0 with the result, or exit 1 with `{"error": ...}` for a CaseError. Other exceptions are bugs and print a traceback.
- `main(argv=None) -> int`.

## Implementation
- Output is indented JSON for a human reading over Claude's shoulder; it is still one document.
- `show` resolves the weapon each time (a subprocess), so it reflects the current presets.

## Assertions
- [ ] Every subcommand prints exactly one JSON document on stdout.
- [ ] A CaseError never escapes as a traceback.
