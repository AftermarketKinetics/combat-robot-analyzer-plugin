#!/usr/bin/env python3
"""Removing information from the join must lose answers, never invent them.

The exact route needs plumbing OpenCASCADE has moved between versions, so it
has to be allowed to fail -- but a build without it must fall back to saying
"ambiguous", not to quietly pairing the wrong standoff with the wrong mass.
This drives the join with each route knocked out in turn and checks that every
answer it still calls trustworthy agrees with the fully-informed one.

    python3 tests/join_degradation.py FILE
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import occenv  # noqa: E402

occenv.ensure_occ()

import occshapes as O   # noqa: E402
import stepcore         # noqa: E402
import stepjoin         # noqa: E402

path = sys.argv[1]
sf = stepcore.load(path)

full_parts = O.load_parts(path, with_colors=False)
full = stepjoin.build_index(sf, full_parts, occ_bbox=O.bbox)
truth = dict((i, e.node) for i, e in
             ((full_parts.index(e.part), e) for e in full.entries
              if e.part is not None))

# the same shapes, loaded by an OCCT build that cannot recover STEP entity ids
blind = O.load_parts(path, with_colors=False, with_entities=False)
assert all(p.entity is None for p in blind), "with_entities=False still resolved"

fails = []


def run(label, disable_transform):
    real = stepjoin._same_transform
    if disable_transform:
        stepjoin._same_transform = lambda *a, **k: False
    try:
        idx = stepjoin.build_index(sf, blind, occ_bbox=O.bbox)
    finally:
        stepjoin._same_transform = real

    wrong = trusted = 0
    for e in idx.entries:
        if e.part is None or not e.trusted:
            continue
        trusted += 1
        want = truth.get(blind.index(e.part))
        if want is not None and e.node is not want:
            wrong += 1
    print("  %-16s %-44s %d trusted" % (label, idx.counts(), trusted))
    if wrong:
        fails.append("%s: %d trusted join(s) disagree with the exact one"
                     % (label, wrong))


print(os.path.basename(path))
run("no entity ids", False)
run("nor transforms", True)
for f in fails:
    print("  FAIL %s" % f)
raise SystemExit(1 if fails else 0)
