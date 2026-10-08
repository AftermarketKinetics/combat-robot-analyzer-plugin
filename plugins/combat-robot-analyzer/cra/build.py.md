# build.py

## Function
Builds a case (place the weapon, scope, mesh, write the deck, have the
OpenRadioss starter check it, estimate the solve time) and solves it. Ported
from the web app's `build_case` and `run_solve` tools.

## Interface
- `build(case) -> dict` — runs `case_cli.py build`, `build_deck.py`, `run_sim.sh --check`; writes `build/build.json`; returns target, scope, elements, contact time, `estimated_solve_minutes`, `threads`, `over_budget` (only when over), weapon placement, `impactor` card, up axis, tooth, aim grade, warnings, `starter_check: "passed"`. Deletes the previous `build/` first.
- `solve(case, nthreads, allow_over_budget) -> dict` — runs `run_sim.sh build/run/strike_0000.rad -nt N`; refuses over budget without the flag; returns `{threads, log}`.
- `starter_nodal_timestep(path) -> float | None`, `estimate_minutes(elements, end_time, dt, nthreads) -> float`, `threads() -> int` (half the CPUs, 1–8).
- Constants `TIMESTEP_SCALE = 0.9`, `THROUGHPUT_PER_THREAD = 1.28e6`, `SOLVE_BUDGET_MIN = 45`, `BUILD_RECORD = "build/build.json"`, `DECK`.

## Implementation
- The estimate uses the starter's nodal timestep, not the mesh geometry: on graded meshes the geometric estimate was off by orders of magnitude (web app, 2026-10-08). Falls back to the build's `dt_physical` when the starter table is missing.
- Throughput is mint's measured i5-6500 rate; local machines are usually faster, so the estimate errs long.
- Unlike the web app there is no solve queue and no one else to protect, so the 45-minute budget is a prompt to ask the user, not a shared-resource limit.

## Assertions
- [ ] `build` never returns `starter_check: "passed"` unless `run_sim.sh --check` exited 0.
- [ ] `solve` refuses an over-budget build without `allow_over_budget`.
- [ ] A new build deletes the old `build/`, so results never mix two builds.
