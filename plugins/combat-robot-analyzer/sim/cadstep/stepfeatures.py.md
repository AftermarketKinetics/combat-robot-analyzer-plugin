# stepfeatures.py

## Function
Turns raw B-rep surfaces from `stepcore` into machining features: holes,
bosses, counterbores, countersinks, bolt circles, plane stacks and blends.
Pure text tier (no OCC). Copied unchanged from the cad-step plugin (via v1's vendored copy); a copy, not a submodule, so fixes must be ported by hand.

## Interface
- Imported only by step_features (as `F`), which uses `holes_of(sf, solid,
  include_bosses=False)`, `blends_of(sf, solid, round_max=1.0)`,
  `bolt_circles(records, min_count=3, tol=1e-3)`, `plane_stack(sf, solid,
  tol=1e-4)`.
- Also public: `cylinders_of`, `cones_of`, `solid_points`,
  `FASTENER_TABLE`, `BEARING_BORES`, `AXIS_TOL`, `RADIUS_TOL`.
- Depends on `stepcore.Vec` and on `sf._cache`.

## Implementation
- A hole is not a STEP entity: coaxial cylindrical faces are grouped, the
  axial extent comes from the faces' own vertices, and hole vs boss comes
  from face orientation.
- `_memo` caches on `sf._cache`, not a module dict keyed by `id(sf)`: a
  collected StepFile's address can be reused by the next one and read stale
  geometry back.
- `blends_of`: toroidal faces are blends at any size (minor radius = fillet
  radius); convex cylinders count only up to `round_max`, since a small one
  is a rolled edge rather than a shaft. Concave blends are left out on
  purpose: `holes_of` already reports them as very small holes. Blends feed the timestep-driver list
  in step_report.

## Assertions
- [ ] No OCC import; runs on `stepcore` alone.
- [ ] The four functions step_features calls keep their names and record keys
      (`diameter, kind, center, start, end, axis` on holes; `radius, kind,
      center` on blends) -- step_report's rollups read them.
