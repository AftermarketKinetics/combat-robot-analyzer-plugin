# results.py

## Function
Post-processes a finished solve into the summary Claude explains and the files
the user opens: energy plot, history CSV, per-part damage, the target's wear
map and the impact playback. Ported from the web app's `get_results` tool.

## Interface
- `results(case) -> dict` — writes `build/results/` (`energy.png`, `history.csv`, `damage.npz`, `wear.svg`, `playback.json/.bin`, `summary.json`) and returns the summary: `units`, `termination`, `energy`, `mass`, `quality_warnings`, `hit`, `overall`, `per_part`, `target_wear`, `files`, and `playback_unavailable` when the animation could not be made.
- `deck_parts(case) -> {name: number}`, `hit_progress(csv) -> dict`, `quality(warnings, damage) -> list[str]`, `RESULTS = "build/results"`.

## Implementation
- Raises CaseError without a finished solve (`build/run/strike_0001.out`) or a build record.
- `hit_progress`: still absorbing when internal energy grew > 5 % over the last tenth of the run.
- `quality` replaces the energy-drift warning with an explanation when elements eroded: deleted elements take their energy with them.
- Unlike the web app it keeps the raw VTK frames, so the post-processing can be re-run; the skill tells Claude to offer deleting them.
- The wear map title may contain spaces here (the web app's broker allowlist forbade them).
- Playback needs an all-tetrahedral mesh; otherwise its error is reported and the rest of the results still stand.

## Assertions
- [ ] A playback failure never fails `results`.
- [ ] `summary.json` is exactly what `results` returns.
