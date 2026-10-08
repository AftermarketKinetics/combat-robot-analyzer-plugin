#!/usr/bin/env python3
"""Check the fast vertex-based bounds against exact OpenCASCADE bounds.

The text tier measures vertices only, so its box must always be contained by
the true B-rep box (a curved face can bulge outside its own vertices, never
the other way round).  A violation means the assembly transforms are wrong.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import occenv  # noqa: E402

occenv.ensure_occ()

import occshapes as O   # noqa: E402
import stepcore         # noqa: E402

path = sys.argv[1]
tol = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05

sf = stepcore.load(path)
fast = sf.world_bounds()
if fast is None:
    print("no vertices; nothing to compare")
    raise SystemExit(0)

lo, hi = None, None
for p in O.load_parts(path):
    a, b = O.bbox(p.shape)
    if lo is None:
        lo, hi = list(a), list(b)
    else:
        for i in range(3):
            lo[i] = min(lo[i], a[i])
            hi[i] = max(hi[i], b[i])

print("fast  %s .. %s" % (fast[0].fmt(3), fast[1].fmt(3)))
print("exact (%.3f, %.3f, %.3f) .. (%.3f, %.3f, %.3f)"
      % (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))

bad = []
for i, ax in enumerate("XYZ"):
    if fast[0][i] < lo[i] - tol:
        bad.append("fast min %s %.4f < exact %.4f" % (ax, fast[0][i], lo[i]))
    if fast[1][i] > hi[i] + tol:
        bad.append("fast max %s %.4f > exact %.4f" % (ax, fast[1][i], hi[i]))
if bad:
    for b in bad:
        print("MISMATCH:", b)
    raise SystemExit(1)
print("cross-tier ok (fast box contained within exact box, tol %.3f mm)" % tol)
