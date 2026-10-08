# cadstep.py

## Function

The bridge to the cad-step scripts copied into `sim/cadstep/` — the one place
that knows where they live. Copied from v1 (`cra.cadstep`); v1's `Settings`
dependency is removed and the path is fixed relative to the package.

## Interface

- `SCRIPTS` — `Path` of `sim/cadstep/` (sibling of the `case` package).
- `ensure_on_path() -> Path` — puts `SCRIPTS` on `sys.path` and returns it.
- `load_step(path)` — the parsed `stepcore.StepFile` (text tier, pure stdlib).
- `run_report(step_path, *, skip=(), min_dia=0.0, timeout=600,
  tessellate_dir=None, max_tessellated=0, drawing_hidden="") -> dict` — runs
  `step_report.py STEP --json` as a subprocess and returns the parsed document.
- `CadStepUnavailable` — the scripts are missing (a broken image, not a bad file).
- `ReportFailed` — the analysis ran and did not produce a usable result
  (non-zero exit, unparseable JSON, or timeout).

## Implementation

**The scripts are CLI files, not an installed package**, so they must be on
`sys.path` before anything can import them; everything goes through here.

**`run_report` is a subprocess, not an import**: the OpenCASCADE sections are
slow enough (clash: 129 s of 142 s on inertial-v6) that a hang must be
killable by `timeout`. pythonocc-core is in the sim Python in both the dev
shell and the image, so the script's `occenv.ensure_occ()` is a no-op.

**Without `tessellate_dir` the tessellate section skips itself**: the geometry
is megabytes and does not come back through the pipe; it is written to the
directory under the model's name (`case_cli.py report` renames it
`geometry.json`).

**`drawing_hidden`** passes `--hidden` (`"outline"`/`"full"`) through; nothing
in v2 uses it.

**The two exception types are distinguished on purpose**: a missing toolchain
is a deployment fault, a `ReportFailed` is a finding about the user's file.

## Assertions

- [ ] `SCRIPTS` is the only place a cad-step path is spelled.
- [ ] `run_report` never imports pythonocc into this process.
- [ ] A timeout surfaces as `ReportFailed`, not `subprocess.TimeoutExpired`.
