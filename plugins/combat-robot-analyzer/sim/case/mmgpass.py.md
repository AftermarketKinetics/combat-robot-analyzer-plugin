# mmgpass.py

## Function

The guarded mmg3d post-pass: propose a repaired mesh with `mmg3d_O3
-optim -nosurf -hmin`, accept it only if measurably cheaper.
`v1/docs/history/mesh-sliver-anatomy.md` §7 measured the unguarded pass at
×0.17 median on the slab-sliver class and ×1.17 median corpus-wide — the
lever only exists as propose-measure-accept, the geometry simplifier's
shape: a repair not measured against the original is indistinguishable
from damage.

Copied from v1 (`cra.mmgpass`); imports rewritten to `case.`.

## Interface

- `guarded_pass(groups, cards, *, baseline, end_time, timestep_scale,
  mesh_floor_mm, workdir, case, mmg_bin=MMG_BIN, timeout_s=TIMEOUT_S) ->
  PassResult` — the whole pass. `groups`/`cards` are exactly what
  `loadcase` hands `mesh_metrics.analyse`; `baseline` is that call's
  result. Never raises for mmg trouble: binary missing, crash, timeout,
  unreadable output and unmeasurable proposals all come back as a
  rejection with the reason. On acceptance, `mesh_path` is a sibling
  `<case>-mmg.msh` and `metrics` the replacement — the caller swaps both.
- `PassResult` — `accepted`, `reason` (never empty; `"accepted"` on
  success), `seconds`, `ec_before/after`, `min_before/after_mm`,
  `mesh_path`, `metrics`; `record()` is the JSON-safe subset that
  `build_case` stores as `BuiltCase.mmg`.
- `evaluate_pass(before, after, volumes_before, volumes_after) -> str` —
  the pure acceptance rule; `"accepted"` or the reason.
- `write_medit(groups, path) -> {ref: name}`, `read_medit(path,
  names_by_ref)`, `write_msh22(groups, path)` — the codecs, pure.
- `ACCEPT_MARGIN` (0.98), `MAX_PART_VOLUME_DRIFT` (1e-3), `MMG_BIN`
  (`"mmg3d_O3"`), `TIMEOUT_S` (300.0; not in `__all__`).

Consumed by `loadcase.build_case(mmg_pass=)`, default off. `case_cli.py
build` does not pass it, so in v2 the agent's tools never run the pass; the
binary is available in the sandbox (`pkgs.mmg` in `v2/nix/sim-env.nix`). In
v1 `Settings.mmg_pass` (`CRA_MMG_PASS`) drove it via B4 and
`cra.calibrate.mesh_algo` reached it with the `-mmg` variant suffix.

## Implementation

**The guard is the module.** `evaluate_pass` rejects unless the proposal
is ≥2% cheaper (`ACCEPT_MARGIN` — churn protection, not ambition), keeps
the exact part set, and holds every part's volume within
`MAX_PART_VOLUME_DRIFT`. That last is a tripwire: `-nosurf` was measured
at 2.3e-8 worst-case drift over 35 corpus meshes, so 1e-3 firing means
mmg did something it promised not to.

**Shared nodes are rebuilt from exact float equality.**
`loadcase._tets_by_group` hands over bare coordinates per tet; every copy
of a node came from the same gmsh node, so keying on the tuple restores
the conformal topology mmg needs. This is only safe because nothing in
the chain recomputes a coordinate — an Assertions item below.

**The replacement is MSH 2.2 with `$PhysicalNames`, written by hand.**
`build_deck.load_parts` resolves parts via meshio's `field_data` +
`gmsh:physical`, which is exactly what the writer emits (verified by
reading a real accepted mesh back through meshio). Hand-rolled rather
than via meshio (v1 reasons: so `cra` kept its no-numpy-in-gates property
and the Lambda image stayed untouched).

**The gmsh mesh is never overwritten.** The accepted file is a sibling;
the original stays for diagnosis, and a rejected pass leaves only the two
Medit scratch files behind.

**`-hmin` is the case's real floor** (`mesh_size / MIN_SIZE_FRACTION`),
passed in rather than hard-coded, so a calibration run at a different
mesh size measures what that size would experience.

## Assertions

- A `PassResult` with `accepted=False` never carries `mesh_path` or
  `metrics`, and `guarded_pass` never raises because of mmg itself — the
  pass can make a build slower but never worse.
- `reason` is never empty.
- The medit/MSH writers emit one shared vertex per distinct coordinate
  tuple; they must never round, format-truncate below round-trip
  precision (`%.17g`), or otherwise recompute a coordinate.
- The MSH output declares format `2.2 0 8`, one `$PhysicalNames` entry
  per part, and every element as type 4 with two tags (physical =
  elementary = the part's ref).
- `evaluate_pass` checks the part set before anything else — a cheaper
  price on a mesh that lost a part is not a price.
