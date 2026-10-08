# _common.py

## Function
Shared CLI plumbing for the `step_*.py` scripts: the argparse skeleton,
JSON-or-text output, text tables, number formatting and name filtering.
Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- Import side effect: puts this directory (`HERE`) on `sys.path`, so a
  script that imports `_common` can then import its siblings by bare name.
- `base_parser(description, multi=False)` -- positional `file` (or `files`
  with `multi=True`) plus `--json`.
- `emit(data, as_json, text_fn)` -- `--json` writes `json.dump(indent=2)` to
  stdout; tuples become lists, anything else unserialisable becomes `str()`.
- `expired(deadline)` -- wall-clock budget test, `None` never expires.
- `match(name, patterns, default=True)` -- case-insensitive substring or
  fnmatch; empty `patterns` returns `default`.
- Text helpers: `table`, `heading`, `human_size`, `fmt_num`, `fmt_xyz`.
- Imported as `C` by step_clash, step_drawing, step_fasteners, step_features,
  step_info, step_measure, step_placements, step_report. Not by
  step_tessellate (its own argparse) or the library modules.

## Implementation
- `expired` is meant to be checked inside the loop that spends the time, not
  between whole collectors: per its docstring, a pass that starts one second
  inside the budget can otherwise return minutes outside it.

## Assertions
- [ ] `emit` with `as_json=True` writes only the JSON document to stdout;
      `case/cadstep.py` parses the whole of `step_report.py`'s stdout.
- [ ] Importing `_common` keeps putting its own directory on `sys.path`.
- [ ] `match(..., patterns=[])` returns `default` (every collector relies
      on "no --part means everything").
